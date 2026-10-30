"""A.N.A.K.I.N - Despachante Automático e Triagem com IA (Upgrade TI).

Script executável ao vivo para monitoramento contínuo, triagem autônoma via IA
e roteamento balanceado por prioridade/nível técnico (Tiered Dispatching).

Uso:
    python dispatcher_aovivo.py
    python dispatcher_aovivo.py --simulacao
    python dispatcher_aovivo.py -s
"""

from datetime import datetime
from pathlib import Path
import argparse
import asyncio
import sys
import time

# Adiciona o diretório src ao sys.path para garantir resolução dos módulos
SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Configuração de UTF-8 no Windows
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if sys.stdin and hasattr(sys.stdin, "reconfigure"):
    try:
        sys.stdin.reconfigure(encoding="utf-8")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from anakin.config.settings import Settings, get_settings
from anakin.dispatch.auto_dispatcher import (
    AutoDispatcher,
    OperadorElegivel,
    ScoreOperador,
    OPERADORES_ELEGIVEIS,
    POOL_SENIOR_N2_EMAILS,
    POOL_PLENO_N1_EMAILS,
    AtribuicaoRegistro,
)
from anakin.dispatch.ticket_triage_ai import TicketTriageAI, ResultadoTriagemIA
from anakin.domain.ticket_models import TicketSummary
from anakin.milvus.readonly_client import MilvusReadOnlyClient
from anakin.milvus.ticket_creator_client import MilvusTicketCreatorClient
from anakin.storage.local_cache import LocalCache
from anakin.web.telemetry import registrar_evento

console = Console()


def criar_tabela_pool(
    scores: dict[OperadorElegivel, ScoreOperador],
    proximo_operador: OperadorElegivel | None,
    settings: Settings,
) -> Table:
    """Gera a tabela do Pool Ativo com o Score de Ocupação Ponderado e indicação de Nível."""
    col_atendendo = f"Atendendo (x{settings.peso_status_atendendo})"
    col_a_fazer = f"A Fazer (x{settings.peso_status_a_fazer})"
    col_pausado = f"Pausado (x{settings.peso_status_pausado})"

    tb = Table(
        title="[bold white]Pool de Atendimento por Nível & Score de Carga (Upgrade TI)[/bold white]",
        box=box.ROUNDED,
        border_style="cyan",
        header_style="bold cyan",
        expand=True,
    )
    tb.add_column("Operador", style="bold white", ratio=3)
    tb.add_column("Nível / Fila", justify="center", style="dim cyan", ratio=2)
    tb.add_column(col_atendendo, justify="center", style="bold red", ratio=2)
    tb.add_column(col_a_fazer, justify="center", style="bold yellow", ratio=2)
    tb.add_column(col_pausado, justify="center", style="dim white", ratio=2)
    tb.add_column("Score Total", justify="center", style="bold cyan", ratio=2)
    tb.add_column("Status Fila", justify="center", ratio=3)

    for op in OPERADORES_ELEGIVEIS:
        sc = scores.get(op)
        if not sc:
            continue

        eh_proximo = proximo_operador is not None and op.email == proximo_operador.email

        # Nível técnico do pool
        if op.email.lower() in POOL_SENIOR_N2_EMAILS:
            nivel_badge = "[bold magenta]Sênior / N2[/bold magenta]"
        else:
            nivel_badge = "[cyan]Pleno / N1[/cyan]"

        # Formatação de cores e badges do Score
        if sc.score_total == 0:
            badge_score = f"[bold green]{sc.score_total} pts[/bold green]"
        elif sc.score_total <= 12:
            badge_score = f"[green]{sc.score_total} pts[/green]"
        elif sc.score_total <= 18:
            badge_score = f"[yellow]{sc.score_total} pts[/yellow]"
        else:
            badge_score = f"[bold red]{sc.score_total} pts[/bold red]"

        if eh_proximo:
            status_txt = "[bold green]★ [PRÓXIMO RECEPTOR][/bold green]"
            estilo_linha = "bold"
        else:
            status_txt = "[dim green]✓ Elegível[/dim green]"
            estilo_linha = ""

        nome_fmt = f"[{estilo_linha}]{op.nome}[/{estilo_linha}]" if estilo_linha else op.nome
        tb.add_row(
            nome_fmt,
            nivel_badge,
            str(sc.qtd_atendendo),
            str(sc.qtd_a_fazer),
            str(sc.qtd_pausado),
            badge_score,
            status_txt,
        )

    return tb


