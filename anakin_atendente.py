"""Agente Operacional de Abertura Segura de Chamados (Passo 5).

Utiliza o Google Gemini (com cascata resiliente para NVIDIA NIM Minimax)
para interpretar solicitações em linguagem natural (e/ou prints de telas)
e abrir chamados no Milvus Helpdesk com confirmação humana obrigatória,
memória inteligente de e-mails de técnicos, suporte a atribuição de Técnico e Mesa,
respeitando rate limit de 62s e política estrita de zero alteração cadastral.
"""

from pathlib import Path
from typing import Any, Optional
import asyncio
import json
import os
import re
import sys
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

# Adiciona o diretório src ao sys.path para garantir resolução dos módulos anakin
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

from anakin.config.settings import get_settings
from anakin.domain.ticket_creation_models import DraftTicketExtraction, MilvusCreateTicketPayload
from anakin.milvus.ticket_creator_client import CreationSafetyError, MilvusTicketCreatorClient
from anakin.storage.local_cache import LocalCache
from anakin.vision.gemini_extractor import GeminiTicketExtractor

console = Console()


def extrair_flag_print(texto: str) -> tuple[str, Optional[Path]]:
    """Extrai flag --print ou /print do texto e retorna (texto_limpo, Path | None).

    Formatos aceitos:
      --print "C:\\caminho\\print.png"
      --print=C:\\caminho\\print.png
      /print C:\\caminho\\print.png
    """
    pattern = r"(?:--print|/print)(?:=|\s+)((?:\"[^\"]+\")|(?:'[^']+')|(?:[^\s]+))"
    match = re.search(pattern, texto, flags=re.IGNORECASE)
    if match:
        raw_path = match.group(1).strip("\"'")
        texto_limpo = texto[:match.start()] + texto[match.end():]
        texto_limpo = " ".join(texto_limpo.split())
        path_obj = Path(raw_path).expanduser().resolve()
        return texto_limpo, path_obj
    return texto.strip(), None


def exibir_cabecalho() -> None:
    """Renderiza painel de boas-vindas do Agente Atendente."""
    console.print(
        Panel.fit(
            "[bold cyan]A.N.A.K.I.N - Agente Atendente Operacional[/bold cyan]\n"
            "[dim]Abertura Cirúrgica de Chamados com Google Gemini + NVIDIA NIM + Milvus Helpdesk\n"
            "• Confirmação humana obrigatória antes de qualquer envio\n"
            "• Memória inteligente para e-mails e usernames de técnicos\n"
            "• Suporte à atribuição de Técnico e Mesa de Atendimento\n"
            "• Zero alterações cadastrais em clientes ou contatos\n"
            "• Rate limit estrito de 62s gerenciado automaticamente\n"
            "• Suporte a anexos de imagem com flag: --print \"caminho\\imagem.png\"[/dim]",
            border_style="cyan",
        )
    )


