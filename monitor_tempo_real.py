"""Script principal de monitoramento e análise cirúrgica em tempo real (Passo 4).

Mantém estrita trava de Somente Leitura (Read-Only), atualiza métricas cirúrgicas
com Pandas e SciPy, gera painel visual com Matplotlib/Seaborn e exibe resumos no terminal.
"""

from datetime import datetime
from pathlib import Path
import asyncio
import sys
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Adiciona o diretório src ao sys.path para garantir resolução dos módulos
SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Configuração de UTF-8 no Windows
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stdin and hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from anakin.analytics.realtime_plotter import RealTimeDashboardPlotter
from anakin.analytics.surgical_metrics import AnalyticsParams, TicketAnalyticsEngine
from anakin.config.settings import get_settings
from anakin.milvus.readonly_client import MilvusReadOnlyClient, ReadOnlySafetyError
from anakin.storage.local_cache import LocalCache

console = Console()


async def ciclo_monitor(
    client: MilvusReadOnlyClient,
    cache: LocalCache,
    plotter: RealTimeDashboardPlotter,
    ciclo_num: int,
) -> None:
    """Executa um ciclo completo de consulta, sincronização, análise e plotagem."""
    horario_str = datetime.now().strftime("%H:%M:%S")
    console.print(f"\n[bold cyan]═══ Ciclo #{ciclo_num} [{horario_str}] ═══[/bold cyan]")

    tickets_abertos = []
    # 1. Consulta segura à API do Milvus com fallback resiliente para o DuckDB
    try:
        with console.status("[bold green]Consultando chamados abertos no Milvus (status=9)...[/bold green]"):
            tickets_abertos = await client.listar_chamados(status=9, total_registros=200, pagina=1)

        if tickets_abertos:
            qtd_salva = cache.salvar_chamados(tickets_abertos)
            finalizados = cache.sincronizar_status_abertos([t.codigo for t in tickets_abertos if t.codigo])
            if finalizados > 0:
                console.print(f"[dim green]✓ {qtd_salva} chamados abertos salvos e {finalizados} finalizados atualizados no DuckDB.[/dim green]")
            else:
                console.print(f"[dim green]✓ {qtd_salva} chamados sincronizados no DuckDB local.[/dim green]")
        else:
            console.print("[dim yellow]Aviso: Nenhum chamado retornado pela API neste ciclo.[/dim yellow]")
    except ReadOnlySafetyError as se:
        console.print(f"[bold red]ERRO CRÍTICO DE SEGURANÇA:[/bold red] {se}")
        return
    except Exception as e:
        console.print(
            f"[bold yellow]Falha temporária de rede/API ({e}). "
            f"Operando resiliente com cache local do DuckDB.[/bold yellow]"
        )

    # 2. Carrega DataFrame do DuckDB
    df_todos = cache.obter_chamados_df()
    if df_todos.empty:
        console.print("[yellow]Nenhum dado encontrado no cache local para análise.[/yellow]")
        return

    # Filtra os chamados do lote atual ou exclui chamados finalizados
    if tickets_abertos:
        ids_abertos = [str(t.id if t.id is not None else t.codigo) for t in tickets_abertos]
        df_analise = df_todos[df_todos["id"].isin(ids_abertos)].copy()
    else:
        # Fallback: exclui chamados que constem como finalizados
        df_analise = df_todos[~df_todos["status"].str.lower().isin(["fechado", "cancelado", "resolvido", "encerrado"])].copy()

    if df_analise.empty:
        df_analise = df_todos.copy()

    # 3. Executa Motor Analítico Cirúrgico
    params = AnalyticsParams(top_n=8)
    engine = TicketAnalyticsEngine(df_analise, params=params)

    # 4. Imprime Resumo Cirúrgico no Terminal via Rich
    df_sla = engine.metricas_sla()
    tb_sla = Table(title="[bold white]Saúde dos SLAs de Solução[/bold white]", border_style="dim", expand=True)
    tb_sla.add_column("Status do SLA", style="bold")
    tb_sla.add_column("Volume", justify="right", style="cyan")
    tb_sla.add_column("Percentual", justify="right", style="bold yellow")

    cores_map = {
        "Em conformidade": "green",
        "Prestes a estourar": "yellow",
        "Estourado": "red",
        "Sem SLA": "dim white",
    }
    for _, row in df_sla.iterrows():
        st = row["status_sla"]
        cor = cores_map.get(st, "white")
        tb_sla.add_row(f"[{cor}]{st}[/{cor}]", str(row["quantidade"]), f"{row['percentual']}%")

    console.print(tb_sla)

    # Outliers de Demanda (Clientes com z >= 1.8)
    df_outliers = engine.ranking_clientes_outliers()
    clientes_alerta = df_outliers[df_outliers["is_outlier"]]
    if not clientes_alerta.empty:
        nomes_alerta = ", ".join([f"[bold red]{r['cliente']}[/bold red] ({r['total_chamados']} chamados, z={r['z_score']})" for _, r in clientes_alerta.iterrows()])
        console.print(Panel(f"Detectada anomalia estatística de demanda (Z-Score >= 1.8):\n{nomes_alerta}", title="[bold red]⚠️ Alerta de Demanda Anômala (Outliers)[/bold red]", border_style="red"))

    # Fila Crítica de Risco Imediato
    fila_critica = engine.fila_critica_risco_imediato()
    if not fila_critica.empty:
        tb_critica = Table(
            title=f"[bold red]🚨 Fila Crítica de Risco Imediato ({len(fila_critica)} Chamados)[/bold red]",
            header_style="bold red",
            border_style="red",
            expand=True,
        )
        tb_critica.add_column("Código", style="bold white", width=9)
        tb_critica.add_column("Cliente", style="yellow", ratio=2)
        tb_critica.add_column("Assunto", style="white", ratio=3)
        tb_critica.add_column("Prioridade", style="bold red", width=11)
        tb_critica.add_column("Status", style="cyan", width=12)
        tb_critica.add_column("Técnico", style="magenta", ratio=2)
        tb_critica.add_column("SLA", style="bold yellow", width=19)

        for _, r in fila_critica.iterrows():
            tb_critica.add_row(
                str(r.get("codigo", "-")),
                str(r.get("cliente", "-")),
                str(r.get("assunto", "-")),
                str(r.get("prioridade", "-")),
                str(r.get("status", "-")),
                str(r.get("tecnico", "-")),
                str(r.get("status_sla_solucao", "-")),
            )
        console.print(tb_critica)
    else:
        console.print("[dim green]✓ Nenhum chamado Crítico ou Alto com SLA violado/em risco iminente.[/dim green]")

    # 5. Renderiza e atualiza o painel gráfico 2x2
    png_path = plotter.renderizar_painel(engine)
    console.print(f"[dim]Snapshot do painel atualizado em '{png_path}'.[/dim]")


