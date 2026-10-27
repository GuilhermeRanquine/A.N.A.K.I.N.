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
                CREATE TABLE IF NOT EXISTS tecnicos_alias (
                    nome_normalizado VARCHAR PRIMARY KEY,
                    email_tecnico VARCHAR
                );
                """
            )

    def salvar_chamados(self, tickets: list[TicketSummary]) -> int:
        """Salva ou atualiza (UPSERT por id) uma lista de chamados no DuckDB.

        Retorna o número de registros processados.
        """
        if not tickets:
            return 0

        with self._obter_conexao() as con:
            for t in tickets:
                # Garante id identificador único (usa codigo como fallback se id for nulo)
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

                con.execute(
                    """
                    INSERT INTO chamados (
                        id, codigo, cliente, cliente_token, contato, assunto, descricao,
                        status, prioridade, tecnico, email_tecnico, mesa_trabalho, categoria_primaria,
                        categoria_secundaria, data_criacao, data_modificacao, data_solucao,
                        status_sla_resposta, status_sla_solucao, atualizado_em
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, now()
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
                    ],
                )

        return len(tickets)

    def obter_email_tecnico(self, nome_tecnico: str) -> Optional[str]:
        """Recupera o e-mail/username cadastrado para um técnico a partir do nome."""
        if not nome_tecnico:
            return None
        nome_norm = normalizar_para_busca(nome_tecnico)
        if not nome_norm:
            return None

        with self._obter_conexao() as con:
            # 1. Busca na tabela de aliases aprendidos
            row = con.execute(
                "SELECT email_tecnico FROM tecnicos_alias WHERE nome_normalizado = ?",
                [nome_norm],
            ).fetchone()
            if row and row[0]:
                return str(row[0])

            # 2. Busca como fallback na tabela chamados
            row_chamados = con.execute(
                """
                SELECT email_tecnico FROM chamados 
                WHERE email_tecnico IS NOT NULL 
                  AND email_tecnico != ''
                  AND LOWER(tecnico) LIKE ? 
                LIMIT 1
                """,
                [f"%{nome_tecnico.strip().lower()}%"],
            ).fetchone()
            if row_chamados and row_chamados[0]:
                return str(row_chamados[0])

        return None

    def salvar_email_tecnico(self, nome_tecnico: str, email_tecnico: str) -> None:
        """Salva ou atualiza a memória de e-mail/username de um técnico."""
        if not nome_tecnico or not email_tecnico:
            return
        nome_norm = normalizar_para_busca(nome_tecnico)
        email_limpo = email_tecnico.strip()
        if not nome_norm or not email_limpo:
            return

        with self._obter_conexao() as con:
            con.execute(
                """
                INSERT INTO tecnicos_alias (nome_normalizado, email_tecnico)
                VALUES (?, ?)
                ON CONFLICT (nome_normalizado) DO UPDATE SET email_tecnico = EXCLUDED.email_tecnico;
                """,
                [nome_norm, email_limpo],
            )

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