async def selecionar_empresa(
    creator: MilvusTicketCreatorClient,
    empresa_alvo: str,
) -> Optional[dict[str, str]]:
    """Resolve a empresa no DuckDB local ou API do Milvus, com desempate manual se necessário."""
    termo = empresa_alvo.strip()
    with console.status(f"[bold green]Localizando empresa '{termo}' no Milvus/Cache...[/bold green]"):
        candidatos = await creator.resolver_empresa(termo)

    if not candidatos:
        console.print(
            Panel(
                f"[bold yellow]Nenhuma empresa encontrada correspondente a '{termo}' no cache local ou no Milvus.[/bold yellow]\n\n"
                f"[dim]Verifique a grafia ou cadastre o cliente previamente no Milvus antes de abrir o chamado.[/dim]",
                title="[yellow]Empresa Não Encontrada[/yellow]",
                border_style="yellow",
            )
        )
        outro_termo = Prompt.ask("[bold cyan]Digite outro nome para buscar a empresa (ou Enter para cancelar)[/bold cyan]").strip()
        if outro_termo:
            return await selecionar_empresa(creator, outro_termo)
        return None

    # Caso 1: Apenas uma empresa encontrada
    if len(candidatos) == 1:
        emp = candidatos[0]
        console.print(
            f"[dim green]✓ Empresa identificada com precisão:[/dim green] [bold]{emp['nome']}[/bold] "
            f"(Token Milvus: [cyan]{emp['token']}[/cyan])"
        )
        return emp

    # Caso 2: Múltiplas empresas encontradas - seleção do operador
    console.print(f"\n[bold yellow]Foram encontradas {len(candidatos)} empresas candidatas:[/bold yellow]")
    tabela = Table(title="Empresas Encontradas no Milvus", border_style="yellow")
    tabela.add_column("#", justify="center", style="bold cyan")
    tabela.add_column("Nome / Razão Social", style="white")
    tabela.add_column("Token Milvus", style="green")

    for idx, c in enumerate(candidatos, start=1):
        tabela.add_row(str(idx), c["nome"], c["token"])

    console.print(tabela)

    while True:
        escolha = Prompt.ask(
            f"[bold cyan]Escolha o número da empresa (1 a {len(candidatos)}) ou 'c' para cancelar[/bold cyan]"
        ).strip().lower()

        if escolha in ("c", "cancelar", "sair"):
            return None
        if escolha.isdigit():
            num = int(escolha)
            if 1 <= num <= len(candidatos):
                selecionada = candidatos[num - 1]
                console.print(
                    f"[dim green]✓ Selecionada:[/dim green] [bold]{selecionada['nome']}[/bold] "
                    f"([cyan]{selecionada['token']}[/cyan])"
                )
                return selecionada
        console.print("[red]Opção inválida. Digite o número correspondente da lista.[/red]")


def exibir_painel_conferencia(
    empresa: dict[str, str],
    payload: MilvusCreateTicketPayload,
    imagem_path: Optional[Path],
) -> None:
    """Exibe o painel de conferência pré-abertura detalhado com destaque visual."""
    conteudo = (
        f"[bold white]Empresa Solicitante:[/bold white] [bold cyan]{empresa['nome']}[/bold cyan]\n"
        f"[bold white]Token do Cliente (Milvus):[/bold white] [bold green]{payload.cliente_id}[/bold green]\n"
        f"[bold white]Mesa / Fila:[/bold white] [bold magenta]{payload.chamado_mesa or '[dim]Padrão do Cliente[/dim]'}[/bold magenta]\n"
        f"[bold white]Técnico Atribuído:[/bold white] [bold cyan]{payload.chamado_tecnico or '[dim]Não atribuído[/dim]'}[/bold cyan]\n"
        f"[bold white]Contato Solicitante:[/bold white] {payload.chamado_contato or '[dim]Não informado[/dim]'}\n"
        f"[bold white]Telefone:[/bold white] {payload.chamado_telefone or '[dim]Não informado[/dim]'}\n"
        f"[bold white]E-mail:[/bold white] {payload.chamado_email or '[dim]Não informado[/dim]'}\n"
        f"[bold white]Anexo Print:[/bold white] {f'[bold magenta]{imagem_path.name}[/bold magenta] ({imagem_path})' if imagem_path else '[dim]Nenhum anexo[/dim]'}\n"
        f"[bold white]Tipo B2C:[/bold white] {'Sim' if payload.is_b2c else 'Não (Corporativo B2B)'}\n"
        f"─────────────────────────────────────────────────────────────────────────────\n"
        f"[bold white]Assunto:[/bold white] [bold yellow]{payload.chamado_assunto}[/bold yellow]\n\n"
        f"[bold white]Descrição (Texto Plano):[/bold white]\n{payload.chamado_descricao}\n\n"
        f"[bold white]Descrição HTML (Gerada automaticamente para o Milvus):[/bold white]\n"
        f"[dim]{payload.chamado_descricao_html}[/dim]"
    )

    console.print("\n")
    console.print(
        Panel(
            conteudo,
            title="[bold yellow]⚠ PAINEL DE CONFERÊNCIA PRÉ-ABERTURA (MILVUS HELPDESK) ⚠[/bold yellow]",
            subtitle="[dim]Verifique todos os dados antes de autorizar o envio[/dim]",
            border_style="yellow",
        )
    )