def criar_tabela_historico(historico: list[AtribuicaoRegistro]) -> Table:
    """Gera a tabela das últimas atribuições realizadas na sessão."""
    tb = Table(
        title="[bold white]Últimas Triagens e Atribuições Realizadas (Sessão Atual)[/bold white]",
        box=box.ROUNDED,
        border_style="dim white",
        header_style="bold magenta",
        expand=True,
    )
    tb.add_column("Horário", justify="center", style="dim", width=9)
    tb.add_column("#Código", justify="center", style="bold cyan", width=10)
    tb.add_column("Cliente", style="yellow", ratio=2)
    tb.add_column("Operador Atribuído", style="bold green", ratio=2)
    tb.add_column("Resumo da Triagem & Justificativa", style="dim cyan", ratio=4)
    tb.add_column("Status", justify="center", width=11)

    if not historico:
        tb.add_row(
            "-",
            "-",
            "[dim italic]Nenhum chamado pendente triado nesta sessão ainda.[/dim italic]",
            "-",
            "[dim italic]Aguardando novos chamados para triagem autônoma com IA...[/dim italic]",
            "[dim]-[/dim]",
        )
    else:
        for reg in historico[:8]:
            tb.add_row(
                reg.horario,
                f"#{reg.codigo}",
                reg.cliente[:24] + ("..." if len(reg.cliente) > 24 else ""),
                reg.operador_nome,
                reg.score_justificativa,
                reg.status,
            )

    return tb


def gerar_interface(
    scores: dict[OperadorElegivel, ScoreOperador],
    proximo_operador: OperadorElegivel | None,
    historico: list[AtribuicaoRegistro],
    segundos_restantes: int,
    intervalo_total: int,
    status_atual: str,
    modo_simulacao: bool,
    settings: Settings,
    ultimo_evento: str | None = None,
) -> Group:
    """Monta a interface completa em tempo real para o rich.live.Live."""
    modo_cor = "bold yellow" if modo_simulacao else "bold green"
    modo_txt = "MODO SIMULAÇÃO (Sem alterações reais no Milvus)" if modo_simulacao else "PRODUÇÃO AO VIVO (Alterações Ativas)"
    hora_str = datetime.now().strftime("%H:%M:%S")

    header_text = Text()
    header_text.append("A.N.A.K.I.N - Triagem Autônoma com IA e Roteamento por Nível (Upgrade TI)\n", style="bold cyan")
    header_text.append("Roteamento: ", style="dim")
    header_text.append("Crítico/Alto -> Sênior (N2) | Médio/Baixo -> Pleno (N1) | Score Ponderado | Rate Limit 62s\n", style="white")
    header_text.append(f"Horário: {hora_str} | Status: ", style="dim")
    header_text.append(f"[{modo_txt}]", style=modo_cor)

    panel_header = Panel(
        header_text,
        box=box.DOUBLE,
        border_style="cyan" if not modo_simulacao else "yellow",
        padding=(0, 2),
    )

    largura_barra = 28
    progresso = max(0.0, min(1.0, 1.0 - (segundos_restantes / max(1, intervalo_total))))
    preenchido = int(progresso * largura_barra)
    barra_str = "█" * preenchido + "░" * (largura_barra - preenchido)

    cronometro_text = Text()
    if segundos_restantes > 0:
        cronometro_text.append("⏳ Próxima checagem no Milvus em: ", style="bold white")
        cronometro_text.append(f"{segundos_restantes:02d}s ", style="bold yellow")
        cronometro_text.append(f"[{barra_str}] ", style="cyan")
        cronometro_text.append(f"• {status_atual}", style="dim")
    else:
        cronometro_text.append("⚡ ", style="bold yellow")
        cronometro_text.append(f"{status_atual}", style="bold green")

    panel_cronometro = Panel(
        cronometro_text,
        box=box.ROUNDED,
        border_style="blue",
        padding=(0, 1),
    )

    tabela_pool = criar_tabela_pool(scores, proximo_operador, settings)
    tabela_historico = criar_tabela_historico(historico)

    elementos = [panel_header, panel_cronometro, tabela_pool, tabela_historico]
    if ultimo_evento:
        try:
            conteudo_alerta = Text.from_markup(ultimo_evento)
        except Exception:
            conteudo_alerta = Text(ultimo_evento, style="bold white")

        panel_alerta = Panel(
            conteudo_alerta,
            title="[bold yellow]🔔 Evento de Triagem e Despacho[/bold yellow]",
            border_style="yellow",
            padding=(0, 1),
        )
        elementos.insert(2, panel_alerta)

    return Group(*elementos)


