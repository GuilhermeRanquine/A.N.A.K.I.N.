"""A.N.A.K.I.N - Despachante Automático de Chamados (Upgrade TI).

Script executável ao vivo para monitoramento contínuo e distribuição automática
de chamados sem técnico, utilizando o Algoritmo de Fila Ponderada por Carga (Score de Ocupação).

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
    AtribuicaoRegistro,
)
from anakin.domain.ticket_models import TicketSummary
from anakin.milvus.readonly_client import MilvusReadOnlyClient
from anakin.milvus.ticket_creator_client import MilvusTicketCreatorClient
from anakin.storage.local_cache import LocalCache

console = Console()


def criar_tabela_pool(
    scores: dict[OperadorElegivel, ScoreOperador],
    proximo_operador: OperadorElegivel | None,
    settings: Settings,
) -> Table:
    """Gera a tabela do Pool Ativo com o Score de Ocupação Ponderado."""
    col_atendendo = f"Atendendo (x{settings.peso_status_atendendo})"
    col_a_fazer = f"A Fazer (x{settings.peso_status_a_fazer})"
    col_pausado = f"Pausado (x{settings.peso_status_pausado})"

    tb = Table(
        title="[bold white]Pool Oficial de Distribuição Ponderada (Upgrade TI - 6 Operadores)[/bold white]",
        box=box.ROUNDED,
        border_style="cyan",
        header_style="bold cyan",
        expand=True,
    )
    tb.add_column("Operador", style="bold white", ratio=3)
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
        title="[bold white]Últimas Atribuições Realizadas (Sessão Atual)[/bold white]",
        box=box.ROUNDED,
        border_style="dim white",
        header_style="bold magenta",
        expand=True,
    )
    tb.add_column("Horário", justify="center", style="dim", width=9)
    tb.add_column("#Código", justify="center", style="bold cyan", width=10)
    tb.add_column("Cliente", style="yellow", ratio=2)
    tb.add_column("Operador Atribuído", style="bold green", ratio=2)
    tb.add_column("Justificativa Matemática", style="dim cyan", ratio=3)
    tb.add_column("Status", justify="center", width=11)

    if not historico:
        tb.add_row(
            "-",
            "-",
            "[dim italic]Nenhum chamado pendente atribuído nesta sessão ainda.[/dim italic]",
            "-",
            "[dim italic]Aguardando novos chamados sem técnico...[/dim italic]",
            "[dim]-[/dim]",
        )
    else:
        for reg in historico[:8]:
            tb.add_row(
                reg.horario,
                f"#{reg.codigo}",
                reg.cliente[:28] + ("..." if len(reg.cliente) > 28 else ""),
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
    # 1. Cabeçalho Principal
    modo_cor = "bold yellow" if modo_simulacao else "bold green"
    modo_txt = "MODO SIMULAÇÃO (Sem alterações reais no Milvus)" if modo_simulacao else "PRODUÇÃO AO VIVO (Alterações Ativas)"
    hora_str = datetime.now().strftime("%H:%M:%S")

    pesos_str = (
        f"Atendendo={settings.peso_status_atendendo}x | "
        f"A Fazer={settings.peso_status_a_fazer}x | "
        f"Pausado={settings.peso_status_pausado}x"
    )

    header_text = Text()
    header_text.append("A.N.A.K.I.N - Despachante Automático de Chamados (Upgrade TI)\n", style="bold cyan")
    header_text.append("Algoritmo: ", style="dim")
    header_text.append(f"Fila Ponderada por Carga (Score de Ocupação: {pesos_str}) | Rate Limit 62s\n", style="white")
    header_text.append(f"Horário: {hora_str} | Status: ", style="dim")
    header_text.append(f"[{modo_txt}]", style=modo_cor)

    panel_header = Panel(
        header_text,
        box=box.DOUBLE,
        border_style="cyan" if not modo_simulacao else "yellow",
        padding=(0, 2),
    )

    # 2. Barra de Progresso / Cronômetro Visual
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

    # 3. Tabela do Pool Ativo
    tabela_pool = criar_tabela_pool(scores, proximo_operador, settings)

    # 4. Tabela de Histórico
    tabela_historico = criar_tabela_historico(historico)

    # 5. Painel de Notificação em Destaque (se houver evento recente)
    elementos = [panel_header, panel_cronometro, tabela_pool, tabela_historico]
    if ultimo_evento:
        panel_alerta = Panel(
            Text(ultimo_evento, style="bold white"),
            title="[bold yellow]🔔 Notificação de Despacho Ponderado[/bold yellow]",
            border_style="yellow",
            padding=(0, 1),
        )
        elementos.insert(2, panel_alerta)

    return Group(*elementos)


async def ciclo_despachante(
    simulacao: bool = False,
    intervalo_segundos: int = 62,
) -> None:
    """Executa o loop contínuo de monitoramento e despacho automático ponderado."""
    settings = get_settings()
    cache = LocalCache(settings=settings)
    dispatcher = AutoDispatcher(settings=settings, cache=cache)
    readonly_client = MilvusReadOnlyClient(settings=settings)
    creator_client = MilvusTicketCreatorClient(settings=settings)

    status_atual = "Inicializando sincronização do banco local..."
    ultimo_evento: str | None = None
    scores = dispatcher.calcular_scores_pool()
    proximo_op, score_info, _ = dispatcher.selecionar_operador_menor_score()

    render_group = gerar_interface(
        scores=scores,
        proximo_operador=proximo_op,
        historico=dispatcher.historico_atribuicoes,
        segundos_restantes=intervalo_segundos,
        intervalo_total=intervalo_segundos,
        status_atual=status_atual,
        modo_simulacao=simulacao,
        settings=settings,
        ultimo_evento=ultimo_evento,
    )

    with Live(render_group, console=console, refresh_per_second=4, screen=False) as live:
        # Sincronização inicial rápida dos chamados abertos (status=9) para calibrar cargas reais
        try:
            status_atual = "Calibrando chamados em aberto no Milvus (status=9)..."
            live.update(
                gerar_interface(
                    scores=scores,
                    proximo_operador=proximo_op,
                    historico=dispatcher.historico_atribuicoes,
                    segundos_restantes=0,
                    intervalo_total=intervalo_segundos,
                    status_atual=status_atual,
                    modo_simulacao=simulacao,
                    settings=settings,
                    ultimo_evento=ultimo_evento,
                )
            )
            tickets_iniciais = await readonly_client.listar_chamados(status=9, total_registros=100, pagina=1)
            if tickets_iniciais:
                cache.salvar_chamados(tickets_iniciais)
                cache.sincronizar_status_abertos([t.codigo for t in tickets_iniciais if t.codigo])
            scores = dispatcher.calcular_scores_pool()
            proximo_op, score_info, _ = dispatcher.selecionar_operador_menor_score()
        except Exception as e:
            status_atual = f"Aviso de sincronização inicial ({e}). Prosseguindo com dados do DuckDB..."

        # Loop principal contínuo
        while True:
            # 1. Contagem regressiva segundo a segundo
            status_atual = "Aguardando próxima janela oficial de verificação..."
            for s in range(intervalo_segundos, 0, -1):
                live.update(
                    gerar_interface(
                        scores=scores,
                        proximo_operador=proximo_op,
                        historico=dispatcher.historico_atribuicoes,
                        segundos_restantes=s,
                        intervalo_total=intervalo_segundos,
                        status_atual=status_atual,
                        modo_simulacao=simulacao,
                        settings=settings,
                        ultimo_evento=ultimo_evento,
                    )
                )
                await asyncio.sleep(1)

            # 2. Executa a checagem no Milvus (POST /chamado/listagem com status=1 "A fazer")
            status_atual = "Consultando chamados 'A fazer' no Milvus (POST /chamado/listagem, status=1)..."
            live.update(
                gerar_interface(
                    scores=scores,
                    proximo_operador=proximo_op,
                    historico=dispatcher.historico_atribuicoes,
                    segundos_restantes=0,
                    intervalo_total=intervalo_segundos,
                    status_atual=status_atual,
                    modo_simulacao=simulacao,
                    settings=settings,
                    ultimo_evento=ultimo_evento,
                )
            )

            novos_pendentes: list[TicketSummary] = []
            try:
                tickets_a_fazer = await readonly_client.listar_chamados(status=1, total_registros=50, pagina=1)
                if tickets_a_fazer:
                    cache.salvar_chamados(tickets_a_fazer)
                    novos_pendentes = dispatcher.identificar_chamados_pendentes(tickets_a_fazer)

                # Se não houver chamados com status=1 sem técnico, checa se há chamados sem técnico no lote geral aberto
                if not novos_pendentes:
                    status_atual = "Verificando se há chamados gerais sem técnico atribuído..."
                    live.update(
                        gerar_interface(
                            scores=scores,
                            proximo_operador=proximo_op,
                            historico=dispatcher.historico_atribuicoes,
                            segundos_restantes=0,
                            intervalo_total=intervalo_segundos,
                            status_atual=status_atual,
                            modo_simulacao=simulacao,
                            settings=settings,
                            ultimo_evento=ultimo_evento,
                        )
                    )
                    tickets_abertos = await readonly_client.listar_chamados(status=9, total_registros=50, pagina=1)
                    if tickets_abertos:
                        cache.salvar_chamados(tickets_abertos)
                        novos_pendentes = dispatcher.identificar_chamados_pendentes(tickets_abertos)

            except Exception as e:
                ultimo_evento = f"[yellow]Alerta na consulta Milvus: {e}[/yellow]"
                novos_pendentes = []

            # 3. Processa cada chamado pendente encontrado
            if novos_pendentes:
                for chamado in novos_pendentes:
                    cod = str(chamado.codigo or chamado.id or "Sem código")
                    cli = str(chamado.cliente or "Sem cliente")

                    # Seleciona operador de menor Score Ponderado com desempate
                    op_escolhido, sc_escolhido, _ = dispatcher.selecionar_operador_menor_score()

                    status_atual = f"Atribuindo Chamado #{cod} ao operador {op_escolhido.nome}..."
                    ultimo_evento = (
                        f"✔ Chamado #{cod} atribuído a [bold green]{op_escolhido.nome}[/bold green] "
                        f"(Menor Score: {sc_escolhido.score_total} pts | Detalhe: {sc_escolhido.detalhe_formatado})"
                    )

                    live.update(
                        gerar_interface(
                            scores=dispatcher.calcular_scores_pool(),
                            proximo_operador=op_escolhido,
                            historico=dispatcher.historico_atribuicoes,
                            segundos_restantes=0,
                            intervalo_total=intervalo_segundos,
                            status_atual=status_atual,
                            modo_simulacao=simulacao,
                            settings=settings,
                            ultimo_evento=ultimo_evento,
                        )
                    )

                    # Executa a atribuição real ou simulada
                    sucesso = await dispatcher.despachar_chamado(
                        chamado=chamado,
                        operador=op_escolhido,
                        score_info=sc_escolhido,
                        creator_client=creator_client,
                        simulacao=simulacao,
                    )

                    if sucesso:
                        tag_modo = "[SIMULAÇÃO]" if simulacao else "[PRODUÇÃO]"
                        ultimo_evento = (
                            f"✔ {tag_modo} Chamado #{cod} atribuído a [bold green]{op_escolhido.nome}[/bold green] "
                            f"(Menor Score: {sc_escolhido.score_total} pts | Detalhe: {sc_escolhido.detalhe_formatado})"
                        )
                    else:
                        ultimo_evento = f"[bold red]✗ Falha ao atribuir Chamado #{cod} para {op_escolhido.nome}.[/bold red]"

                    # Recalcula scores para o próximo chamado
                    scores = dispatcher.calcular_scores_pool()
                    proximo_op, score_info, _ = dispatcher.selecionar_operador_menor_score()
            else:
                ultimo_evento = None
                scores = dispatcher.calcular_scores_pool()
                proximo_op, score_info, _ = dispatcher.selecionar_operador_menor_score()


def main() -> None:
    """Ponto de entrada do script com tratamento de argumentos CLI."""
    parser = argparse.ArgumentParser(
        description="A.N.A.K.I.N - Despachante Automático de Chamados (Upgrade TI)",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "-s",
        "--simulacao",
        action="store_true",
        help="Executa em modo de simulação (avalia e exibe atribuições sem alterar nada no Milvus).",
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