async def processar_solicitacao(
    texto_usuario: str,
    extractor: GeminiTicketExtractor,
    creator: MilvusTicketCreatorClient,
    cache: LocalCache,
) -> None:
    """Processa uma única solicitação do operador ponta a ponta."""
    texto_limpo, imagem_path = extrair_flag_print(texto_usuario)

    if imagem_path:
        if not imagem_path.exists():
            console.print(f"[bold red]Erro:[/bold red] O arquivo de imagem especificado não existe: '{imagem_path}'")
            prosseguir = Confirm.ask("Deseja continuar a solicitação desconsiderando a imagem?", default=True)
            if not prosseguir:
                return
            imagem_path = None
        else:
            console.print(f"[dim green]✓ Anexo de imagem reconhecido: '{imagem_path.name}'[/dim green]")

    if not texto_limpo and not imagem_path:
        console.print("[yellow]Nenhum texto ou imagem foi fornecido. Digite sua solicitação.[/yellow]")
        return

    # 1. Extração estruturada com IA (Gemini ou NVIDIA NIM)
    with console.status("[bold green]Analisando solicitação com IA (Gemini / NVIDIA NIM)...[/bold green]"):
        try:
            extraction = extractor.extrair_dados(
                texto_usuario=texto_limpo or "Analise a imagem anexada e extraia os dados do chamado de suporte.",
                imagem_path=imagem_path,
            )
        except Exception as e:
            console.print(f"[bold red]Falha na extração de dados com IA:[/bold red] {e}")
            return

    # 2. Validação de dados suficientes
    if not extraction.dados_suficientes or not extraction.empresa_alvo or not extraction.chamado_assunto or not extraction.chamado_descricao:
        console.print(
            Panel(
                f"[bold yellow]{extraction.mensagem_para_operador}[/bold yellow]\n\n"
                f"[bold white]Dados identificados até agora:[/bold white]\n"
                f"• Empresa: {extraction.empresa_alvo or '[red]Pendente[/red]'}\n"
                f"• Assunto: {extraction.chamado_assunto or '[red]Pendente[/red]'}\n"
                f"• Descrição: {extraction.chamado_descricao or '[red]Pendente[/red]'}\n"
                f"• Mesa: {extraction.chamado_mesa or '[dim]Opcional[/dim]'}\n"
                f"• Técnico: {extraction.chamado_tecnico or '[dim]Opcional[/dim]'}\n"
                f"• Contato: {extraction.chamado_contato or '[dim]Opcional[/dim]'}\n\n"
                f"[dim]Dica: Informe os detalhes faltantes (principalmente a empresa ou o relato do problema).[/dim]",
                title="[yellow]Dados Insuficientes para Abertura[/yellow]",
                border_style="yellow",
            )
        )
        return

    # 3. Resolução da empresa no Milvus / DuckDB (com correspondência normalizada)
    empresa = await selecionar_empresa(creator, extraction.empresa_alvo)
    if not empresa:
        console.print("[yellow]Abertura de chamado abortada (empresa não selecionada).[/yellow]")
        return

    # 4. Tratamento do Técnico e Mesa com Memória Inteligente
    tecnico_final = extraction.chamado_tecnico
    if tecnico_final:
        # Se informado sem @, tenta recuperar da memória inteligente
        if "@" not in tecnico_final:
            email_salvo = cache.obter_email_tecnico(tecnico_final)
            if email_salvo:
                console.print(
                    f"[dim green]✓ E-mail do técnico recuperado da memória inteligente:[/dim green] "
                    f"[bold cyan]{email_salvo}[/bold cyan] ([dim]para '{tecnico_final}'[/dim])"
                )
                tecnico_final = email_salvo
            else:
                tec_local = creator.resolver_tecnico_local(tecnico_final)
                msg_local = f"\n[dim]Técnico localizado no histórico: [bold green]{tec_local}[/bold green][/dim]" if tec_local else ""
                console.print(
                    Panel(
                        f"[bold yellow]Aviso sobre Atribuição de Técnico no Milvus:[/bold yellow]\n"
                        f"O operador solicitou: '[bold cyan]{tecnico_final}[/bold cyan]'.{msg_local}\n\n"
                        f"[dim]A API do Milvus exige obrigatoriamente o [bold white]username ou e-mail[/bold white] cadastrado do técnico.\n"
                        f"Informe o e-mail/username abaixo (ele será memorizado automaticamente para as próximas solicitações);\n"
                        f"caso contrário pressione Enter para manter.[/dim]",
                        title="[yellow]Atenção: E-mail / Username do Técnico[/yellow]",
                        border_style="yellow",
                    )
                )
                try:
                    novo_email = Prompt.ask(
                        f"[bold cyan]Digite o e-mail/username do técnico para envio ao Milvus (ou Enter para manter '{tecnico_final}')[/bold cyan]",
                        default="",
                    ).strip()
                except (KeyboardInterrupt, EOFError):
                    novo_email = ""

                if novo_email:
                    cache.salvar_email_tecnico(tecnico_final, novo_email)
                    console.print(f"[dim green]✓ Memória salva: '{tecnico_final}' -> '{novo_email}'.[/dim green]")
                    tecnico_final = novo_email
        else:
            # Se já contém @, salva alias associado ao nome aproximado
            prefixo = tecnico_final.split("@")[0].replace(".", " ").title()
            cache.salvar_email_tecnico(prefixo, tecnico_final)

    # 5. Construção do Payload do Milvus
    payload = MilvusCreateTicketPayload(
        cliente_id=empresa["token"],
        chamado_assunto=extraction.chamado_assunto,
        chamado_descricao=extraction.chamado_descricao,
        is_b2c=False,
        chamado_contato=extraction.chamado_contato,
        chamado_telefone=extraction.chamado_telefone,
        chamado_email=extraction.chamado_email,
        chamado_tecnico=tecnico_final,
        chamado_mesa=extraction.chamado_mesa,
    )

    # 6. Painel de Conferência Pré-Abertura
    exibir_painel_conferencia(empresa, payload, imagem_path)

    # 7. Confirmação Humana Obrigatória
    console.print(
        "[bold red]AÇÃO IRREVERSÍVEL:[/bold red] Confirma a abertura REAL deste chamado no Milvus Helpdesk?"
    )
    try:
        confirma = Prompt.ask(
            "Digite '[bold green]s[/bold green]' para confirmar e criar o chamado ou qualquer outra tecla para cancelar",
            default="n",
        ).strip().lower()
    except (KeyboardInterrupt, EOFError):
        confirma = "n"

    if confirma != "s":
        console.print("[yellow]Operação cancelada pelo operador. Nenhum dado foi gravado no Milvus.[/yellow]\n")
        return

    # 8. Execução do POST /chamado/criar
    with console.status("[bold green]Criando chamado no Milvus Helpdesk (respeitando rate limit)...[/bold green]"):
        try:
            resposta_milvus = await creator.criar_chamado(payload)
        except CreationSafetyError as se:
            console.print(f"[bold red]VIOLAÇÃO DE SEGURANÇA:[/bold red] {se}")
            return
        except Exception as api_err:
            console.print(f"[bold red]Erro ao criar chamado no Milvus:[/bold red] {api_err}")
            return

    # 9. Apresentação do Sucesso (compatível com int, str ou dict retornado pela API)
    if isinstance(resposta_milvus, dict):
        chamado_id = str(
            resposta_milvus.get("id")
            or resposta_milvus.get("chamado_id")
            or resposta_milvus.get("codigo")
            or resposta_milvus.get("protocolo")
            or "Gerado"
        )
    elif isinstance(resposta_milvus, (int, str)):
        chamado_id = str(resposta_milvus)
    else:
        chamado_id = str(resposta_milvus)

    resposta_exibicao = (
        json.dumps(resposta_milvus, ensure_ascii=False)
        if isinstance(resposta_milvus, (dict, list))
        else str(resposta_milvus)
    )

    detalhes_sucesso = (
        f"[bold green]✓ Chamado aberto com sucesso em produção no Milvus![/bold green]\n\n"
        f"[bold white]ID / Protocolo Milvus:[/bold white] [bold cyan]{chamado_id}[/bold cyan]\n"
        f"[bold white]Empresa:[/bold white] {empresa['nome']} ({empresa['token']})\n"
        f"[bold white]Assunto:[/bold white] {payload.chamado_assunto}\n"
        f"[bold white]Mesa:[/bold white] {payload.chamado_mesa or '[dim]Padrão[/dim]'}\n"
        f"[bold white]Técnico:[/bold white] {payload.chamado_tecnico or '[dim]Não atribuído[/dim]'}\n"
        f"[bold white]Retorno Oficial Milvus:[/bold white] [bold yellow]{resposta_exibicao}[/bold yellow]"
    )

    console.print(
        Panel(
            detalhes_sucesso,
            title="[bold green]Sucesso - Chamado Aberto[/bold green]",
            border_style="green",
        )
    )

    # 10. Envio Opcional de Anexo (se houver imagem)
    if imagem_path and chamado_id and chamado_id != "Gerado":
        enviar_anexo = Confirm.ask(
            f"\nDeseja anexar o print '{imagem_path.name}' ao chamado {chamado_id} agora? "
            f"(Nota: aguardará a janela de 62s do rate limit)",
            default=True,
        )
        if enviar_anexo:
            with console.status(f"[bold green]Enviando anexo '{imagem_path.name}' para o chamado {chamado_id}...[/bold green]"):
                try:
                    res_anexo = await creator.anexar_arquivo(chamado_id, imagem_path)
                    console.print(f"[bold green]✓ Anexo enviado com sucesso:[/bold green] {res_anexo}\n")
                except Exception as anexo_err:
                    console.print(f"[bold red]Erro ao anexar arquivo:[/bold red] {anexo_err}\n")


