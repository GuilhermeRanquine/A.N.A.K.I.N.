"""Motor cirúrgico de métricas e análise analítica de chamados (Pandas + SciPy).

Processa os chamados armazenados no cache local DuckDB e extrai indicadores
operacionais, tabelas cruzadas, detecção estatística de outliers e fila de risco.
"""

from typing import Any
import json
import re
import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from scipy import stats


class AnalyticsParams(BaseModel):
    """Parâmetros flexíveis para filtragem e recorte analítico."""

    apenas_status: list[str] | None = None
    apenas_prioridades: list[str] | None = None
    cliente_filtro: str | None = None
    tecnico_filtro: str | None = None
    top_n: int = Field(default=8, ge=1, le=50)


def normalizar_sla_texto(val: Any) -> str:
    """Extrai e normaliza a categoria do SLA de solução para categorias canônicas."""
    if val is None or pd.isna(val):
        return "Sem SLA"

    texto = str(val).strip()
    if not texto or texto.lower() in ("-", "none", "nan", "null"):
        return "Sem SLA"

    # Se for JSON serializado no DuckDB (ex: {"status": "Em conformidade", ...})
    if texto.startswith("{") and texto.endswith("}"):
        try:
            d = json.loads(texto)
            if isinstance(d, dict):
                texto = str(d.get("status") or d.get("descricao") or "")
        except Exception:
            pass

    texto_lower = texto.lower()
    if "conformidade" in texto_lower or "dentro do prazo" in texto_lower or "normal" in texto_lower:
        return "Em conformidade"
    elif "prestes" in texto_lower or "atenção" in texto_lower or "atencao" in texto_lower or "alerta" in texto_lower:
        return "Prestes a estourar"
    elif "estourado" in texto_lower or "atrasado" in texto_lower or "vencido" in texto_lower or "violado" in texto_lower:
        return "Estourado"

    return texto


def normalizar_prioridade_texto(val: Any) -> str:
    """Padroniza prioridades com suporte a variações de acentuação."""
    if val is None or pd.isna(val):
        return "Não definida"

    texto = str(val).strip()
    if not texto or texto in ("-", "none", "nan", "null"):
        return "Não definida"

    # Normaliza variações comuns de acentuação / maiúsculas
    texto_norm = re.sub(r"[^\w\s]", "", texto).capitalize()
    if "crit" in texto.lower():
        return "Crítico"
    elif "alt" in texto.lower():
        return "Alto"
    elif "med" in texto.lower():
        return "Médio"
    elif "baix" in texto.lower():
        return "Baixo"
    elif "plan" in texto.lower():
        return "Planejado"

    return texto_norm


