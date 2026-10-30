"""Worker Assíncrono do Despachante A.N.A.K.I.N (FastAPI Background Task).

Controla o ciclo de vida da triagem por IA e do despacho contínuo em processo único,
emitindo telemetria e contagem regressiva em tempo real para o barramento SSE do HUD Web.
"""

from datetime import datetime
from typing import Any, Optional
import asyncio
from rich.console import Console

from anakin.config.settings import Settings, get_settings
from anakin.dispatch.auto_dispatcher import AutoDispatcher, ScoreOperador, OperadorElegivel
from anakin.dispatch.ticket_triage_ai import TicketTriageAI
from anakin.domain.ticket_models import TicketSummary
from anakin.milvus.readonly_client import MilvusReadOnlyClient
from anakin.milvus.ticket_creator_client import MilvusTicketCreatorClient
from anakin.storage.local_cache import LocalCache
from anakin.web.telemetry import registrar_evento, telemetry

console = Console()


class DispatcherWorker:
    """Worker de despacho automático controlado por ciclo de vida assíncrono."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        simulacao: bool = False,
        intervalo_segundos: int = 62,
    ) -> None:
        self.settings: Settings = settings or get_settings()
        self.modo_simulacao: bool = simulacao
        self.intervalo_segundos: int = intervalo_segundos
        self.segundos_restantes: int = intervalo_segundos

        self.is_running: bool = False
        self.is_paused: bool = False
        self.ultimo_chamado_triado: Optional[dict[str, Any]] = None

        self._task: Optional[asyncio.Task[None]] = None
        self._pause_event: asyncio.Event = asyncio.Event()
        self._pause_event.set()

        # Componentes do motor de despacho
        self.cache: LocalCache = LocalCache(settings=self.settings)
        self.dispatcher: AutoDispatcher = AutoDispatcher(settings=self.settings, cache=self.cache)
        self.triage_ai: TicketTriageAI = TicketTriageAI(settings=self.settings, cache=self.cache)
        self.readonly_client: MilvusReadOnlyClient = MilvusReadOnlyClient(settings=self.settings)
        self.creator_client: MilvusTicketCreatorClient = MilvusTicketCreatorClient(settings=self.settings)

    def pause(self) -> None:
        """Pausa a triagem e atribuição de chamados mantendo o worker ativo."""
        self.is_paused = True
        self._pause_event.clear()
        registrar_evento(
            origem="DISPATCHER",
            nivel="WARN",
            mensagem="Motor de Despacho pausado pelo operador.",
            detalhes={"tipo": "controle", "acao": "pause"},
        )
        console.print("[bold yellow][DISPATCHER WORKER][/bold yellow] Fila de despacho pausada.")

    def resume(self) -> None:
        """Retoma o fluxo contínuo de despacho de chamados."""
        self.is_paused = False
        self._pause_event.set()
        registrar_evento(
            origem="DISPATCHER",
            nivel="SUCCESS",
            mensagem="Motor de Despacho retomado pelo operador.",
            detalhes={"tipo": "controle", "acao": "resume"},
        )
        console.print("[bold green][DISPATCHER WORKER][/bold green] Fila de despacho retomada.")

    def get_status(self) -> dict[str, Any]:
        """Retorna o estado operacional consolidado do worker."""
        return {
            "is_running": self.is_running,
            "is_paused": self.is_paused,
            "rodando": self.is_running,
            "pausado": self.is_paused,
            "segundos_restantes": self.segundos_restantes,
            "intervalo_segundos": self.intervalo_segundos,
            "modo_simulacao": self.modo_simulacao,
            "ultimo_chamado_triado": self.ultimo_chamado_triado,
        }

    async def _calibracao_inicial(self) -> None:
        """Executa sincronização inicial rápida dos chamados abertos (status=9) no Milvus."""
        try:
            console.print("[dim cyan][DISPATCHER WORKER] Calibrando chamados em aberto no Milvus (status=9)...[/dim cyan]")
            tickets_iniciais = await self.readonly_client.listar_chamados(status=9, total_registros=100, pagina=1)
            if tickets_iniciais:
                self.cache.salvar_chamados(tickets_iniciais)
                self.cache.sincronizar_status_abertos([t.codigo for t in tickets_iniciais if t.codigo])
                console.print(f"[dim green]✓ Calibração concluída: {len(tickets_iniciais)} chamados sincronizados.[/dim green]")
        except Exception as e:
            console.print(f"[dim yellow]Aviso de calibração inicial ({e}). Prosseguindo com dados do DuckDB...[/dim yellow]")

    async def start(self) -> None:
        """Ponto de entrada do loop assíncrono do despachante."""
        if self._task is None:
            self._task = asyncio.current_task()

        self.is_running = True
        self._pause_event.set()

        tag_modo = "[SIMULAÇÃO]" if self.modo_simulacao else "[PRODUÇÃO AO VIVO]"
        console.print(f"[bold cyan]🛸 A.N.A.K.I.N Worker Iniciado {tag_modo} (Intervalo: {self.intervalo_segundos}s)[/bold cyan]")

        # Calibração inicial
        await self._calibracao_inicial()

        try:
            while self.is_running:
                await self._executar_ciclo()
        except asyncio.CancelledError:
            console.print("[yellow][DISPATCHER WORKER] Tarefa assíncrona cancelada graciosamente.[/yellow]")
        except Exception as e:
            registrar_evento(
                origem="DISPATCHER",
                nivel="ERROR",
                mensagem=f"Erro não tratado no worker de despacho: {e}",
                detalhes={"erro": str(e)},
            )
            console.print(f"[bold red][DISPATCHER WORKER ERRO FATAL]:[/bold red] {e}")
        finally:
            self.is_running = False

    async def stop(self) -> None:
        """Cancela a task graciosamente e fecha os clientes HTTP."""
        self.is_running = False
        self._pause_event.set()

        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

        try:
            await self.creator_client.close()
        except Exception:
            pass

        try:
            await self.readonly_client.close()
        except Exception:
            pass

        console.print("[bold yellow][DISPATCHER WORKER] Conexões HTTP e tarefas encerradas com segurança.[/bold yellow]")

    async def _executar_ciclo(self) -> None:
        """Executa um ciclo completo de contagem regressiva, consulta, triagem e despacho."""
        # Se pausado, aguarda liberação
        while self.is_paused and self.is_running:
            await asyncio.sleep(0.5)

        if not self.is_running:
            return

        # 1. Contagem regressiva segundo a segundo emitida via SSE
        for s in range(self.intervalo_segundos, 0, -1):
            if not self.is_running:
                return

            while self.is_paused and self.is_running:
                await asyncio.sleep(0.5)

            if not self.is_running:
                return

            self.segundos_restantes = s
            registrar_evento(
                origem="DISPATCHER",
                nivel="DEBUG",
                mensagem=f"Contagem regressiva: {s}s",
                detalhes={"tipo": "countdown", "segundos": s, "total": self.intervalo_segundos},
            )
            await asyncio.sleep(1)

        if not self.is_running:
            return

        # 2. Marca 0s no cronômetro e emite evento visual de busca
        self.segundos_restantes = 0
        registrar_evento(
            origem="DISPATCHER",
            nivel="DEBUG",
            mensagem="Buscando chamados pendentes no Milvus...",
            detalhes={"tipo": "countdown", "segundos": 0, "total": self.intervalo_segundos, "status": "buscando"},
        )

        # 3. Consulta chamados no Milvus
        novos_pendentes: list[TicketSummary] = []
        try:
            tickets_a_fazer = await self.readonly_client.listar_chamados(status=1, total_registros=50, pagina=1)
            if tickets_a_fazer:
                self.cache.salvar_chamados(tickets_a_fazer)
                novos_pendentes = self.dispatcher.identificar_chamados_pendentes(tickets_a_fazer)

            if not novos_pendentes:
                tickets_abertos = await self.readonly_client.listar_chamados(status=9, total_registros=50, pagina=1)
                if tickets_abertos:
                    self.cache.salvar_chamados(tickets_abertos)
                    novos_pendentes = self.dispatcher.identificar_chamados_pendentes(tickets_abertos)
        except Exception as e:
            registrar_evento(
                origem="DISPATCHER",
                nivel="ERROR",
                mensagem=f"Alerta na consulta Milvus: {e}",
                detalhes={"erro": str(e)},
            )
            novos_pendentes = []

        # 4. Processa novos chamados encontrados com Triagem IA e Despacho
        if novos_pendentes:
            catalogo = self.cache.obter_catalogo_categorias_e_tipos()
            tag_modo = "[SIMULAÇÃO]" if self.modo_simulacao else "[PRODUÇÃO]"

            for chamado in novos_pendentes:
                if not self.is_running:
                    break

                cod = str(chamado.codigo or chamado.id or "Sem código")
                try:
                    # Triagem IA
                    triagem = self.triage_ai.triar_chamado(chamado, catalogo=catalogo)

                    # Roteamento por Nível de Prioridade (Tiered Dispatching)
                    op_escolhido, sc_escolhido, _, tag_pool = self.dispatcher.selecionar_operador_por_prioridade(
                        triagem.prioridade
                    )

                    # Atribuição
                    sucesso = await self.dispatcher.despachar_chamado_triado(
                        chamado=chamado,
                        operador=op_escolhido,
                        triagem=triagem,
                        score_info=sc_escolhido,
                        tag_pool=tag_pool,
                        creator_client=self.creator_client,
                        simulacao=self.modo_simulacao,
                    )

                    if sucesso:
                        self.ultimo_chamado_triado = {
                            "codigo": cod,
                            "operador": op_escolhido.nome,
                            "email": op_escolhido.email,
                            "prioridade": triagem.prioridade,
                            "tag_pool": tag_pool,
                            "horario": datetime.now().strftime("%H:%M:%S"),
                        }
                        msg = f"{tag_modo} Chamado #{cod} atribuído a {op_escolhido.nome} ({tag_pool})"
                        console.print(f"[bold green]✔ {msg}[/bold green]")
                        registrar_evento(
                            origem="DISPATCHER",
                            nivel="SUCCESS",
                            mensagem=msg,
                            detalhes={
                                "codigo": cod,
                                "operador": op_escolhido.nome,
                                "prioridade": triagem.prioridade,
                                "tag_pool": tag_pool,
                            },
                        )
                    else:
                        msg_falha = f"{tag_modo} Falha ao atribuir Chamado #{cod} para {op_escolhido.nome}."
                        console.print(f"[bold red]✗ {msg_falha}[/bold red]")
                        registrar_evento(
                            origem="DISPATCHER",
                            nivel="ERROR",
                            mensagem=msg_falha,
                            detalhes={"codigo": cod, "operador": op_escolhido.nome},
                        )

                except Exception as e:
                    registrar_evento(
                        origem="DISPATCHER",
                        nivel="ERROR",
                        mensagem=f"Erro durante triagem/despacho do chamado #{cod}: {e}",
                        detalhes={"codigo": cod, "erro": str(e)},
                    )
                    console.print(f"[bold red]Erro no chamado #{cod}:[/bold red] {e}")