async def main() -> None:
    """Ponto de entrada do Agente Atendente."""
    exibir_cabecalho()

    settings = get_settings()
    if not settings.gemini_api_key and not settings.nvidia_api_key:
        console.print(
            "[bold red]ERRO DE CONFIGURAÇÃO:[/bold red] Nenhuma chave de IA (GEMINI_API_KEY ou NVIDIA_API_KEY) foi encontrada no `.env`.\n"
            "Configure as chaves no arquivo `.env` para utilizar o Agente Atendente."
        )
        return

    extractor = GeminiTicketExtractor(settings=settings)
    cache = LocalCache(settings=settings)

    async with MilvusTicketCreatorClient(settings=settings) as creator:
        # Se argumentos foram passados na linha de comando, processa como solicitação única
        if len(sys.argv) > 1:
            comando_cli = " ".join(sys.argv[1:])
            console.print(f"[dim]Processando solicitação via linha de comando:[/dim] [cyan]{comando_cli}[/cyan]\n")
            try:
                await processar_solicitacao(comando_cli, extractor, creator, cache)
            except Exception as e:
                console.print(f"[bold red]Erro no processamento da solicitação:[/bold red] {e}\n")
            return

        # Modo Interativo (REPL)
        console.print("[dim]Digite sua solicitação em linguagem natural ou digite 'sair' para encerrar.[/dim]\n")

        while True:
            try:
                entrada = Prompt.ask("[bold cyan]Anakin [Atendente][/bold cyan]").strip()
            except (KeyboardInterrupt, EOFError):
                console.print("\n[yellow]Sessão encerrada pelo operador.[/yellow]")
                break

            if not entrada:
                continue

            if entrada.lower() in ("sair", "exit", "quit", "q"):
                console.print("[yellow]Encerrando o Agente Atendente. Até logo![/yellow]")
                break

            try:
                await processar_solicitacao(entrada, extractor, creator, cache)
            except Exception as e:
                console.print(f"[bold red]Erro durante o atendimento:[/bold red] {e}\n")

            console.print("\n" + "─" * 80 + "\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Operação cancelada.[/yellow]")
        sys.exit(0)