class TicketAnalyticsEngine:
    """Motor analítico de alta performance para chamados com Pandas e SciPy."""

    def __init__(self, df: pd.DataFrame, params: AnalyticsParams | None = None) -> None:
        self.params = params or AnalyticsParams()
        self.df_bruto = df.copy()
        self.df = self._tratar_e_filtrar(self.df_bruto, self.params)

    def _tratar_e_filtrar(self, df: pd.DataFrame, params: AnalyticsParams) -> pd.DataFrame:
        """Padroniza colunas nulas e aplica filtros vetorizados do AnalyticsParams."""
        if df.empty:
            return df

        df = df.copy()

        # Garante colunas essenciais
        colunas_esperadas = [
            "id", "codigo", "cliente", "contato", "assunto", "descricao",
            "status", "prioridade", "tecnico", "mesa_trabalho", "categoria_primaria",
            "categoria_secundaria", "data_criacao", "status_sla_solucao"
        ]
        for col in colunas_esperadas:
            if col not in df.columns:
                df[col] = None

        # Padronização de valores nulos e strings vazias
        df["prioridade"] = df["prioridade"].apply(normalizar_prioridade_texto)
        df["status_sla_solucao"] = df["status_sla_solucao"].apply(normalizar_sla_texto)
        df["tecnico"] = df["tecnico"].fillna("Sem técnico").replace({"": "Sem técnico", "-": "Sem técnico", "nan": "Sem técnico"})
        df["cliente"] = df["cliente"].fillna("Não identificado").replace({"": "Não identificado", "-": "Não identificado", "nan": "Não identificado"})
        df["status"] = df["status"].fillna("Não definido").replace({"": "Não definido", "-": "Não definido", "nan": "Não definido"})

        # Filtro de status
        if params.apenas_status:
            df = df[df["status"].isin(params.apenas_status)]

        # Filtro de prioridades
        if params.apenas_prioridades:
            df = df[df["prioridade"].isin(params.apenas_prioridades)]

        # Filtro de cliente (case-insensitive substring)
        if params.cliente_filtro:
            df = df[df["cliente"].str.contains(params.cliente_filtro, case=False, na=False)]

        # Filtro de técnico (case-insensitive substring)
        if params.tecnico_filtro:
            df = df[df["tecnico"].str.contains(params.tecnico_filtro, case=False, na=False)]

        return df

    def metricas_sla(self) -> pd.DataFrame:
        """Calcula volume e percentual da saúde do SLA de solução."""
        if self.df.empty:
            return pd.DataFrame(columns=["status_sla", "quantidade", "percentual"])

        contagem = self.df["status_sla_solucao"].value_counts().reset_index()
        contagem.columns = ["status_sla", "quantidade"]
        total = contagem["quantidade"].sum()
        contagem["percentual"] = (contagem["quantidade"] / total * 100).round(1)

        # Ordenação canônica preferencial
        ordem = ["Em conformidade", "Prestes a estourar", "Estourado", "Sem SLA"]
        contagem["ordem_cat"] = contagem["status_sla"].apply(lambda x: ordem.index(x) if x in ordem else 99)
        contagem = contagem.sort_values(by=["ordem_cat", "quantidade"], ascending=[True, False]).drop(columns=["ordem_cat"])
        return contagem.reset_index(drop=True)

    def crosstab_status_prioridade(self) -> pd.DataFrame:
        """Gera tabela cruzada de Status vs Prioridade."""
        if self.df.empty:
            return pd.DataFrame()

        ct = pd.crosstab(self.df["status"], self.df["prioridade"])
        # Garante colunas de prioridades principais
        colunas_ordem = ["Crítico", "Alto", "Médio", "Baixo", "Planejado", "Não definida"]
        existentes = [c for c in colunas_ordem if c in ct.columns]
        outros = [c for c in ct.columns if c not in colunas_ordem]
        ct = ct[existentes + outros]
        return ct

    def ranking_tecnicos(self, top_n: int | None = None) -> pd.DataFrame:
        """Gera o ranking de técnicos por volume de chamados e chamados com SLA em risco."""
        n = top_n or self.params.top_n
        if self.df.empty:
            return pd.DataFrame(columns=["tecnico", "total_chamados", "em_risco", "percentual_risco"])

        # Identifica chamados em risco de SLA
        df_temp = self.df.copy()
        df_temp["is_risco"] = df_temp["status_sla_solucao"].isin(["Prestes a estourar", "Estourado"])

        grp = df_temp.groupby("tecnico").agg(
            total_chamados=("codigo", "count"),
            em_risco=("is_risco", "sum")
        ).reset_index()

        grp["percentual_risco"] = ((grp["em_risco"] / grp["total_chamados"]) * 100).round(1)
        grp = grp.sort_values(by=["total_chamados", "em_risco"], ascending=[False, False]).head(n)
        return grp.reset_index(drop=True)

    def ranking_clientes_outliers(self, top_n: int | None = None) -> pd.DataFrame:
        """Gera o ranking de clientes com identificação estatística de outliers via Z-Score (SciPy).

        Sinaliza anomalias de demanda com z >= 1.8.
        """
        n = top_n or self.params.top_n
        if self.df.empty:
            return pd.DataFrame(columns=["cliente", "total_chamados", "z_score", "is_outlier"])

        grp = self.df.groupby("cliente")["codigo"].count().reset_index()
        grp.columns = ["cliente", "total_chamados"]

        contagens = grp["total_chamados"].values.astype(float)
        # SciPy zscore tratando desvio padrão zero ou poucas observações
        if len(contagens) > 1 and np.std(contagens) > 0:
            z_scores = stats.zscore(contagens, nan_policy="omit")
            z_scores = np.nan_to_num(z_scores, nan=0.0)
        else:
            z_scores = np.zeros(len(contagens))

        grp["z_score"] = np.round(z_scores, 2)
        grp["is_outlier"] = grp["z_score"] >= 1.8

        grp = grp.sort_values(by=["total_chamados", "z_score"], ascending=[False, False]).head(n)
        return grp.reset_index(drop=True)

    def fila_critica_risco_imediato(self) -> pd.DataFrame:
        """Extrai chamados que exigem atenção imediata.

        Critério: Prioridade é Crítico ou Alto E SLA de solução é Prestes a estourar ou Estourado.
        """
        if self.df.empty:
            return pd.DataFrame()

        mascara_prioridade = self.df["prioridade"].isin(["Crítico", "Alto"])
        mascara_sla = self.df["status_sla_solucao"].isin(["Prestes a estourar", "Estourado"])

        fila = self.df[mascara_prioridade & mascara_sla].copy()

        colunas_relevantes = [
            "codigo", "cliente", "assunto", "prioridade", "status",
            "tecnico", "status_sla_solucao", "data_criacao"
        ]
        colunas_finais = [c for c in colunas_relevantes if c in fila.columns]

        ordem_prio = {"Crítico": 1, "Alto": 2}
        ordem_sla = {"Estourado": 1, "Prestes a estourar": 2}

        fila["peso_prio"] = fila["prioridade"].map(lambda x: ordem_prio.get(x, 9))
        fila["peso_sla"] = fila["status_sla_solucao"].map(lambda x: ordem_sla.get(x, 9))

        fila = fila.sort_values(by=["peso_prio", "peso_sla", "data_criacao"], ascending=[True, True, True])
        return fila[colunas_finais].reset_index(drop=True)
