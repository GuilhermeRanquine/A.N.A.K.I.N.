"""Módulo de cache local e armazenamento analítico com DuckDB.

Permite salvar chamados consultados via API para leitura e análise subsequente
sem gastar chamadas adicionais nem ultrapassar o rate limit do Milvus.
"""

from pathlib import Path
from typing import Any, Optional
import json
import re
import unicodedata
import duckdb
import pandas as pd

from anakin.config.settings import Settings, get_settings
from anakin.domain.ticket_models import TicketSummary


def normalizar_para_busca(texto: str) -> str:
    """Normaliza texto para busca removendo acentos, pontuação, traços e espaços."""
    if not texto:
        return ""
    nfkd = unicodedata.normalize("NFKD", str(texto))
    sem_acento = "".join([c for c in nfkd if not unicodedata.combining(c)])
    return re.sub(r"[^a-z0-9]", "", sem_acento.lower())


def normalizar_fonetico(texto: str) -> str:
    """Normalização fonética simples para tolerar variações comuns (ex: y -> i, w -> v, c -> k)."""
    norm = normalizar_para_busca(texto)
    norm = norm.replace("y", "i")
    norm = norm.replace("ca", "ka").replace("co", "ko").replace("cu", "ku")
    norm = re.sub(r"m$", "n", norm)
    return norm


OPERADORES_OFICIAIS_PADRAO: list[tuple[str, str]] = [
    ("Aijalom", "aijalom.goncalves@upgradeti.com.br"),
    ("Carlos Eduardo", "carlos.bucazio@upgradeti.com.br"),
    ("Felipe Xavier", "felipe.xavier@upgradeti.com.br"),
    ("Gabriel Oliveira", "gabriel@upgradeti.com.br"),
    ("Gabryel Santos", "gabryel.santos@upgradeti.com.br"),
    ("Gabriel Sena", "gabriel.sena@upgradeti.com.br"),
    ("Guilherme Maganha", "guilherme.melo@upgradeti.com.br"),
    ("Guilherme Ranquine", "guilherme.ranquine@upgradeti.com.br"),
    ("Kaio Silva", "kaio.silva@upgradeti.com.br"),
    ("Leandro Oliveira", "leandro@upgradeti.com.br"),
    ("Paulo Roberto", "paulo@upgradeti.com.br"),
    ("Rodrigo Mellino", "analista05@upgradeti.com.br"),
    ("Willian Pereira", "willian.pereira@upgradeti.com.br"),
    ("Wander Cristiano", ""),
]


def normalizar_texto_mesa(texto: str) -> str:
    """Remove artigos, preposições e termos redundantes de mesa de trabalho."""
    if not texto:
        return ""
    padrao = r"^(?:mesa(?:\s+de\s+trabalho)?(?:\s+(?:do|da|de|dos|das|para|pro|pra))?\s*[:\-]?\s*|fila(?:\s+(?:do|da|de|para))?\s*[:\-]?\s*)"
    limpo = re.sub(padrao, "", texto.strip(), flags=re.IGNORECASE).strip()
    return limpo if limpo else texto.strip()


