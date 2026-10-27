"""Script CLI seguro (100% Read-Only) para visualização de chamados do Milvus.

Consulta os chamados em produção sem qualquer risco de escrita ou alteração,
atualiza o cache local DuckDB e exibe os dados formatados com Rich.
"""

import asyncio
import sys
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from pathlib import Path

# Adiciona o diretório src ao sys.path para importação do pacote anakin
SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Garante codificação UTF-8 no Windows
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stdin and hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from anakin.milvus.readonly_client import MilvusReadOnlyClient, ReadOnlySafetyError
from anakin.storage.local_cache import LocalCache

console = Console()


async def main() -> None:
    console.print(
        Panel.fit(
            "[bold cyan]A.N.A.K.I.N - Consulta Segura de Chamados (Milvus Read-Only)[/bold cyan]\n"
            "[dim]Módulo 100% somente leitura | Rate Limit estrito (62s) | Cache local DuckDB[/dim]",
            border_style="cyan",
        )
    )

    client = MilvusReadOnlyClient()
    cache = LocalCache()

    try:
        # 1. Consulta os últimos 10 chamados abertos (status=9)
        with console.status("[bold green]Consultando últimos 10 chamados abertos no Milvus...[/bold green]"):
            tickets = await client.listar_chamados(status=9, total_registros=10, pagina=1)

        if not tickets:
            console.print("[bold yellow]Nenhum chamado aberto encontrado no Milvus.[/bold yellow]")
            return

        # Atualiza o cache local DuckDB
        salvos = cache.salvar_chamados(tickets)
        console.print(
            f"[dim green]✓ {salvos} chamados sincronizados no DuckDB local "
            f"('{client.settings.duckdb_path}', total no banco: {cache.total_chamados()}).[/dim green]\n"
        )

        # Monta a tabela de chamados com Rich
        tabela = Table(
            title="[bold white]Últimos Chamados Abertos (Milvus Helpdesk)[/bold white]",
            header_style="bold magenta",
            border_style="dim",
            expand=True,
        )

        tabela.add_column("Código", style="bold cyan", no_wrap=True, width=10)
        tabela.add_column("Cliente", style="green", ratio=2)
        tabela.add_column("Assunto", style="white", ratio=3)
        tabela.add_column("Status", style="yellow", width=12)
        tabela.add_column("Prioridade", style="bold blue", width=10)
        tabela.add_column("Técnico", style="dim cyan", ratio=2)
        tabela.add_column("SLA Solução", style="bold yellow", ratio=2)

        for t in tickets:
            tabela.add_row(
                str(t.codigo or t.id or "-"),
                str(t.cliente or "-"),
                str(t.assunto or "-"),
                str(t.status or "Aberto"),
                str(t.prioridade or "-"),
                str(t.tecnico or "Não atribuído"),
                t.sla_solucao_formatado,
            )

        console.print(tabela)

        # 2. Exibe a descrição completa do chamado mais recente
        mais_recente = tickets[0]
        desc_texto = mais_recente.descricao_limpa or "[dim italic]Sem descrição cadastrada.[/dim italic]"

        console.print("\n")
        console.print(
            Panel(
                desc_texto,
                title=f"[bold green]Descrição Completa - Chamado #{mais_recente.codigo}[/bold green]",
                subtitle=(
                    f"[dim]Cliente: {mais_recente.cliente or 'N/D'} | "
                    f"Contato: {mais_recente.contato or 'N/D'} | "
                    f"Aberto em: {mais_recente.data_criacao or 'N/D'}[/dim]"
                ),
                border_style="green",
            )
        )

        # 3. Pergunta opcional para ver comentários/acompanhamentos
        try:
            resposta = input(
                "\nDeseja consultar os comentários/acompanhamentos deste chamado? (s/N) "
                "[Aviso: aguardará 62s de rate limit caso necessário]: "
            ).strip().lower()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Operação cancelada pelo usuário.[/yellow]")
            return

        if resposta in ("s", "sim", "y", "yes"):
            with console.status(
                f"[bold cyan]Buscando acompanhamentos do chamado #{mais_recente.codigo}...[/bold cyan]"
            ):
                comentarios = await client.listar_comentarios(
                    chamado_codigo=mais_recente.codigo or mais_recente.id
                )

            if not comentarios:
                console.print(
                    f"[yellow]Nenhum comentário/acompanhamento encontrado para o chamado #{mais_recente.codigo}.[/yellow]"
                )
            else:
                console.print(
                    f"\n[bold green]Encontrados {len(comentarios)} acompanhamento(s) para o chamado #{mais_recente.codigo}:[/bold green]\n"
                )
                for idx, c in enumerate(comentarios, 1):
                    tipo_str = f" ({c.tipo})" if c.tipo else ""
                    privado_tag = " [bold red][PRIVADO][/bold red]" if c.privado else ""
                    console.print(
                        Panel(
                            c.texto_limpo or "[dim]Sem texto[/dim]",
                            title=f"#{idx} - {c.pessoa or 'Sistema'}{tipo_str}{privado_tag}",
                            subtitle=f"[dim]Perfil: {c.perfil or 'N/D'} | Data: {c.data or 'N/D'}[/dim]",
                            border_style="blue",
                        )
                    )

    except ReadOnlySafetyError as se:
        console.print(f"[bold red]ERRO DE SEGURANÇA:[/bold red] {se}")
    except Exception as e:
        console.print(f"[bold red]Erro ao processar chamada no Milvus:[/bold red] {e}")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
