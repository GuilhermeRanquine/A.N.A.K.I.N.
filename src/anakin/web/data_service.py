"""Serviço de Dados Analíticos e Leitura do DuckDB (Somente Leitura).

Respeita 100% o contrato de Somente Leitura (read_only=True).
Integra os modelos de métricas cirúrgicas, pool de despacho e consultas paginadas.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Optional
import json
import math
import re
import duckdb
import pandas as pd

from anakin.analytics.surgical_metrics import (
    AnalyticsParams,
    TicketAnalyticsEngine,
    normalizar_prioridade_texto,
    normalizar_sla_texto,
)
from anakin.config.settings import Settings, get_settings
from anakin.dispatch.auto_dispatcher import (
    AutoDispatcher,
    OPERADORES_ELEGIVEIS,
    POOL_SENIOR_N2_EMAILS,
    POOL_PLENO_N1_EMAILS,
    eh_chamado_sem_tecnico,
)


def calcular_tempo_aberto(data_criacao_str: Optional[str]) -> str:
    """Calcula a duração desde a abertura do chamado em formato legível (ex: '2d 5h', '3h 12m', '45m')."""
    if not data_criacao_str:
        return "-"
    data_limpa = str(data_criacao_str).strip()
    dt: Optional[datetime] = None

    formatos = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%Y-%m-%dT%H:%M:%S",
    ]
    for fmt in formatos:
        try:
            dt = datetime.strptime(data_limpa[:19], fmt)
            break
        except Exception:
            continue

    if not dt:
        return data_limpa

    delta = datetime.now() - dt
    total_segundos = max(0, int(delta.total_seconds()))

    dias = total_segundos // 86400
    horas = (total_segundos % 86400) // 3600
    minutos = (total_segundos % 3600) // 60

    if dias > 0:
        return f"{dias}d {horas}h"
    if horas > 0:
        return f"{horas}h {minutos}m"
    return f"{minutos}m"


class AnakinDataService:
    """Provedor de dados para o HUD Sci-Fi com conexão estritamente Read-Only."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.db_path = Path(self.settings.duckdb_path)

    def _obter_conexao(self) -> duckdb.DuckDBPyConnection:
        """Abre conexão com o DuckDB estritamente em modo Somente Leitura (read_only=True)."""
        if not self.db_path.exists():
            raise FileNotFoundError(f"Banco de dados local não encontrado em: {self.db_path}")
        import time
        for tentativa in range(5):
            try:
                return duckdb.connect(str(self.db_path), read_only=True)
            except duckdb.ConnectionException as e:
                if tentativa < 4 and "different configuration" in str(e):
                    time.sleep(0.03)
                    continue
                raise

    def obter_status_geral(self, dias_janela: int = 30) -> dict[str, Any]:
        """Calcula o panorama geral da aplicação, contadores e integridade da rede na janela de no máximo 30 dias."""
        where_30d = f"COALESCE(TRY_CAST(data_criacao AS TIMESTAMP), atualizado_em) >= CURRENT_TIMESTAMP - INTERVAL {dias_janela} DAY"
        with self._obter_conexao() as con:
            total_tickets = con.execute(f"SELECT COUNT(*) FROM chamados WHERE {where_30d}").fetchone()[0]

            # Contadores por status na janela de 30 dias
            rows_status = con.execute(
                f"SELECT LOWER(TRIM(status)), COUNT(*) FROM chamados WHERE {where_30d} GROUP BY 1"
            ).fetchall()
            status_map: dict[str, int] = {}
            for st, cnt in rows_status:
                if st:
                    status_map[st] = cnt

            atendendo = status_map.get("atendendo", 0) + status_map.get("em atendimento", 0)
            a_fazer = status_map.get("a fazer", 0)
            pausado = status_map.get("pausado", 0)
            finalizado = (
                status_map.get("finalizado", 0)
                + status_map.get("resolvido", 0)
                + status_map.get("fechado", 0)
            )
            total_abertos = total_tickets - finalizado

            # Chamados sem técnico atribuído na janela de 30 dias
            sem_tecnico_row = con.execute(
                f"""
                SELECT COUNT(*) FROM chamados
                WHERE LOWER(status) NOT IN ('finalizado', 'resolvido', 'fechado', 'cancelado')
                  AND {where_30d}
                  AND (
                    tecnico IS NULL
                    OR TRIM(tecnico) = ''
                    OR LOWER(tecnico) IN ('-', 'none', 'null', 'não atribuído', 'nao atribuido', 'sem técnico', 'sem tecnico')
                  )
                """
            ).fetchone()
            sem_tecnico_count = sem_tecnico_row[0] if sem_tecnico_row else 0

            # Finalizados hoje
            hoje_str = datetime.now().strftime("%Y-%m-%d")
            finalizados_hoje_row = con.execute(
                """
                SELECT COUNT(*) FROM chamados
                WHERE LOWER(status) IN ('finalizado', 'resolvido', 'fechado')
                  AND (
                    data_solucao LIKE ?
                    OR data_modificacao LIKE ?
                    OR atualizado_em::VARCHAR LIKE ?
                  )
                """,
                [f"{hoje_str}%", f"{hoje_str}%", f"{hoje_str}%"],
            ).fetchone()
            finalizados_hoje = finalizados_hoje_row[0] if finalizados_hoje_row else 0

            # Métricas de SLA na janela de 30 dias
            rows_sla = con.execute(
                f"""
                SELECT status_sla_solucao, COUNT(*)
                FROM chamados
                WHERE LOWER(status) NOT IN ('finalizado', 'resolvido', 'fechado', 'cancelado')
                  AND {where_30d}
                GROUP BY 1
                """
            ).fetchall()
            sla_stats = {
                "conformidade": 0,
                "prestes_a_estourar": 0,
                "estourado": 0,
                "sem_sla": 0,
            }
            for sla_raw, count in rows_sla:
                sla_norm = normalizar_sla_texto(sla_raw)
                if sla_norm == "Em conformidade":
                    sla_stats["conformidade"] += count
                elif sla_norm == "Prestes a estourar":
                    sla_stats["prestes_a_estourar"] += count
                elif sla_norm == "Estourado":
                    sla_stats["estourado"] += count
                else:
                    sla_stats["sem_sla"] += count

            total_com_sla = sla_stats["conformidade"] + sla_stats["prestes_a_estourar"] + sla_stats["estourado"]
            taxa_conformidade = (
                round((sla_stats["conformidade"] / total_com_sla) * 100, 1)
                if total_com_sla > 0
                else 100.0
            )

        return {
            "timestamp": datetime.now().isoformat(),
            "relogio_local": datetime.now().strftime("%H:%M:%S"),
            "data_local": datetime.now().strftime("%d/%m/%Y"),
            "read_only_ativo": True,
            "milvus": {
                "online": True,
                "read_only": self.settings.milvus_read_only,
                "base_url": self.settings.milvus_base_url,
                "rate_limit_segundos": self.settings.milvus_rate_limit_seconds,
            },
            "duckdb": {
                "path": str(self.db_path),
                "total_registros": total_tickets,
            },
            "kpis": {
                "total_tickets": total_tickets,
                "total_abertos": total_abertos,
                "atendendo": atendendo,
                "a_fazer": a_fazer,
                "pausado": pausado,
                "finalizado": finalizado,
                "finalizados_hoje": finalizados_hoje,
                "sem_tecnico": sem_tecnico_count,
                "sla_conformidade": sla_stats["conformidade"],
                "sla_prestes": sla_stats["prestes_a_estourar"],
                "sla_estourado": sla_stats["estourado"],
                "sla_sem": sla_stats["sem_sla"],
                "taxa_conformidade_pct": taxa_conformidade,
            },
        }

    def obter_tickets(
        self,
        page: int = 1,
        page_size: int = 15,
        apenas_abertos: bool = True,
        status_filtro: Optional[str] = None,
        prioridade_filtro: Optional[str] = None,
        tecnico_filtro: Optional[str] = None,
        busca: Optional[str] = None,
        dias_janela: int = 30,
    ) -> dict[str, Any]:
        """Consulta paginada e filtrada de chamados ordenada dos mais recentes para os mais antigos (janela de 30 dias)."""
        page = max(1, page)
        page_size = max(1, min(100, page_size))
        offset = (page - 1) * page_size

        condicoes: list[str] = []
        params: list[Any] = []

        # Janela de no máximo 30 dias para evitar chamados obsoletos
        if dias_janela > 0:
            condicoes.append(f"COALESCE(TRY_CAST(data_criacao AS TIMESTAMP), atualizado_em) >= CURRENT_TIMESTAMP - INTERVAL {dias_janela} DAY")

        if apenas_abertos:
            condicoes.append("LOWER(status) NOT IN ('finalizado', 'resolvido', 'fechado', 'cancelado')")

        if status_filtro:
            condicoes.append("LOWER(status) = ?")
            params.append(status_filtro.strip().lower())

        if prioridade_filtro:
            condicoes.append("LOWER(prioridade) LIKE ?")
            params.append(f"%{prioridade_filtro.strip().lower()}%")

        if tecnico_filtro:
            condicoes.append("LOWER(tecnico) LIKE ?")
            params.append(f"%{tecnico_filtro.strip().lower()}%")

        if busca:
            termo = f"%{busca.strip().lower()}%"
            condicoes.append(
                "(LOWER(codigo) LIKE ? OR LOWER(cliente) LIKE ? OR LOWER(assunto) LIKE ? OR LOWER(tecnico) LIKE ?)"
            )
            params.extend([termo, termo, termo, termo])

        where_clause = " WHERE " + " AND ".join(condicoes) if condicoes else ""

        # Ordenação por criticidade:
        # 1. Prioridade: Crítico=1, Alto=2, Médio=3, Baixo=4, Planejado=5, Outros=6
        # 2. SLA: Estourado=1, Prestes=2, Conformidade=3, Sem SLA=4
        # 3. Status: Atendendo=1, A fazer=2, Pausado=3
        # 4. Data Criação: Mais antigos primeiro
        # Ordenação cronológica estrita: mais recentes para os mais antigos
        order_clause = """
            ORDER BY
                data_criacao DESC,
                codigo DESC
        """

        count_query = f"SELECT COUNT(*) FROM chamados{where_clause}"
        data_query = f"""
            SELECT
                id,
                codigo,
                cliente,
                assunto,
                status,
                prioridade,
                tecnico,
                email_tecnico,
                mesa_trabalho,
                categoria_primaria,
                categoria_secundaria,
                data_criacao,
                status_sla_resposta,
                status_sla_solucao
            FROM chamados
            {where_clause}
            {order_clause}
            LIMIT ? OFFSET ?
        """

        with self._obter_conexao() as con:
            total_registros = con.execute(count_query, params).fetchone()[0]
            cursor = con.execute(data_query, params + [page_size, offset])
            colunas = [c[0] for c in cursor.description]
            rows = cursor.fetchall()

        itens: list[dict[str, Any]] = []
        for r in rows:
            d = dict(zip(colunas, r))
            d["prioridade_norm"] = normalizar_prioridade_texto(d.get("prioridade"))
            d["sla_solucao_norm"] = normalizar_sla_texto(d.get("status_sla_solucao"))
            d["tempo_aberto"] = calcular_tempo_aberto(d.get("data_criacao"))
            itens.append(d)

        total_paginas = max(1, math.ceil(total_registros / page_size)) if total_registros > 0 else 1

        return {
            "page": page,
            "page_size": page_size,
            "total_registros": total_registros,
            "total_paginas": total_paginas,
            "itens": itens,
        }

    def obter_pool_atendimento(self) -> dict[str, Any]:
        """Calcula o status do Pool de Atendimento N1/N2, presença online e scores de carga ponderada."""
        dispatcher = AutoDispatcher(settings=self.settings, read_only=True)
        scores = dispatcher.calcular_scores_pool()
        status_online_map = dispatcher.obter_status_operadores()

        # Próximos candidatos por nível (considerando exclusivamente operadores ONLINE)
        try:
            op_n1, _, _, _ = dispatcher.selecionar_operador_por_prioridade(prioridade="Médio")
            proximo_n1 = op_n1
        except Exception:
            proximo_n1 = None

        try:
            op_n2, _, _, _ = dispatcher.selecionar_operador_por_prioridade(prioridade="Crítico")
            proximo_n2 = op_n2
        except Exception:
            proximo_n2 = None

        operadores_info: list[dict[str, Any]] = []
        for op in OPERADORES_ELEGIVEIS:
            sc = scores.get(op)
            if not sc:
                continue

            nivel = "Sênior / N2" if op.email.lower() in POOL_SENIOR_N2_EMAILS else "Pleno / N1"
            is_online = status_online_map.get(op.email.lower(), True)
            eh_proximo_n1 = is_online and proximo_n1 is not None and op.email == proximo_n1.email
            eh_proximo_n2 = is_online and proximo_n2 is not None and op.email == proximo_n2.email

            operadores_info.append({
                "nome": op.nome,
                "email": op.email,
                "nivel": nivel,
                "online": is_online,
                "score_total": sc.score_total,
                "total_abertos": sc.total_chamados_abertos,
                "atendendo": sc.qtd_atendendo,
                "a_fazer": sc.qtd_a_fazer,
                "pausado": sc.qtd_pausado,
                "outros": sc.qtd_outros,
                "detalhe": sc.detalhe_formatado,
                "eh_proximo_n1": eh_proximo_n1,
                "eh_proximo_n2": eh_proximo_n2,
            })

        # Ordenação inteligente: operadores ONLINE primeiro (por menor score), depois OFFLINE
        operadores_info.sort(key=lambda x: (not x["online"], x["score_total"], x["total_abertos"]))

        total_online = sum(1 for o in operadores_info if o["online"])
        total_operadores = len(operadores_info)

        return {
            "pesos": {
                "atendendo": self.settings.peso_status_atendendo,
                "a_fazer": self.settings.peso_status_a_fazer,
                "pausado": self.settings.peso_status_pausado,
            },
            "total_online": total_online,
            "total_operadores": total_operadores,
            "proximo_n1": proximo_n1.nome if proximo_n1 else None,
            "proximo_n2": proximo_n2.nome if proximo_n2 else None,
            "operadores": operadores_info,
        }

    def obter_metricas_cirurgicas(self, dias_janela: int = 30) -> dict[str, Any]:
        """Calcula estatísticas de volumetria, SLA, categorias e outliers na janela de 30 dias."""
        where_30d = f"COALESCE(TRY_CAST(data_criacao AS TIMESTAMP), atualizado_em) >= CURRENT_TIMESTAMP - INTERVAL {dias_janela} DAY"
        with self._obter_conexao() as con:
            df = con.execute(f"SELECT * FROM chamados WHERE {where_30d}").df()

        if df.empty:
            return {
                "prioridades": [],
                "categorias": [],
                "sla": [],
                "outliers_clientes": [],
                "ranking_tecnicos": [],
            }

        engine = TicketAnalyticsEngine(df)

        # 1. Distribuição de prioridades (apenas abertos)
        df_abertos = df[~df["status"].astype(str).str.lower().isin(["finalizado", "resolvido", "fechado", "cancelado"])].copy()
        df_abertos["prioridade_norm"] = df_abertos["prioridade"].apply(normalizar_prioridade_texto)
        prio_counts = df_abertos["prioridade_norm"].value_counts().reset_index()
        prio_counts.columns = ["prioridade", "quantidade"]
        total_prio = prio_counts["quantidade"].sum()
        prio_counts["percentual"] = (
            (prio_counts["quantidade"] / total_prio * 100).round(1) if total_prio > 0 else 0
        )
        prioridades_lista = prio_counts.to_dict(orient="records")

        # 2. Distribuição por categorias primárias (top 7)
        cat_counts = (
            df_abertos["categoria_primaria"]
            .fillna("Não categorizado")
            .replace({"": "Não categorizado", "-": "Não categorizado"})
            .value_counts()
            .head(7)
            .reset_index()
        )
        cat_counts.columns = ["categoria", "quantidade"]
        categorias_lista = cat_counts.to_dict(orient="records")

        # 3. Métricas de SLA via TicketAnalyticsEngine
        df_sla = engine.metricas_sla()
        sla_lista = df_sla.to_dict(orient="records")

        # 4. Outliers de clientes via Z-Score
        df_outliers = engine.ranking_clientes_outliers(top_n=6)
        outliers_lista = df_outliers.to_dict(orient="records")

        # 5. Ranking de técnicos
        df_tecnicos = engine.ranking_tecnicos(top_n=6)
        tecnicos_lista = df_tecnicos.to_dict(orient="records")

        return {
            "prioridades": prioridades_lista,
            "categorias": categorias_lista,
            "sla": sla_lista,
            "outliers_clientes": outliers_lista,
            "ranking_tecnicos": tecnicos_lista,
        }