def criar_quadro_contador(segundos_restantes: int) -> Panel:
    """Gera um quadro simples e compacto apenas com o número dos segundos contando."""
    texto = Text(justify="center")
    texto.append("⏳ Próxima checagem no Milvus em: ", style="bold white")
    texto.append(f"{segundos_restantes:02d}s", style="bold yellow")
    return Panel(
        texto,
        box=box.ROUNDED,
        border_style="cyan",
        padding=(0, 2),
        width=48,
    )


async def ciclo_despachante(
    simulacao: bool = False,
    intervalo_segundos: int = 62,
) -> None:
    """Executa o loop contínuo de monitoramento, triagem com IA e despacho por prioridade."""
    settings = get_settings()
    cache = LocalCache(settings=settings)
    dispatcher = AutoDispatcher(settings=settings, cache=cache)
    triage_ai = TicketTriageAI(settings=settings, cache=cache)
    readonly_client = MilvusReadOnlyClient(settings=settings)
    creator_client = MilvusTicketCreatorClient(settings=settings)

    # Carrega catálogo homologado do DuckDB
    catalogo = cache.obter_catalogo_categorias_e_tipos()

    modo_cor = "bold yellow" if simulacao else "bold green"
    modo_txt = "MODO SIMULAÇÃO (Sem alterações reais no Milvus)" if simulacao else "PRODUÇÃO AO VIVO (Alterações Ativas)"
    hora_str = datetime.now().strftime("%H:%M:%S")

    header_text = Text()
    header_text.append("A.N.A.K.I.N - Despachante Automático de Chamados (Upgrade TI)\n", style="bold cyan")
    header_text.append("Roteamento: ", style="dim")
    header_text.append("Crítico/Alto -> Sênior (N2) | Médio/Baixo -> Pleno (N1) | Score Ponderado | Rate Limit 62s\n", style="white")
    header_text.append(f"Horário: {hora_str} | Status: [{modo_txt}]", style="dim")

    console.print(
        Panel(
            header_text,
            box=box.DOUBLE,
            border_style="cyan" if not simulacao else "yellow",
            padding=(0, 2),
        )
    )

    # Calibração inicial rápida dos chamados abertos (status=9)
    try:
        console.print("[dim]Calibrando chamados em aberto no Milvus (status=9)...[/dim]")
        tickets_iniciais = await readonly_client.listar_chamados(status=9, total_registros=100, pagina=1)
        if tickets_iniciais:
            cache.salvar_chamados(tickets_iniciais)
            cache.sincronizar_status_abertos([t.codigo for t in tickets_iniciais if t.codigo])
    except Exception as e:
        console.print(f"[dim yellow]Aviso de sincronização inicial ({e}). Prosseguindo com dados do DuckDB...[/dim yellow]")

    scores = dispatcher.calcular_scores_pool()
    proximo_op, score_info, _ = dispatcher.selecionar_operador_menor_score()
    console.print(criar_tabela_pool(scores, proximo_op, settings))

    # Loop principal contínuo
    while True:
        # 1. Contagem regressiva em quadro compacto e isolado (sem pular linhas)
        with Live(
            criar_quadro_contador(intervalo_segundos),
            console=console,
            refresh_per_second=2,
            transient=True,
        ) as live_contador:
            for s in range(intervalo_segundos, 0, -1):
                live_contador.update(criar_quadro_contador(s))
                await asyncio.sleep(1)

        # 2. Executa a checagem no Milvus (POST /chamado/listagem com status=1 "A fazer")
        console.print("[dim cyan]⚡ Consultando chamados 'A fazer' no Milvus (POST /chamado/listagem, status=1)...[/dim cyan]")
        novos_pendentes: list[TicketSummary] = []
        try:
            tickets_a_fazer = await readonly_client.listar_chamados(status=1, total_registros=50, pagina=1)
            if tickets_a_fazer:
                cache.salvar_chamados(tickets_a_fazer)
                novos_pendentes = dispatcher.identificar_chamados_pendentes(tickets_a_fazer)

            if not novos_pendentes:
                tickets_abertos = await readonly_client.listar_chamados(status=9, total_registros=50, pagina=1)
                if tickets_abertos:
                    cache.salvar_chamados(tickets_abertos)
                    novos_pendentes = dispatcher.identificar_chamados_pendentes(tickets_abertos)
        except Exception as e:
            console.print(f"[yellow]Alerta na consulta Milvus: {e}[/yellow]")
            novos_pendentes = []

        # 3. Processa cada chamado pendente encontrado com Triagem IA e Roteamento por Nível
        if novos_pendentes:
            for chamado in novos_pendentes:
                cod = str(chamado.codigo or chamado.id or "Sem código")
                console.print(f"[bold cyan]🔍 Executando Triagem com IA para o Chamado #{cod}...[/bold cyan]")

                # Triagem técnica inteligente com IA
                triagem = triage_ai.triar_chamado(chamado, catalogo=catalogo)

                # Roteamento por Nível de Prioridade (Tiered Dispatching)
                op_escolhido, sc_escolhido, _, tag_pool = dispatcher.selecionar_operador_por_prioridade(
                    triagem.prioridade
                )

                log_triagem = (
                    f"✔ Triagem #{cod}: Prioridade: [{triagem.prioridade}] | "
                    f"Mesa: [{op_escolhido.nome}] | Setor: [{op_escolhido.nome}] | "
                    f"Operador: [{op_escolhido.nome} ({op_escolhido.email})]"
                )

                console.print(f"[dim]Atribuindo Chamado #{cod} ({op_escolhido.nome} - {tag_pool})...[/dim]")

                # Executa a atribuição completa no Milvus e no DuckDB
                sucesso = await dispatcher.despachar_chamado_triado(
                    chamado=chamado,
                    operador=op_escolhido,
                    triagem=triagem,
                    score_info=sc_escolhido,
                    tag_pool=tag_pool,
                    creator_client=creator_client,
                    simulacao=simulacao,
                )

                tag_modo = "[SIMULAÇÃO]" if simulacao else "[PRODUÇÃO]"
                if sucesso:
                    msg_sucesso = f"[bold green]{tag_modo} {log_triagem}[/bold green]"
                    console.print(f"\n{msg_sucesso}")
                    try:
                        registrar_evento(
                            origem="DISPATCHER",
                            nivel="SUCCESS",
                            mensagem=f"{tag_modo} Chamado #{cod} -> {op_escolhido.nome} ({tag_pool}): {getattr(triagem, 'justificativa_prioridade', '')}",
                        )
                    except Exception:
                        pass
                else:
                    msg_falha = f"[bold red]✗ Falha ao atribuir Chamado #{cod} para {op_escolhido.nome}.[/bold red]"
                    console.print(f"\n{msg_falha}")
                    try:
                        registrar_evento(
                            origem="DISPATCHER",
                            nivel="ERROR",
                            mensagem=f"{tag_modo} Falha ao atribuir Chamado #{cod} para {op_escolhido.nome}.",
                        )
                    except Exception:
                        pass

            # Exibe a tabela atualizada do pool após atribuições
            scores = dispatcher.calcular_scores_pool()
            proximo_op, score_info, _ = dispatcher.selecionar_operador_menor_score()
            console.print(criar_tabela_pool(scores, proximo_op, settings))
        else:
            console.print("[dim green]✓ Nenhum chamado pendente no momento. Fila balanceada.[/dim green]")
            scores = dispatcher.calcular_scores_pool()
            proximo_op, score_info, _ = dispatcher.selecionar_operador_menor_score()


def main() -> None:
    """Ponto de entrada do script com tratamento de argumentos CLI."""
    parser = argparse.ArgumentParser(
        description="A.N.A.K.I.N - Despachante Automático e Triagem com IA (Upgrade TI)",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "-s",
        "--simulacao",
        action="store_true",
        help="Executa em modo de simulação (avalia, tria e exibe atribuições sem alterar dados no Milvus).",
    )
    parser.add_argument(
        "--intervalo",
        type=int,
        default=62,
        help="Intervalo em segundos entre checagens na API do Milvus (padrão: 62s para rate limit estrito).",
    )

    args = parser.parse_args()

    try:
        asyncio.run(
            ciclo_despachante(
                simulacao=args.simulacao,
                intervalo_segundos=args.intervalo,
            )
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        console.print("\n[bold yellow]Despachante Automático encerrado com segurança pelo operador.[/bold yellow]\n")
    except Exception as e:
        console.print(f"\n[bold red]Erro fatal na execução do despachante:[/bold red] {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