class LocalCache:
    """Gerenciador do banco de dados DuckDB local para chamados."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.db_path = Path(self.settings.duckdb_path)
        # Garante a existência do diretório pai (ex: data/)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._inicializar_tabelas()

    def _obter_conexao(self) -> duckdb.DuckDBPyConnection:
        """Abre uma conexão com o DuckDB."""
        return duckdb.connect(str(self.db_path))

    def _inicializar_tabelas(self) -> None:
        """Cria as tabelas analíticas caso não existam e adiciona colunas necessárias."""
        with self._obter_conexao() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS chamados (
                    id VARCHAR PRIMARY KEY,
                    codigo VARCHAR,
                    cliente VARCHAR,
                    cliente_token VARCHAR,
                    contato VARCHAR,
                    assunto VARCHAR,
                    descricao VARCHAR,
                    status VARCHAR,
                    prioridade VARCHAR,
                    tecnico VARCHAR,
                    email_tecnico VARCHAR,
                    mesa_trabalho VARCHAR,
                    categoria_primaria VARCHAR,
                    categoria_secundaria VARCHAR,
                    data_criacao VARCHAR,
                    data_modificacao VARCHAR,
                    data_solucao VARCHAR,
                    status_sla_resposta VARCHAR,
                    status_sla_solucao VARCHAR,
                    atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                ALTER TABLE chamados ADD COLUMN IF NOT EXISTS email_tecnico VARCHAR;
                ALTER TABLE chamados ADD COLUMN IF NOT EXISTS raw_json VARCHAR;
                ALTER TABLE chamados ADD COLUMN IF NOT EXISTS ultima_consulta_direta_em TIMESTAMP;

                -- Cache de acompanhamentos e histórico de chamados (com validade configurável)
                CREATE TABLE IF NOT EXISTS acompanhamentos_cache (
                    chamado_codigo VARCHAR PRIMARY KEY,
                    payload_json VARCHAR,
                    atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                -- Tabela oficial única e higienizada de operadores da Upgrade TI
                CREATE TABLE IF NOT EXISTS operadores_oficiais (
                    nome_exibicao VARCHAR PRIMARY KEY,
                    email_tecnico VARCHAR
                );

                -- Remove tabela legada com apelidos duplicados/colados
                DROP TABLE IF EXISTS tecnicos_alias;
                """
            )

            # Popula ou atualiza operadores oficiais padrão (garante exatamente 1 linha por operador)
            for nome, default_email in OPERADORES_OFICIAIS_PADRAO:
                row = con.execute(
                    "SELECT email_tecnico FROM operadores_oficiais WHERE nome_exibicao = ?",
                    [nome],
                ).fetchone()

                if not row:
                    email_final = default_email
                    if not email_final:
                        row_hist = con.execute(
                            """
                            SELECT email_tecnico FROM chamados
                            WHERE LOWER(tecnico) LIKE ?
                              AND email_tecnico IS NOT NULL
                              AND email_tecnico LIKE '%@%'
                              AND email_tecnico NOT LIKE '% %'
                            LIMIT 1
                            """,
                            [f"%{nome.split()[0].lower()}%"],
                        ).fetchone()
                        if row_hist and row_hist[0]:
                            email_final = str(row_hist[0]).strip().lower()

                    con.execute(
                        "INSERT INTO operadores_oficiais (nome_exibicao, email_tecnico) VALUES (?, ?)",
                        [nome, email_final],
                    )
                else:
                    atual_email = str(row[0] or "").strip()
                    if not atual_email and default_email:
                        con.execute(
                            "UPDATE operadores_oficiais SET email_tecnico = ? WHERE nome_exibicao = ?",
                            [default_email, nome],
                        )
                    elif not atual_email:
                        row_hist = con.execute(
                            """
                            SELECT email_tecnico FROM chamados
                            WHERE LOWER(tecnico) LIKE ?
                              AND email_tecnico IS NOT NULL
                              AND email_tecnico LIKE '%@%'
                              AND email_tecnico NOT LIKE '% %'
                            LIMIT 1
                            """,
                            [f"%{nome.split()[0].lower()}%"],
                        ).fetchone()
                        if row_hist and row_hist[0]:
                            con.execute(
                                "UPDATE operadores_oficiais SET email_tecnico = ? WHERE nome_exibicao = ?",
                                [str(row_hist[0]).strip().lower(), nome],
                            )

    def _preencher_email_operador_se_vazio(self, nome_tecnico: str, email: str) -> None:
        """Atualiza email_tecnico de um operador oficial apenas se o email estiver vazio.
        
        NUNCA insere novos registros nem cria aliases na sincronização.
        """
        if not nome_tecnico or not email:
            return
        email_limpo = email.strip().lower()
        if "@" not in email_limpo or " " in email_limpo:
            return

        tec_norm = normalizar_para_busca(nome_tecnico)
        if not tec_norm:
            return

        with self._obter_conexao() as con:
            rows = con.execute(
                "SELECT nome_exibicao, email_tecnico FROM operadores_oficiais WHERE email_tecnico IS NULL OR TRIM(email_tecnico) = ''"
            ).fetchall()
            for nome_oficial, _ in rows:
                nome_oficial_str = str(nome_oficial)
                oficial_norm = normalizar_para_busca(nome_oficial_str)
                primeiro_nome_norm = normalizar_para_busca(nome_oficial_str.split()[0])
                if (
                    tec_norm == oficial_norm
                    or oficial_norm in tec_norm
                    or tec_norm in oficial_norm
                    or (primeiro_nome_norm and primeiro_nome_norm in tec_norm)
                ):
                    con.execute(
                        "UPDATE operadores_oficiais SET email_tecnico = ? WHERE nome_exibicao = ?",
                        [email_limpo, nome_oficial_str],
                    )
                    break

    def salvar_chamados(self, tickets: list[TicketSummary], consulta_direta: bool = False) -> int:
        """Salva ou atualiza (UPSERT por id) uma lista de chamados no DuckDB.

        Args:
            tickets: Lista de chamados a salvar.
            consulta_direta: Se True, registra o timestamp atual em 'ultima_consulta_direta_em'.
        """
        if not tickets:
            return 0

        with self._obter_conexao() as con:
            for t in tickets:
                ticket_id = str(t.id if t.id is not None else t.codigo)
                codigo_str = str(t.codigo) if t.codigo is not None else None
                status_str = str(t.status) if t.status is not None else None
                prioridade_str = str(t.prioridade) if t.prioridade is not None else None

                sla_resp_str = (
                    json.dumps(t.status_sla_resposta, ensure_ascii=False)
                    if isinstance(t.status_sla_resposta, (dict, list))
                    else str(t.status_sla_resposta) if t.status_sla_resposta is not None else None
                )
                sla_sol_str = (
                    json.dumps(t.status_sla_solucao, ensure_ascii=False)
                    if isinstance(t.status_sla_solucao, (dict, list))
                    else str(t.status_sla_solucao) if t.status_sla_solucao is not None else None
                )

                raw_json_str = getattr(t, "raw_json", None)
                if not raw_json_str and hasattr(t, "model_dump"):
                    try:
                        raw_json_str = json.dumps(t.model_dump(), ensure_ascii=False)
                    except Exception:
                        raw_json_str = None

                con.execute(
                    """
                    INSERT INTO chamados (
                        id, codigo, cliente, cliente_token, contato, assunto, descricao,
                        status, prioridade, tecnico, email_tecnico, mesa_trabalho, categoria_primaria,
                        categoria_secundaria, data_criacao, data_modificacao, data_solucao,
                        status_sla_resposta, status_sla_solucao, raw_json, ultima_consulta_direta_em, atualizado_em
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?,
                        CASE WHEN ? THEN now() ELSE NULL END,
                        now()
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        codigo = EXCLUDED.codigo,
                        cliente = EXCLUDED.cliente,
                        cliente_token = EXCLUDED.cliente_token,
                        contato = EXCLUDED.contato,
                        assunto = EXCLUDED.assunto,
                        descricao = EXCLUDED.descricao,
                        status = EXCLUDED.status,
                        prioridade = EXCLUDED.prioridade,
                        tecnico = EXCLUDED.tecnico,
                        email_tecnico = EXCLUDED.email_tecnico,
                        mesa_trabalho = EXCLUDED.mesa_trabalho,
                        categoria_primaria = EXCLUDED.categoria_primaria,
                        categoria_secundaria = EXCLUDED.categoria_secundaria,
                        data_criacao = EXCLUDED.data_criacao,
                        data_modificacao = EXCLUDED.data_modificacao,
                        data_solucao = EXCLUDED.data_solucao,
                        status_sla_resposta = EXCLUDED.status_sla_resposta,
                        status_sla_solucao = EXCLUDED.status_sla_solucao,
                        raw_json = COALESCE(EXCLUDED.raw_json, chamados.raw_json),
                        ultima_consulta_direta_em = CASE WHEN ? THEN now() ELSE chamados.ultima_consulta_direta_em END,
                        atualizado_em = now();
                    """,
                    [
                        ticket_id,
                        codigo_str,
                        t.cliente,
                        t.cliente_token,
                        t.contato,
                        t.assunto,
                        t.descricao,
                        status_str,
                        prioridade_str,
                        t.tecnico,
                        t.email_tecnico,
                        t.mesa_trabalho,
                        t.categoria_primaria,
                        t.categoria_secundaria,
                        t.data_criacao,
                        t.data_modificacao,
                        t.data_solucao,
                        sla_resp_str,
                        sla_sol_str,
                        raw_json_str,
                        consulta_direta,
                        consulta_direta,
                    ],
                )

                # Se houver técnico e email_tecnico com arroba, preenche APENAS se algum operador oficial estiver com email vazio
                if t.tecnico and t.email_tecnico:
                    self._preencher_email_operador_se_vazio(str(t.tecnico), str(t.email_tecnico))

        return len(tickets)

    def sincronizar_status_abertos(self, codigos_abertos_atuais: list[str | int]) -> int:
        """Marca como 'Finalizado' chamados que antes constavam como abertos mas não vieram mais na busca de abertos.

        Evita que chamados concluídos ou encerrados no Milvus continuem aparecendo com status desatualizado.
        """
        if not codigos_abertos_atuais:
            return 0

        cods_str = [str(c).lstrip("#").strip() for c in codigos_abertos_atuais if c is not None and str(c).strip()]
        if not cods_str:
            return 0

        placeholders = ", ".join(["?"] * len(cods_str))
        with self._obter_conexao() as con:
            query = f"""
                UPDATE chamados
                SET status = 'Finalizado',
                    atualizado_em = now()
                WHERE LOWER(status) NOT IN ('finalizado', 'resolvido', 'fechado', 'cancelado')
                  AND CAST(codigo AS VARCHAR) NOT IN ({placeholders});
            """
            res = con.execute(query, cods_str)
            return res.rowcount if hasattr(res, "rowcount") else 0

    def definir_email_operador(self, nome_operador: str, email_tecnico: str) -> tuple[bool, str]:
        """Define ou atualiza o e-mail de um operador no catálogo oficial.

        Se o operador já existir (mesmo com busca aproximada), atualiza.
        Se não existir, cadastra como novo operador oficial.
        Retorna (sucesso: bool, mensagem: str).
        """
        if not nome_operador or not nome_operador.strip():
            return False, "Nome do operador não pode ser vazio."

        nome_limpo = nome_operador.strip()
        email_limpo = email_tecnico.strip().lower() if email_tecnico else ""

        if email_limpo and ("@" not in email_limpo or " " in email_limpo):
            return False, f"E-mail '{email_limpo}' inválido (deve conter '@' e não pode conter espaços)."

        with self._obter_conexao() as con:
            operadores = con.execute("SELECT nome_exibicao FROM operadores_oficiais").fetchall()
            alvo_nome: Optional[str] = None
            termo_norm = normalizar_para_busca(nome_limpo)

            for (op_nome,) in operadores:
                op_nome_str = str(op_nome)
                if normalizar_para_busca(op_nome_str) == termo_norm:
                    alvo_nome = op_nome_str
                    break

            if not alvo_nome:
                for (op_nome,) in operadores:
                    op_nome_str = str(op_nome)
                    if termo_norm in normalizar_para_busca(op_nome_str) or normalizar_para_busca(op_nome_str) in termo_norm:
                        alvo_nome = op_nome_str
                        break

            if alvo_nome:
                con.execute(
                    "UPDATE operadores_oficiais SET email_tecnico = ? WHERE nome_exibicao = ?",
                    [email_limpo, alvo_nome],
                )
                msg = f"Operador oficial '{alvo_nome}' atualizado com o e-mail: '{email_limpo}'."
                return True, msg
            else:
                con.execute(
                    "INSERT INTO operadores_oficiais (nome_exibicao, email_tecnico) VALUES (?, ?)",
                    [nome_limpo, email_limpo],
                )
                msg = f"Novo operador oficial '{nome_limpo}' cadastrado com o e-mail: '{email_limpo}'."
                return True, msg

    def salvar_email_tecnico(self, nome_tecnico: str, email_tecnico: str) -> bool:
        """Alias de compatibilidade que delega para definir_email_operador."""
        sucesso, _ = self.definir_email_operador(nome_tecnico, email_tecnico)
        return sucesso

    def obter_email_tecnico(self, nome_tecnico: str) -> Optional[str]:
        """Recupera o e-mail oficial cadastrado para um técnico a partir do nome."""
        if not nome_tecnico:
            return None
        candidatos = self.buscar_operadores_candidatos(nome_tecnico)
        if len(candidatos) == 1 and candidatos[0][1]:
            return candidatos[0][1]
        for nome, email in candidatos:
            if normalizar_para_busca(nome) == normalizar_para_busca(nome_tecnico) and email:
                return email
        return None

    def listar_operadores_oficiais(self) -> list[tuple[str, str]]:
        """Retorna lista dos operadores oficiais em ordem alfabética."""
        with self._obter_conexao() as con:
            rows = con.execute(
                """
                SELECT nome_exibicao, COALESCE(email_tecnico, '')
                FROM operadores_oficiais
                ORDER BY nome_exibicao COLLATE NOCASE ASC
                """
            ).fetchall()
            return [(str(r[0]), str(r[1])) for r in rows if r[0]]

    def listar_operadores_mapeados(self) -> list[tuple[str, str]]:
        """Alias de compatibilidade para listar_operadores_oficiais."""
        return self.listar_operadores_oficiais()

    def listar_mesas_trabalho(self) -> list[str]:
        """Lista todas as mesas de trabalho únicas cadastradas no histórico do DuckDB."""
        with self._obter_conexao() as con:
            rows = con.execute(
                """
                SELECT DISTINCT mesa_trabalho
                FROM chamados
                WHERE mesa_trabalho IS NOT NULL
                  AND TRIM(mesa_trabalho) != ''
                ORDER BY mesa_trabalho ASC
                """
            ).fetchall()
            return [str(r[0]).strip() for r in rows if r[0] and str(r[0]).strip()]

    def resolver_mesa_trabalho(self, termo: str) -> Optional[str]:
        """Localiza mesa de trabalho exata ou parcial case-insensitive a partir de um termo."""
        if not termo or not termo.strip():
            return None

        termo_limpo = normalizar_texto_mesa(termo)
        if not termo_limpo:
            return None

        mesas = self.listar_mesas_trabalho()
        if not mesas:
            return termo_limpo

        termo_norm = normalizar_para_busca(termo_limpo)

        # 1. Correspondência exata case-insensitive
        for m in mesas:
            if m.lower() == termo_limpo.lower():
                return m

        # 2. Correspondência normalizada exata
        for m in mesas:
            if normalizar_para_busca(m) == termo_norm:
                return m

        # 3. Correspondência parcial (termo contido na mesa cadastrada)
        for m in mesas:
            m_norm = normalizar_para_busca(m)
            if termo_norm in m_norm:
                return m

        # 4. Correspondência parcial inversa (mesa cadastrada contida no termo)
        for m in mesas:
            m_norm = normalizar_para_busca(m)
            if m_norm in termo_norm:
                return m

        return termo_limpo

    def buscar_operadores_candidatos(self, termo: str) -> list[tuple[str, str]]:
        """Busca correspondência no catálogo oficial de operadores (sem duplicatas)."""
        if not termo or not termo.strip():
            return []
        termo_limpo = termo.strip()
        termo_norm = normalizar_para_busca(termo_limpo)
        if not termo_norm:
            return []

        operadores = self.listar_operadores_oficiais()
        if not operadores:
            return []

        # 1. Correspondência exata de nome normalizado
        for nome, email in operadores:
            if normalizar_para_busca(nome) == termo_norm:
                return [(nome, email)]

        # 2. Correspondência exata no e-mail (caso o usuário tenha passado um e-mail)
        if "@" in termo_limpo:
            for nome, email in operadores:
                if email and email.lower() == termo_limpo.lower():
                    return [(nome, email)]

        # 3. Correspondência por token ou substring (com tolerância fonética y/i, etc.)
        termo_fon = normalizar_fonetico(termo_limpo)
        candidatos: list[tuple[str, str]] = []

        for nome, email in operadores:
            nome_norm = normalizar_para_busca(nome)
            nome_fon = normalizar_fonetico(nome)
            email_norm = normalizar_para_busca(email) if email else ""

            # Tokens do nome (ex: ["carlos", "eduardo"])
            tokens_norm = [normalizar_para_busca(p) for p in nome.split() if p]
            tokens_fon = [normalizar_fonetico(p) for p in nome.split() if p]

            if (
                termo_norm in tokens_norm
                or termo_fon in tokens_fon
                or termo_norm in nome_norm
                or termo_fon in nome_fon
                or (email_norm and termo_norm in email_norm)
            ):
                candidatos.append((nome, email))

        return candidatos

    def obter_chamados_df(self, status: Optional[str | int] = None) -> pd.DataFrame:
        """Carrega os chamados do cache em um DataFrame do Pandas."""
        query = "SELECT * FROM chamados"
        params: list[Any] = []

        if status is not None:
            query += " WHERE status = ?"
            params.append(str(status))

        query += " ORDER BY data_criacao DESC"

        with self._obter_conexao() as con:
            return con.execute(query, params).df()

    def total_chamados(self) -> int:
        """Retorna o número total de chamados armazenados no cache."""
        with self._obter_conexao() as con:
            res = con.execute("SELECT COUNT(*) FROM chamados").fetchone()
            return int(res[0]) if res else 0

    def salvar_acompanhamentos_cache(
        self,
        chamado_codigo: str | int,
        acompanhamentos: list[dict[str, Any]] | list[Any] | str,
    ) -> None:
        """Salva a lista completa de acompanhamentos no cache local com timestamp atualizado."""
        if not chamado_codigo:
            return
        cod_limpo = str(chamado_codigo).lstrip("#").strip()
        if isinstance(acompanhamentos, str):
            payload_str = acompanhamentos
        else:
            payload_list: list[dict[str, Any]] = []
            for item in acompanhamentos:
                if hasattr(item, "model_dump"):
                    payload_list.append(item.model_dump())
                elif isinstance(item, dict):
                    payload_list.append(item)
                else:
                    payload_list.append({"texto": str(item)})
            payload_str = json.dumps(payload_list, ensure_ascii=False)

        with self._obter_conexao() as con:
            con.execute(
                """
                INSERT INTO acompanhamentos_cache (chamado_codigo, payload_json, atualizado_em)
                VALUES (?, ?, now())
                ON CONFLICT (chamado_codigo) DO UPDATE SET
                    payload_json = EXCLUDED.payload_json,
                    atualizado_em = now();
                """,
                [cod_limpo, payload_str],
            )

    def obter_acompanhamentos_cache(
        self,
        chamado_codigo: str | int,
        max_idade_minutos: int = 10,
    ) -> Optional[list[dict[str, Any]]]:
        """Recupera acompanhamentos em cache se ainda dentro da janela de validade (max_idade_minutos)."""
        if not chamado_codigo:
            return None
        cod_limpo = str(chamado_codigo).lstrip("#").strip()
        with self._obter_conexao() as con:
            row = con.execute(
                """
                SELECT payload_json, atualizado_em
                FROM acompanhamentos_cache
                WHERE chamado_codigo = ?
                """,
                [cod_limpo],
            ).fetchone()

            if not row or not row[0]:
                return None

            payload_str, atualizado_em = row
            if atualizado_em is not None and max_idade_minutos > 0:
                import datetime
                agora = datetime.datetime.now()
                if hasattr(atualizado_em, "tzinfo") and atualizado_em.tzinfo is not None:
                    agora = datetime.datetime.now(datetime.timezone.utc)
                try:
                    diff_segundos = (agora - atualizado_em).total_seconds()
                    if diff_segundos > (max_idade_minutos * 60):
                        return None
                except Exception:
                    pass

            try:
                dados = json.loads(payload_str)
                return dados if isinstance(dados, list) else None
            except Exception:
                return None

    def obter_chamado_por_codigo(self, codigo: str | int) -> Optional[dict[str, Any]]:
        """Busca um chamado específico no DuckDB pelo código ou ID, extraindo raw_json enriquecido."""
        if not codigo:
            return None
        cod_limpo = str(codigo).lstrip("#").strip()
        with self._obter_conexao() as con:
            df = con.execute(
                """
                SELECT * FROM chamados
                WHERE CAST(codigo AS VARCHAR) = ? OR CAST(id AS VARCHAR) = ?
                LIMIT 1
                """,
                [cod_limpo, cod_limpo],
            ).fetch_df()

            if df.empty:
                return None

            d = df.iloc[0].to_dict()
            for k, v in list(d.items()):
                if pd.isna(v):
                    d[k] = None

            if d.get("raw_json"):
                try:
                    raw_dict = json.loads(str(d["raw_json"]))
                    if isinstance(raw_dict, dict):
                        d["_raw_milvus"] = raw_dict
                        
                        # Mapeamento e extração cirúrgica de 100% dos campos ricos do Milvus
                        campos_ricos_map: dict[str, tuple[str, ...]] = {
                            "status": ("status", "status_nome", "chamado_status"),
                            "tecnico": ("tecnico", "tecnico_nome", "analista", "usuario_nome", "responsavel"),
                            "email_tecnico": ("email_tecnico", "tecnico_email", "email_analista", "usuario_email"),
                            "mesa_trabalho": ("mesa_trabalho", "mesa_trabalho_nome", "mesa"),
                            "prioridade": ("prioridade", "prioridade_nome"),
                            "categoria_primaria": ("categoria_primaria", "categoria", "categoria_nome"),
                            "categoria_secundaria": ("categoria_secundaria", "subcategoria", "subcategoria_nome"),
                            "tipo_ticket": ("tipo_ticket", "tipo", "origem", "tipo_nome"),
                            "origem": ("origem", "tipo_ticket", "canal"),
                            "setor": ("setor", "setor_nome", "departamento"),
                            "contato": ("contato", "contato_nome", "nome_contato"),
                            "telefone": ("telefone", "contato_telefone", "celular"),
                            "email_conferencia": ("email_conferencia", "email_notificacao", "email"),
                            "dispositivo_vinculado": ("dispositivo_vinculado", "dispositivo", "hostname", "patrimonio"),
                            "total_horas": ("total_horas", "horas_gastas", "horas"),
                            "servico_realizado": ("servico_realizado", "servico_executado", "resolucao"),
                            "motivo_pausa": ("motivo_pausa", "pausa_motivo"),
                            "total_avaliacao": ("total_avaliacao", "avaliacao", "nota_avaliacao", "estrelas"),
                            "descricao_avaliacao": ("descricao_avaliacao", "comentario_avaliacao", "avaliacao_comentario"),
                            "data_criacao": ("data_criacao", "criado_em", "data_abertura"),
                            "data_modificacao": ("data_modificacao", "modificado_em", "data_hora_alteracao"),
                            "data_solucao": ("data_solucao", "solucionado_em", "data_hora_solucao"),
                            "data_resposta": ("data_resposta", "respondido_em"),
                            "sla": ("sla",),
                            "impacto": ("impacto",),
                            "urgencia": ("urgencia",),
                            "ultima_log": ("ultima_log",),
                            "ultimas_cinco_logs": ("ultimas_cinco_logs",),
                        }

                        for campo_dest, aliases in campos_ricos_map.items():
                            for al in aliases:
                                if al in raw_dict and raw_dict[al] is not None:
                                    val = raw_dict[al]
                                    if isinstance(val, dict) and "nome" in val:
                                        val = val["nome"]
                                    d[campo_dest] = val
                                    break
                except Exception:
                    pass

            return d