async def main() -> None:
    settings = get_settings()
    console.print(
        Panel.fit(
            "[bold cyan]A.N.A.K.I.N - Monitor Cirúrgico & Painel Operacional em Tempo Real[/bold cyan]\n"
            f"[dim]Trava 100% Read-Only ({settings.milvus_read_only}) | "
            f"Ciclo de Atualização: 90s (Rate Limit seguro: {settings.milvus_rate_limit_seconds}s) | "
            f"DuckDB + Pandas + SciPy[/dim]",
            border_style="cyan",
        )
    )

    client = MilvusReadOnlyClient(settings)
    cache = LocalCache(settings)
    plotter = RealTimeDashboardPlotter("data/painel_tempo_real.png")

    ciclo = 1
    intervalo_ciclo = 90  # 90 segundos garante folga sobre os 62s do Milvus

    try:
        while True:
            await ciclo_monitor(client, cache, plotter, ciclo)
            ciclo += 1

            console.print(f"[dim]Aguardando próximo ciclo em {intervalo_ciclo}s (janela gráfica ativa)...[/dim]")
            # Mantém a janela do Matplotlib responsiva no Windows
            janela_ativa = await plotter.aguardar_com_gui_ativa(intervalo_ciclo)
            if not janela_ativa:
                console.print("\n[yellow]Janela do gráfico fechada pelo usuário. Encerrando monitoramento.[/yellow]")
                break

    except (KeyboardInterrupt, asyncio.CancelledError):
        console.print("\n[yellow]Monitoramento interrompido pelo usuário.[/yellow]")
    finally:
        plotter.fechar()
        await client.close()
        console.print("[cyan]Sessão encerrada com segurança.[/cyan]")


if __name__ == "__main__":
    asyncio.run(main())
