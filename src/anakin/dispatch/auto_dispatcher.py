"""Módulo central de regras de negócio e despacho automático de chamados.

Implementa:
- Pool estrito de 6 operadores elegíveis da Upgrade TI.
- Bloqueio rígido contra operadores não autorizados (Paulo, Leandro, Rodrigo, Wander, etc.).
- Algoritmo de Fila Ponderada por Carga (Score de Ocupação) com pesos configuráveis:
    Score = (Atendendo * peso_atendendo) + (A Fazer * peso_a_fazer) + (Pausado * peso_pausado) + (Outros * peso_outros)
- Regra de Desempate: quem foi atribuído há mais tempo ou round-robin sequencial.
- Justificativa matemática explícita a cada atribuição.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
import duckdb
from rich.console import Console

from anakin.config.settings import Settings, get_settings
from anakin.domain.ticket_models import TicketSummary
from anakin.milvus.ticket_creator_client import MilvusTicketCreatorClient
from anakin.storage.local_cache import LocalCache

console = Console()


class SecurityDispatchError(Exception):
    """Exceção levantada em caso de violação das regras de segurança de despacho."""


@dataclass(frozen=True)
class OperadorElegivel:
    """Representa um operador oficialmente autorizado a receber chamados automáticos."""

    nome: str
    email: str
    termo_filtro_sql: str

    def __str__(self) -> str:
        return f"{self.nome} <{self.email}>"


# 1. Pool estrito de 6 operadores oficiais autorizados
OPERADORES_ELEGIVEIS: list[OperadorElegivel] = [
    OperadorElegivel(
        nome="Carlos Eduardo",
        email="carlos.bucazio@upgradeti.com.br",
        termo_filtro_sql="%bucazio%",
    ),
    OperadorElegivel(
        nome="Gabriel Oliveira",
        email="gabriel@upgradeti.com.br",
        termo_filtro_sql="%gabriel%oliveira%",
    ),
    OperadorElegivel(
        nome="Gabryel Santos",
        email="gabryel.santos@upgradeti.com.br",
        termo_filtro_sql="%gabryel%santos%",
    ),
    OperadorElegivel(
        nome="Gabriel Sena",
        email="gabriel.sena@upgradeti.com.br",
        termo_filtro_sql="%gabriel%sena%",
    ),
    OperadorElegivel(
        nome="Guilherme Maganha",
        email="guilherme.melo@upgradeti.com.br",
        termo_filtro_sql="%maganha%",
    ),
    OperadorElegivel(
        nome="Kaio Silva",
        email="kaio.silva@upgradeti.com.br",
        termo_filtro_sql="%kaio%",
    ),
]

OPERADORES_ELEGIVEIS_EMAILS: set[str] = {op.email.lower() for op in OPERADORES_ELEGIVEIS}

# 2. Operadores estritamente bloqueados da fila automática
OPERADORES_BLOQUEADOS: set[str] = {
    "paulo@upgradeti.com.br",
    "leandro@upgradeti.com.br",
    "analista05@upgradeti.com.br",  # Rodrigo Mellino
    "felipe.xavier@upgradeti.com.br",
    "willian.pereira@upgradeti.com.br",
    "aijalom.goncalves@upgradeti.com.br",
    "guilherme.ranquine@upgradeti.com.br",
}

TERMOS_NOMES_BLOQUEADOS: tuple[str, ...] = (
    "paulo",
    "leandro",
    "rodrigo",
    "wander",
    "felipe",
    "willian",
    "aijalom",
    "ranquine",
)


def verificar_seguranca_operador(email: str, nome: Optional[str] = None) -> None:
    """Aplica trava estrita: impede qualquer tentativa de atribuição fora dos 6 operadores."""
    email_clean = email.strip().lower() if email else ""

    if email_clean in OPERADORES_BLOQUEADOS:
        raise SecurityDispatchError(
            f"BLOQUEIO DE SEGURANÇA: O operador com e-mail '{email_clean}' está na lista de bloqueados "
            f"da fila automática de chamados."
        )

    if nome:
        nome_lower = nome.lower()
        for t in TERMOS_NOMES_BLOQUEADOS:
            if t in nome_lower:
                raise SecurityDispatchError(
                    f"BLOQUEIO DE SEGURANÇA: O operador '{nome}' está na lista de bloqueados "
                    f"da fila automática de chamados."
                )

    if email_clean not in OPERADORES_ELEGIVEIS_EMAILS:
        raise SecurityDispatchError(
            f"BLOQUEIO DE SEGURANÇA: O e-mail '{email_clean}' não pertence ao pool dos 6 operadores "
            f"elegíveis oficiais da Upgrade TI."
        )


def eh_chamado_sem_tecnico(chamado: TicketSummary) -> bool:
    """Verifica se um chamado ainda não foi atribuído a nenhum operador."""
    email = (chamado.email_tecnico or "").strip().lower()
    if email and "@" in email and " " not in email:
        return False

    tec = (chamado.tecnico or "").strip().lower()
    termos_sem_tecnico = {
        "",
        "none",
        "null",
        "não atribuído",
        "nao atribuido",
        "não atribuido",
        "nao atribuído",
        "sem técnico",
        "sem tecnico",
        "não informado",
        "nao informado",
        "-",
    }
    return tec in termos_sem_tecnico


@dataclass
class ScoreOperador:
    """Armazena a decomposição de carga por status e o score ponderado de um operador."""

    operador: OperadorElegivel
    qtd_atendendo: int = 0
    qtd_a_fazer: int = 0
    qtd_pausado: int = 0
    qtd_outros: int = 0
    score_total: int = 0
    ultima_atribuicao: Optional[datetime] = None
    ultima_atribuicao_str: str = ""

    @property
    def total_chamados_abertos(self) -> int:
        """Soma simples do total de chamados abertos."""
        return self.qtd_atendendo + self.qtd_a_fazer + self.qtd_pausado + self.qtd_outros

    @property
    def detalhe_formatado(self) -> str:
        """Gera a descrição detalhada dos chamados por status."""
        texto = f"{self.qtd_atendendo} fazendo, {self.qtd_a_fazer} a fazer, {self.qtd_pausado} pausado"
        if self.qtd_outros > 0:
            texto += f", {self.qtd_outros} outros"
        return texto


@dataclass
class AtribuicaoRegistro:
    """Registro de auditoria de uma atribuição de chamado executada ou simulada."""

    horario: str
    codigo: str
    cliente: str
    assunto: str
    operador_nome: str
    operador_email: str
    score_justificativa: str
    status: str
    simulacao: bool = False


class AutoDispatcher:
    """Controlador inteligente de distribuição baseado em Score de Carga Ponderada."""

    def __init__(
        self,
        settings: Settings | None = None,
        cache: LocalCache | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.cache = cache or LocalCache(settings=self.settings)
        self.db_path = Path(self.settings.duckdb_path)
        self._round_robin_counter: int = 0
        self.historico_atribuicoes: list[AtribuicaoRegistro] = []

        # Deltas transitórios em memória para simulação ou despachos dentro do mesmo ciclo
        # Estrutura: {email: {"atendendo": 0, "a_fazer": 0, "pausado": 0, "outros": 0}}
        self._deltas_sessao: dict[str, dict[str, int]] = {
            op.email: defaultdict(int) for op in OPERADORES_ELEGIVEIS
        }
        self._ultima_atribuicao_sessao: dict[str, datetime] = {}

    def _obter_conexao(self, read_only: bool = True) -> duckdb.DuckDBPyConnection:
        """Abre conexão com o banco DuckDB configurado."""
        return duckdb.connect(str(self.db_path), read_only=read_only)

    def calcular_scores_pool(self) -> dict[OperadorElegivel, ScoreOperador]:
        """Calcula o Score de Carga Ponderada de todos os 6 operadores elegíveis via DuckDB.

        Score = (Qtd_Atendendo * peso_atendendo) +
                (Qtd_A_Fazer * peso_a_fazer) +
                (Qtd_Pausado * peso_pausado) +
                (Qtd_Outros * peso_outros)
        """
        p_atendendo = self.settings.peso_status_atendendo
        p_a_fazer = self.settings.peso_status_a_fazer
        p_pausado = self.settings.peso_status_pausado
        p_outros = self.settings.peso_status_outros

        scores: dict[OperadorElegivel, ScoreOperador] = {}

        for op in OPERADORES_ELEGIVEIS:
            qtd_atendendo = 0
            qtd_a_fazer = 0
            qtd_pausado = 0
            qtd_outros = 0
            ultima_data_str = ""

            if self.db_path.exists():
                try:
                    with self._obter_conexao(read_only=True) as con:
                        query = """
                            SELECT 
                                CASE 
                                    WHEN LOWER(status) LIKE '%atendendo%' OR LOWER(status) LIKE '%em atendimento%' THEN 'atendendo'
                                    WHEN LOWER(status) LIKE '%a fazer%' OR LOWER(status) LIKE '%aberto%' THEN 'a_fazer'
                                    WHEN LOWER(status) LIKE '%pausado%' OR LOWER(status) LIKE '%espera%' THEN 'pausado'
                                    ELSE 'outros'
                                END AS st_grp,
                                count(*) AS total,
                                MAX(COALESCE(data_criacao, '')) AS max_data
                            FROM chamados
                            WHERE LOWER(status) NOT IN ('finalizado', 'resolvido', 'fechado', 'cancelado')
                              AND (
                                  LOWER(email_tecnico) = LOWER(?)
                                  OR (
                                      (email_tecnico IS NULL OR TRIM(email_tecnico) = '')
                                      AND LOWER(tecnico) LIKE ?
                                  )
                              )
                            GROUP BY 1
                        """
                        rows = con.execute(query, [op.email, op.termo_filtro_sql]).fetchall()
                        for st_grp, count_val, max_dt in rows:
                            cnt = int(count_val)
                            if st_grp == "atendendo":
                                qtd_atendendo += cnt
                            elif st_grp == "a_fazer":
                                qtd_a_fazer += cnt
                            elif st_grp == "pausado":
                                qtd_pausado += cnt
                            else:
                                qtd_outros += cnt

                            if max_dt and str(max_dt) > ultima_data_str:
                                ultima_data_str = str(max_dt)
                except Exception as e:
                    console.print(f"[dim yellow]Aviso ao consultar DuckDB para {op.nome}: {e}[/dim yellow]")

            # Soma os deltas transitórios da sessão
            deltas = self._deltas_sessao.get(op.email, {})
            qtd_atendendo += deltas.get("atendendo", 0)
            qtd_a_fazer += deltas.get("a_fazer", 0)
            qtd_pausado += deltas.get("pausado", 0)
            qtd_outros += deltas.get("outros", 0)

            # Cálculo da pontuação total ponderada
            score_total = (
                (qtd_atendendo * p_atendendo)
                + (qtd_a_fazer * p_a_fazer)
                + (qtd_pausado * p_pausado)
                + (qtd_outros * p_outros)
            )

            # Data de última atribuição (prioriza a da sessão se houver)
            dt_sessao = self._ultima_atribuicao_sessao.get(op.email)
            if dt_sessao:
                dt_final = dt_sessao
                dt_str = dt_sessao.strftime("%Y-%m-%d %H:%M:%S")
            elif ultima_data_str:
                dt_str = ultima_data_str
                try:
                    dt_final = datetime.fromisoformat(ultima_data_str)
                except Exception:
                    dt_final = None
            else:
                dt_final = None
                dt_str = ""

            scores[op] = ScoreOperador(
                operador=op,
                qtd_atendendo=qtd_atendendo,
                qtd_a_fazer=qtd_a_fazer,
                qtd_pausado=qtd_pausado,
                qtd_outros=qtd_outros,
                score_total=score_total,
                ultima_atribuicao=dt_final,
                ultima_atribuicao_str=dt_str,
            )

        return scores

    def obter_cargas_pool(self) -> dict[OperadorElegivel, int]:
        """Compatibilidade: retorna dicionário com total de chamados abertos por operador."""
        scores = self.calcular_scores_pool()
        return {op: sc.total_chamados_abertos for op, sc in scores.items()}

    def selecionar_operador_menor_score(self) -> tuple[OperadorElegivel, ScoreOperador, list[ScoreOperador]]:
        """Seleciona o operador elegível do pool com o MENOR Score de Carga.

        Regra de Desempate:
            Se dois ou mais operadores tiverem o mesmo Score mínimo:
            1. Escolhe aquele que foi atribuído a um chamado há mais tempo (data mais antiga / vazia).
            2. Em caso de empate absoluto de data, utiliza o Round-Robin sequencial.

        Returns:
            tuple[OperadorElegivel, ScoreOperador, list[ScoreOperador]]:
                - Operador selecionado
                - Objeto ScoreOperador com dados completos e detalhe matemático
                - Lista ordenada de todos os operadores por score crescente
        """
        scores_map = self.calcular_scores_pool()
        todos_scores = list(scores_map.values())

        # Ordenação geral informativa
        todos_scores.sort(key=lambda sc: (sc.score_total, sc.total_chamados_abertos))

        # Encontra o menor score do pool
        menor_score = min(sc.score_total for sc in todos_scores)
        candidatos_empatados = [sc for sc in todos_scores if sc.score_total == menor_score]

        if len(candidatos_empatados) == 1:
            escolhido_score = candidatos_empatados[0]
        else:
            # Desempate 1: operador atribuído há mais tempo
            # Quem tem ultima_atribuicao vazia/None recebe a maior prioridade (representado por datetime.min)
            def chave_desempate(sc: ScoreOperador) -> tuple[datetime, int]:
                dt = sc.ultima_atribuicao or datetime.min
                return (dt, sc.total_chamados_abertos)

            candidatos_empatados.sort(key=chave_desempate)

            # Verifica se há empate na data mais antiga
            melhor_data = chave_desempate(candidatos_empatados[0])
            iguais_na_data = [sc for sc in candidatos_empatados if chave_desempate(sc) == melhor_data]

            if len(iguais_na_data) == 1:
                escolhido_score = iguais_na_data[0]
            else:
                # Desempate 2: Round-Robin sequencial entre os estritamente empatados
                idx = self._round_robin_counter % len(iguais_na_data)
                escolhido_score = iguais_na_data[idx]
                self._round_robin_counter += 1

        # Validação rígida de segurança
        verificar_seguranca_operador(escolhido_score.operador.email, escolhido_score.operador.nome)

        return escolhido_score.operador, escolhido_score, todos_scores

    def selecionar_operador_menor_carga(self) -> tuple[OperadorElegivel, int, list[tuple[str, int]]]:
        """Alias retrocompatível delegando para a lógica moderna de menor Score."""
        op, sc, ranking_scores = self.selecionar_operador_menor_score()
        ranking_tuplas = [(s.operador.nome, s.score_total) for s in ranking_scores]
        return op, sc.score_total, ranking_tuplas

    def identificar_chamados_pendentes(
        self,
        tickets: list[TicketSummary],
    ) -> list[TicketSummary]:
        """Filtra e retorna apenas chamados que ainda não possuem operador/técnico atribuído."""
        pendentes: list[TicketSummary] = []
        for t in tickets:
            if eh_chamado_sem_tecnico(t):
                pendentes.append(t)
        return pendentes

    async def despachar_chamado(
        self,
        chamado: TicketSummary,
        operador: OperadorElegivel,
        score_info: Optional[ScoreOperador] = None,
        creator_client: Optional[MilvusTicketCreatorClient] = None,
        simulacao: bool = False,
    ) -> bool:
        """Executa a atribuição do chamado com registro da justificativa matemática."""
        verificar_seguranca_operador(operador.email, operador.nome)

        codigo_str = str(chamado.codigo or chamado.id or "Desconhecido")
        cliente_str = str(chamado.cliente or "Cliente não informado")
        assunto_str = str(chamado.assunto or "Sem assunto")
        horario_str = datetime.now().strftime("%H:%M:%S")

        # Justificativa matemática do despacho
        if score_info is None:
            scores_map = self.calcular_scores_pool()
            score_info = scores_map.get(operador)

        if score_info:
            justificativa = (
                f"Menor Score: {score_info.score_total} pts | "
                f"Detalhe: {score_info.detalhe_formatado}"
            )
        else:
            justificativa = "Menor Score de Fila Ponderada"

        if simulacao:
            # Modo Simulação: registra em memória e adiciona +1 a_fazer (+peso_a_fazer pts)
            self._deltas_sessao[operador.email]["a_fazer"] += 1
            self._ultima_atribuicao_sessao[operador.email] = datetime.now()

            registro = AtribuicaoRegistro(
                horario=horario_str,
                codigo=codigo_str,
                cliente=cliente_str,
                assunto=assunto_str,
                operador_nome=operador.nome,
                operador_email=operador.email,
                score_justificativa=justificativa,
                status="[yellow]Simulado[/yellow]",
                simulacao=True,
            )
            self.historico_atribuicoes.insert(0, registro)
            if len(self.historico_atribuicoes) > 20:
                self.historico_atribuicoes.pop()
            return True

        # Modo Produção
        if creator_client is None:
            creator_client = MilvusTicketCreatorClient(settings=self.settings)

        identificador = chamado.id or chamado.codigo
        if not identificador:
            console.print(f"[bold red]Erro: Chamado sem ID ou Código válido para atribuição.[/bold red]")
            return False

        sucesso = await creator_client.atribuir_operador(
            chamado_codigo_ou_id=identificador,
            email_tecnico=operador.email,
        )

        status_fmt = "[green]Sucesso[/green]" if sucesso else "[red]Falha[/red]"
        registro = AtribuicaoRegistro(
            horario=horario_str,
            codigo=codigo_str,
            cliente=cliente_str,
            assunto=assunto_str,
            operador_nome=operador.nome,
            operador_email=operador.email,
            score_justificativa=justificativa,
            status=status_fmt,
            simulacao=False,
        )
        self.historico_atribuicoes.insert(0, registro)
        if len(self.historico_atribuicoes) > 20:
            self.historico_atribuicoes.pop()

        if sucesso:
            self._atualizar_chamado_duckdb(identificador, operador)
            self._ultima_atribuicao_sessao[operador.email] = datetime.now()

        return sucesso

    def _atualizar_chamado_duckdb(
        self,
        chamado_id_ou_codigo: str | int,
        operador: OperadorElegivel,
    ) -> None:
        """Persiste a atribuição do chamado no DuckDB local com status 'A fazer'."""
        val = str(chamado_id_ou_codigo).lstrip("#").strip()
        try:
            with self._obter_conexao(read_only=False) as con:
                con.execute(
                    """
                    UPDATE chamados
                    SET tecnico = ?,
                        email_tecnico = ?,
                        status = 'A fazer',
                        atualizado_em = now()
                    WHERE CAST(codigo AS VARCHAR) = ? OR CAST(id AS VARCHAR) = ?
                    """,
                    [operador.nome, operador.email, val, val],
                )
        except Exception as e:
            console.print(f"[dim yellow]Aviso ao atualizar chamado no DuckDB: {e}[/dim yellow]")
