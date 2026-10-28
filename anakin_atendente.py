"""Agente Operacional de Abertura Segura de Chamados (Passo 6).

Utiliza o Google Gemini (com cascata resiliente para NVIDIA NIM Minimax)
para interpretar solicitações em linguagem natural (e/ou prints de telas)
e abrir chamados no Milvus Helpdesk com confirmação humana obrigatória,
atribuição cirúrgica de Operador (chamado_tecnico) e Mesa de Trabalho (chamado_mesa),
leitura de prints via Ctrl+C (@colar) ou seletor (@arquivo),
sincronização de operadores reais (/sincronizar, /operadores),
respeitando rate limit de 62s e política estrita de zero alteração cadastral.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
import asyncio
import json
import os
import re
import shutil
import sys
from PIL import Image, ImageGrab
from rich.console import Console
from rich.markdown import Markdown
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

from anakin.analytics.ticket_advisor import TicketAdvisorEngine
from anakin.config.settings import Settings, get_settings
from anakin.domain.ticket_creation_models import DraftTicketExtraction, MilvusCreateTicketPayload
from anakin.milvus.readonly_client import MilvusReadOnlyClient
from anakin.milvus.ticket_creator_client import CreationSafetyError, MilvusTicketCreatorClient
from anakin.storage.local_cache import LocalCache, normalizar_para_busca
from anakin.vision.gemini_extractor import GeminiTicketExtractor

console = Console()


@dataclass
class RascunhoSessao:
    """Estado persistente de rascunho de chamado mantido entre turnos da sessão (Stateful Draft)."""

    empresa_alvo: Optional[str] = None
    chamado_assunto: Optional[str] = None
    chamado_descricao: Optional[str] = None
    chamado_contato: Optional[str] = None
    chamado_telefone: Optional[str] = None
    chamado_email: Optional[str] = None
    chamado_mesa: Optional[str] = None
    chamado_tecnico: Optional[str] = None
    anexo_paths: list[str] = field(default_factory=list)

    def esta_ativo(self) -> bool:
        """Retorna True se houver qualquer dado relevante preenchido ou print salvo."""
        return any([
            self.empresa_alvo,
            self.chamado_assunto,
            self.chamado_descricao,
            self.chamado_contato,
            self.chamado_telefone,
            self.chamado_email,
            self.chamado_mesa,
            self.chamado_tecnico,
            bool(self.anexo_paths),
        ])

    def limpar(self) -> None:
        """Limpa integralmente o rascunho ativo."""
        self.empresa_alvo = None
        self.chamado_assunto = None
        self.chamado_descricao = None
        self.chamado_contato = None
        self.chamado_telefone = None
        self.chamado_email = None
        self.chamado_mesa = None
        self.chamado_tecnico = None
        self.anexo_paths.clear()

    def to_dict(self) -> dict[str, Any]:
        """Retorna dicionário serializável para passagem ao extrator Gemini."""
        return {
            "empresa_alvo": self.empresa_alvo,
            "chamado_assunto": self.chamado_assunto,
            "chamado_descricao": self.chamado_descricao,
            "chamado_contato": self.chamado_contato,
            "chamado_telefone": self.chamado_telefone,
            "chamado_email": self.chamado_email,
            "chamado_mesa": self.chamado_mesa,
            "chamado_tecnico": self.chamado_tecnico,
            "anexo_paths": list(self.anexo_paths),
        }

    def aplicar_merge_defensivo(
        self,
        extraction: DraftTicketExtraction,
        nova_imagem: Optional[Path] = None,
        cache: Optional[LocalCache] = None,
    ) -> None:
        """Aplica merge defensivo no Python garantindo que nenhum dado prévio seja perdido."""
        # Anexo: se veio nova imagem via @colar ou @arquivo, salva no rascunho
        if nova_imagem:
            nova_str = str(nova_imagem.resolve())
            if nova_str not in self.anexo_paths:
                self.anexo_paths.append(nova_str)

        # Campos de texto: atualiza no rascunho apenas se a nova extração vier preenchida
        campos = [
            "empresa_alvo",
            "chamado_assunto",
            "chamado_descricao",
            "chamado_contato",
            "chamado_telefone",
            "chamado_email",
            "chamado_mesa",
            "chamado_tecnico",
        ]
        for campo in campos:
            novo_val = getattr(extraction, campo, None)
            if novo_val is not None:
                val_str = str(novo_val).strip()
                if val_str and val_str.lower() not in ("null", "none"):
                    setattr(self, campo, val_str)

        # Normalização inteligente de Mesa de Trabalho (casando com DuckDB se disponível)
        if self.chamado_mesa and cache:
            mesa_resolvida = cache.resolver_mesa_trabalho(self.chamado_mesa)
            if mesa_resolvida:
                if mesa_resolvida != self.chamado_mesa:
                    console.print(
                        f"[dim green]✓ Mesa de Trabalho ajustada com base no Milvus:[/dim green] "
                        f"'{self.chamado_mesa}' -> [bold magenta]{mesa_resolvida}[/bold magenta]"
                    )
                self.chamado_mesa = mesa_resolvida

        # Sincroniza de volta no objeto extraction para que as etapas seguintes usem os dados consolidados
        for campo in campos:
            setattr(extraction, campo, getattr(self, campo))

        # Reavalia dados_suficientes baseado no estado consolidado do rascunho
        extraction.dados_suficientes = bool(
            self.empresa_alvo and self.chamado_assunto and self.chamado_descricao
        )


def exibir_painel_rascunho(rascunho: RascunhoSessao, mensagem: Optional[str] = None) -> None:
    """Exibe o painel visual do rascunho ativo indicando campos pendentes e preenchidos."""
    anexos_formatados = (
        ", ".join([f"[bold magenta]{Path(p).name}[/bold magenta]" for p in rascunho.anexo_paths])
        if rascunho.anexo_paths
        else "[dim]Nenhum anexo[/dim]"
    )
    conteudo = (
        f"{f'[bold yellow]{mensagem}[/bold yellow]\n\n' if mensagem else ''}"
        f"[bold white]Dados do Rascunho Ativo:[/bold white]\n"
        f"• Empresa Solicitante: {f'[bold cyan]{rascunho.empresa_alvo}[/bold cyan]' if rascunho.empresa_alvo else '[red]Pendente[/red]'}\n"
        f"• Assunto: {f'[bold yellow]{rascunho.chamado_assunto}[/bold yellow]' if rascunho.chamado_assunto else '[red]Pendente[/red]'}\n"
        f"• Descrição: {f'[white]{rascunho.chamado_descricao}[/white]' if rascunho.chamado_descricao else '[red]Pendente[/red]'}\n"
        f"• Mesa de Trabalho: {f'[bold magenta]{rascunho.chamado_mesa}[/bold magenta]' if rascunho.chamado_mesa else '[dim]Opcional / Padrão[/dim]'}\n"
        f"• Operador Atribuído: {f'[bold cyan]{rascunho.chamado_tecnico}[/bold cyan]' if rascunho.chamado_tecnico else '[dim]Opcional / Não atribuído[/dim]'}\n"
        f"• Contato: {rascunho.chamado_contato or '[dim]Não informado[/dim]'}\n"
        f"• Telefone: {rascunho.chamado_telefone or '[dim]Não informado[/dim]'}\n"
        f"• E-mail: {rascunho.chamado_email or '[dim]Não informado[/dim]'}\n"
        f"• Print(s) Salvo(s): {anexos_formatados}\n\n"
        f"[dim]Dica: Digite as informações faltantes ou use '/cancelar' para limpar o rascunho.[/dim]"
    )
    console.print(
        Panel(
            conteudo,
            title="[yellow]⚠ Rascunho de Chamado em Andamento[/yellow]",
            border_style="yellow",
        )
    )


def capturar_print_clipboard() -> Optional[Path]:
    """Captura imagem da área de transferência do Windows (Win + Shift + S ou print copiado)."""
    try:
        conteudo = ImageGrab.grabclipboard()
    except Exception as e:
        console.print(f"[bold yellow]Aviso ao ler Área de Transferência:[/bold yellow] {e}")
        return None

    prints_dir = Path("data/prints")
    prints_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target_path = prints_dir / f"print_{timestamp}.png"

    # Caso 1: Objeto de imagem PIL direto na memória (Win + Shift + S ou imagem copiada)
    if isinstance(conteudo, Image.Image):
        try:
            conteudo.save(target_path, format="PNG")
            console.print(f"[dim green]✓ Print capturado da Área de Transferência:[/dim green] [bold cyan]{target_path.name}[/bold cyan]")
            return target_path.resolve()
        except Exception as err:
            console.print(f"[bold red]Erro ao salvar print da Área de Transferência:[/bold red] {err}")
            return None

    # Caso 2: Lista de arquivos copiados no Windows Explorer
    if isinstance(conteudo, list):
        for item in conteudo:
            p = Path(str(item))
            if p.is_file() and p.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp", ".bmp"]:
                try:
                    shutil.copy2(p, target_path)
                    console.print(f"[dim green]✓ Imagem copiada do Explorer salva em:[/dim green] [bold cyan]{target_path.name}[/bold cyan]")
                    return target_path.resolve()
                except Exception as err:
                    console.print(f"[yellow]Aviso ao duplicar imagem: {err}. Utilizando caminho original.[/yellow]")
                    return p.resolve()

    console.print(
        "[bold yellow]Nenhuma imagem detectada na Área de Transferência.[/bold yellow]\n"
        "[dim]Dica: Tire um print com Win + Shift + S ou copie um arquivo de imagem antes de usar @colar.[/dim]"
    )
    return None


def selecionar_arquivo_imagem() -> Optional[Path]:
    """Abre o seletor nativo de arquivos do Windows para escolha de um print/imagem."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        caminho = filedialog.askopenfilename(
            title="Selecione o Print ou Imagem do Chamado",
            filetypes=[
                ("Arquivos de Imagem", "*.png;*.jpg;*.jpeg;*.webp;*.bmp"),
                ("Todos os arquivos", "*.*"),
            ],
        )
        root.destroy()

        if caminho:
            p = Path(caminho).resolve()
            if p.is_file():
                console.print(f"[dim green]✓ Arquivo de imagem selecionado:[/dim green] [bold cyan]{p.name}[/bold cyan]")
                return p
        else:
            console.print("[dim yellow]Seleção de arquivo cancelada pelo operador.[/dim yellow]")
    except Exception as e:
        console.print(f"[bold red]Falha ao abrir seletor de arquivos:[/bold red] {e}")
    return None


def extrair_flag_print(texto: str) -> tuple[str, Optional[Path]]:
    """Extrai flag legada --print ou /print do texto e retorna (texto_limpo, Path | None)."""
    pattern = r"(?:--print|/print)(?:=|\s+)((?:\"[^\"]+\")|(?:'[^']+')|(?:[^\s]+))"
    match = re.search(pattern, texto, flags=re.IGNORECASE)
    if match:
        raw_path = match.group(1).strip("\"'")
        texto_limpo = texto[:match.start()] + texto[match.end():]
        texto_limpo = " ".join(texto_limpo.split())
        path_obj = Path(raw_path).expanduser().resolve()
        return texto_limpo, path_obj
    return texto.strip(), None


def processar_anexos_comando(texto_usuario: str) -> tuple[str, Optional[Path]]:
    """Processa @colar, @arquivo e flags legadas --print / /print.

    Retorna (texto_limpo, imagem_path).
    """
    imagem_path: Optional[Path] = None
    texto_atual = texto_usuario

    # 1. Suporte a @colar (Área de Transferência do Windows)
    if re.search(r"(?i)@colar\b", texto_atual):
        imagem_path = capturar_print_clipboard()
        texto_atual = re.sub(r"(?i)@colar\b", "", texto_atual)

    # 2. Suporte a @arquivo (Seletor nativo do Windows)
    if re.search(r"(?i)@arquivo\b", texto_atual):
        if not imagem_path:
            imagem_path = selecionar_arquivo_imagem()
        texto_atual = re.sub(r"(?i)@arquivo\b", "", texto_atual)

    # 3. Suporte a --print / /print legado
    texto_limpo, path_flag = extrair_flag_print(texto_atual)
    if not imagem_path and path_flag:
        imagem_path = path_flag

    texto_final = " ".join(texto_limpo.split()).strip()
    return texto_final, imagem_path


async def aguardar_com_contagem(segundos: int = 62, motivo: str = "rate limit do Milvus") -> None:
    """Exibe uma contagem regressiva amigável no terminal para aguardar o rate limit."""
    console.print(f"\n[bold yellow]Aguardando {segundos}s ({motivo})...[/bold yellow]")
    for r in range(segundos, 0, -1):
        console.print(f" [dim]Liberando requisição em {r:02d}s...[/dim]", end="\r")
        await asyncio.sleep(1)
    console.print(" [dim green]✓ Janela de 62s liberada! Prosseguindo...[/dim green]                       ")


async def sincronizar_operadores(settings: Settings, cache: LocalCache) -> int:
    """Busca chamados no Milvus para atualizar o status dos abertos no DuckDB e preencher e-mails pendentes dos operadores oficiais."""
    console.print(
        Panel(
            "[bold cyan]Sincronizando Chamados Abertos e Operadores com o Milvus Helpdesk...[/bold cyan]\n"
            "[dim]Consultando chamados abertos (POST /chamado/listagem, status=9) para atualizar status no DuckDB e preencher e-mails...[/dim]",
            title="[cyan]Sincronização Geral[/cyan]",
            border_style="cyan",
        )
    )

    async with MilvusReadOnlyClient(settings=settings) as ro_client:
        try:
            with console.status("[bold green]Buscando lote de chamados abertos no Milvus (status=9)...[/bold green]"):
                chamados = await ro_client.listar_chamados(status=9, total_registros=200)

            total_salvos = cache.salvar_chamados(chamados)
            cods_abertos = [c.codigo for c in chamados if c.codigo]
            finalizados = cache.sincronizar_status_abertos(cods_abertos)
            if finalizados > 0:
                console.print(f"[dim green]✓ {finalizados} chamado(s) que foram concluídos no Milvus marcados como 'Finalizado' no cache local.[/dim green]")

            # Tenta preencher Wander Cristiano ou outros operadores se encontrados no lote
            for c in chamados:
                tec_str = str(c.tecnico or "").strip()
                email_str = str(c.email_tecnico or "").strip().lower()
                if email_str and "@" in email_str and " " not in email_str:
                    if "wander" in tec_str.lower():
                        cache.definir_email_operador("Wander Cristiano", email_str)

            operadores = cache.listar_operadores_oficiais()
            exibir_operadores_mapeados(cache)
            console.print(f"[bold green]✓ Sincronização concluída com sucesso![/bold green] {len(operadores)} operador(es) oficial(is) no catálogo.\n")
            return len(operadores)
        except Exception as e:
            console.print(f"[bold red]Erro ao sincronizar operadores do Milvus:[/bold red] {e}\n")
            return 0


def exibir_operadores_mapeados(cache: LocalCache) -> None:
    """Renderiza tabela elegante com exatamente os 14 operadores oficiais em ordem alfabética."""
    operadores = cache.listar_operadores_oficiais()
    if not operadores:
        console.print("[yellow]Nenhum operador oficial cadastrado.[/yellow]")
        return

    tabela = Table(title=f"Catálogo Oficial de Operadores - Upgrade TI ({len(operadores)})", border_style="cyan")
    tabela.add_column("#", justify="center", style="dim")
    tabela.add_column("Operador / Nome Oficial", style="bold white")
    tabela.add_column("E-mail Oficial (chamado_tecnico)", style="bold cyan")

    for idx, (nome, email) in enumerate(operadores, 1):
        email_display = email if email else "[dim red]Pendente (/definir-email para preencher)[/dim red]"
        tabela.add_row(str(idx), nome, email_display)

    console.print(tabela)


def exibir_cabecalho() -> None:
    """Renderiza painel de boas-vindas do Agente Atendente e Copiloto Ticket Manager."""
    console.print(
        Panel.fit(
            "[bold cyan]A.N.A.K.I.N - Agente Atendente & Copiloto Ticket Manager[/bold cyan]\n"
            "[dim]Abertura Cirúrgica de Chamados + Auditoria Profunda ITIL (Milvus Helpdesk)\n\n"
            "[bold yellow]1. Modo Copiloto do Ticket Manager (Passo 8 - 100% Somente Leitura):[/bold yellow]\n"
            "• [bold white]/analisar <código>[/bold white] ou diga [cyan]'Quero entender o chamado #69118...'[/cyan]\n"
            "  -> Raio-X da situação, linha do tempo, diagnóstico técnico, ajustes no Milvus e mensagem pronta para o técnico!\n"
            "• [bold white]/atualizar <código>[/bold white]: força consulta fresca na API do Milvus para atualizar o cache\n"
            "• [bold white]/carteira <operador>[/bold white] ou diga [cyan]'Como estão os chamados do Gabriel Oliveira e como devo operá-lo?'[/cyan]\n"
            "  -> Plano tático imediato com ordem exata de ataque e alinhamento 1:1\n"
            "• [bold white]Continuação Fluida:[/bold white] faça perguntas em sequência sobre o mesmo chamado com resposta instantânea e zero API!\n\n"
            "[bold green]2. Modo Abertura de Chamados (100% Preservado):[/bold green]\n"
            "• [bold white]@colar[/bold white]: captura de print direto da Área de Transferência (Win + Shift + S)\n"
            "• [bold white]@arquivo[/bold white]: seletor gráfico de arquivo PNG/JPG no Windows\n"
            "• [bold white]Operador & Mesa:[/bold white] mapeamento exclusivo para os 14 operadores oficiais e filas reais\n"
            "• [bold white]/sincronizar[/bold white] | [bold white]/operadores[/bold white] | [bold white]/definir-email[/bold white]: gestão do catálogo de técnicos\n"
            "• Confirmação humana obrigatória ('s'/'e'/'c') antes de qualquer gravação em produção\n"
            "• Proteção de cota com cascata resiliente: Gemini 3.5 -> Gemini 3.1 -> NVIDIA NIM Minimax[/dim]",
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
    anexo_paths: list[str] | Optional[Path],
    operador_nome_display: Optional[str] = None,
) -> None:
    """Exibe o painel de conferência pré-abertura detalhado com destaque visual e nomenclatura Milvus."""
    # Formatação do Operador Atribuído: Nome (email@dominio.com)
    if payload.chamado_tecnico:
        if operador_nome_display and operador_nome_display.lower() != payload.chamado_tecnico.lower():
            operador_formatado = f"[bold cyan]{operador_nome_display}[/bold cyan] ([dim green]{payload.chamado_tecnico}[/dim green])"
        else:
            operador_formatado = f"[bold cyan]{payload.chamado_tecnico}[/bold cyan]"
    else:
        operador_formatado = "[dim]Não atribuído[/dim]"

    # Formatação dos anexos
    if isinstance(anexo_paths, list):
        if anexo_paths:
            nomes = ", ".join([f"[bold magenta]{Path(p).name}[/bold magenta]" for p in anexo_paths])
            anexo_str = f"{nomes} ({len(anexo_paths)} arquivo(s))"
        else:
            anexo_str = "[dim]Nenhum anexo[/dim]"
    elif anexo_paths:
        p = Path(anexo_paths)
        anexo_str = f"[bold magenta]{p.name}[/bold magenta] ({p})"
    else:
        anexo_str = "[dim]Nenhum anexo[/dim]"

    conteudo = (
        f"[bold white]Empresa Solicitante:[/bold white] [bold cyan]{empresa['nome']}[/bold cyan]\n"
        f"[bold white]Token do Cliente (Milvus):[/bold white] [bold green]{payload.cliente_id}[/bold green]\n"
        f"[bold white]Mesa de Trabalho:[/bold white] [bold magenta]{payload.chamado_mesa or '[dim]Padrão do Cliente[/dim]'}[/bold magenta]\n"
        f"[bold white]Operador Atribuído:[/bold white] {operador_formatado}\n"
        f"[bold white]Contato Solicitante:[/bold white] {payload.chamado_contato or '[dim]Não informado[/dim]'}\n"
        f"[bold white]Telefone:[/bold white] {payload.chamado_telefone or '[dim]Não informado[/dim]'}\n"
        f"[bold white]E-mail:[/bold white] {payload.chamado_email or '[dim]Não informado[/dim]'}\n"
        f"[bold white]Anexos / Prints:[/bold white] {anexo_str}\n"
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
            subtitle="[dim]Verifique todos os dados antes de autorizar a abertura do chamado[/dim]",
            border_style="yellow",
        )
    )


async def processar_solicitacao(
    texto_usuario: str,
    extractor: GeminiTicketExtractor,
    creator: MilvusTicketCreatorClient,
    cache: LocalCache,
    settings: Settings,
    rascunho: RascunhoSessao,
) -> bool:
    """Processa uma única solicitação do operador preservando o rascunho contínuo da sessão."""
    texto_limpo, imagem_path = processar_anexos_comando(texto_usuario)

    if imagem_path:
        if not imagem_path.exists():
            console.print(f"[bold red]Erro:[/bold red] O arquivo de imagem especificado não existe: '{imagem_path}'")
            prosseguir = Confirm.ask("Deseja continuar a solicitação desconsiderando a imagem?", default=True)
            if not prosseguir:
                return False
            imagem_path = None
        else:
            console.print(f"[dim green]✓ Anexo de print pronto: '{imagem_path.name}'[/dim green]")

    if not texto_limpo and not imagem_path:
        if rascunho.esta_ativo():
            exibir_painel_rascunho(rascunho, "Rascunho atual em andamento:")
        else:
            console.print("[yellow]Nenhum texto ou print foi fornecido. Digite sua solicitação ou use @colar.[/yellow]")
        return False

    # Define qual imagem deve ser enviada para a extração do Gemini nesta iteração
    img_para_extracao = imagem_path
    if not img_para_extracao and not texto_limpo and rascunho.anexo_paths:
        ultimo = Path(rascunho.anexo_paths[-1])
        if ultimo.exists():
            img_para_extracao = ultimo

    if img_para_extracao and not texto_limpo:
        prompt_ia = (
            "Analise atentamente o print anexado (conversa de WhatsApp, tela de erro ou e-mail). "
            "Extraia o contexto completo do problema relatado pelo colaborador, gere um chamado_assunto objetivo "
            "e redija uma chamado_descricao clara, técnica e completa baseada no print, identificando também "
            "o nome da empresa, contato, telefone ou e-mail se visíveis."
        )
    elif img_para_extracao and texto_limpo:
        prompt_ia = (
            f"{texto_limpo}\n\n"
            "Nota: Há um print anexado. Analise atentamente a imagem do print para extrair o contexto completo do "
            "problema relatado pelo colaborador, enriquecendo o assunto e a descrição técnica do chamado."
        )
    else:
        prompt_ia = texto_limpo

    # 1. Extração estruturada com IA passando o rascunho prévio ativo
    with console.status("[bold green]Analisando solicitação com IA (Gemini / NVIDIA NIM)...[/bold green]"):
        try:
            extraction = extractor.extrair_dados(
                texto_usuario=prompt_ia,
                imagem_path=img_para_extracao,
                rascunho_previo=rascunho.to_dict() if rascunho.esta_ativo() else None,
            )
        except Exception as e:
            console.print(f"[bold red]Falha na extração de dados com IA:[/bold red] {e}")
            return False

    # 2. Merge defensivo no Python garantindo que nenhum campo anterior seja perdido
    rascunho.aplicar_merge_defensivo(extraction, nova_imagem=imagem_path, cache=cache)

    # 3. Validação de dados suficientes para abertura
    if not extraction.dados_suficientes or not extraction.empresa_alvo or not extraction.chamado_assunto or not extraction.chamado_descricao:
        exibir_painel_rascunho(
            rascunho,
            mensagem=extraction.mensagem_para_operador or "Dados insuficientes para abertura imediata do chamado.",
        )
        return False

    # 4. Resolução da empresa no Milvus / DuckDB (sem perder o rascunho se não encontrar)
    empresa = await selecionar_empresa(creator, extraction.empresa_alvo)
    if not empresa:
        # Limpa apenas a empresa_alvo incorreta, preservando assunto, descrição, operador e prints!
        rascunho.empresa_alvo = None
        console.print(
            Panel(
                "[bold yellow]Empresa não selecionada.[/bold yellow]\n\n"
                "[dim]O rascunho atual (assunto, descrição, mesa, operador e print) foi [bold green]MANTIDO[/bold green]!\n"
                "Digite o nome correto da empresa no próximo comando para prosseguir.[/dim]",
                title="[yellow]Rascunho Preservado[/yellow]",
                border_style="yellow",
            )
        )
        return False

    # 5. Tratamento de Operador (chamado_tecnico) com Catálogo Oficial da Upgrade TI
    operador_solicitado = extraction.chamado_tecnico
    operador_email_final: Optional[str] = None
    operador_display: Optional[str] = operador_solicitado

    if operador_solicitado:
        candidatos_op = cache.buscar_operadores_candidatos(operador_solicitado)

        if len(candidatos_op) == 1:
            # Apenas 1 operador oficial correspondente (ex: Felipe Xavier, Rodrigo Mellino, etc.)
            op_nome, op_email = candidatos_op[0]
            operador_display = op_nome

            if op_email:
                operador_email_final = op_email
                console.print(
                    f"[dim green]✓ Operador oficial identificado:[/dim green] "
                    f"[bold cyan]{op_nome}[/bold cyan] ([dim]{op_email}[/dim])"
                )
            else:
                # Operador oficial cadastrado mas com e-mail pendente (ex: Wander Cristiano)
                console.print(
                    Panel(
                        f"[bold yellow]Operador Oficial Localizado:[/bold yellow] [bold white]{op_nome}[/bold white]\n\n"
                        f"[dim]Este operador ainda não possui e-mail oficial cadastrado no catálogo.\n"
                        f"A API do Milvus exige obrigatoriamente o e-mail oficial para atribuição do chamado.\n"
                        f"Informe o e-mail abaixo para cadastrar definitivamente no catálogo e enviar ao Milvus\n"
                        f"(ou pressione Enter para prosseguir sem atribuição):[/dim]",
                        title="[yellow]Atenção: E-mail do Operador Pendente[/yellow]",
                        border_style="yellow",
                    )
                )
                try:
                    novo_email = Prompt.ask(
                        f"[bold cyan]Digite o e-mail oficial de {op_nome}[/bold cyan]",
                        default="",
                    ).strip()
                except (KeyboardInterrupt, EOFError):
                    novo_email = ""

                if novo_email and "@" in novo_email and " " not in novo_email:
                    cache.definir_email_operador(op_nome, novo_email)
                    operador_email_final = novo_email
                    console.print(f"[dim green]✓ E-mail cadastrado com sucesso para {op_nome}: {novo_email}[/dim green]")
                else:
                    operador_email_final = None

        elif len(candidatos_op) > 1:
            # Múltiplos operadores: lista numerada de candidatos únicos oficiais (ex: Gabriel Oliveira / Gabriel Sena / Gabryel Santos)
            console.print(
                f"\n[bold yellow]Foram encontrados {len(candidatos_op)} operadores oficiais correspondentes a '{operador_solicitado}':[/bold yellow]"
            )
            tabela_op = Table(title=f"Operadores Oficiais ({operador_solicitado})", border_style="yellow")
            tabela_op.add_column("#", justify="center", style="bold cyan")
            tabela_op.add_column("Operador / Nome Oficial", style="bold white")
            tabela_op.add_column("E-mail Oficial (chamado_tecnico)", style="green")

            for idx, (n_op, e_op) in enumerate(candidatos_op, start=1):
                e_label = e_op if e_op else "[dim red]Pendente[/dim red]"
                tabela_op.add_row(str(idx), n_op, e_label)

            console.print(tabela_op)

            try:
                escolha_op = Prompt.ask(
                    f"[bold cyan]Escolha o número do operador (1 a {len(candidatos_op)}) ou Enter para manter '{operador_solicitado}'[/bold cyan]",
                    default="",
                ).strip()
            except (KeyboardInterrupt, EOFError):
                escolha_op = ""

            if escolha_op.isdigit() and 1 <= int(escolha_op) <= len(candidatos_op):
                sel_nome, sel_email = candidatos_op[int(escolha_op) - 1]
                operador_display = sel_nome
                operador_email_final = sel_email
                if not sel_email:
                    try:
                        novo_email = Prompt.ask(
                            f"[bold cyan]Digite o e-mail oficial de {sel_nome} (ou Enter para pular)[/bold cyan]",
                            default="",
                        ).strip()
                    except (KeyboardInterrupt, EOFError):
                        novo_email = ""
                    if novo_email and "@" in novo_email and " " not in novo_email:
                        cache.definir_email_operador(sel_nome, novo_email)
                        operador_email_final = novo_email
                console.print(
                    f"[dim green]✓ Operador selecionado:[/dim green] [bold]{sel_nome}[/bold] ({operador_email_final or '[dim]sem e-mail[/dim]'})"
                )
            else:
                operador_email_final = operador_solicitado if "@" in operador_solicitado else None
        else:
            # Nenhum operador oficial localizado pelo termo
            if "@" in operador_solicitado and " " not in operador_solicitado:
                operador_email_final = operador_solicitado.strip().lower()
            else:
                op_local = creator.resolver_tecnico_local(operador_solicitado)
                msg_local = f"\n[dim]Operador localizado no catálogo: [bold green]{op_local}[/bold green][/dim]" if op_local else ""
                console.print(
                    Panel(
                        f"[bold yellow]Aviso sobre Atribuição de Operador no Milvus:[/bold yellow]\n"
                        f"O operador solicitado foi: '[bold cyan]{operador_solicitado}[/bold cyan]'.{msg_local}\n\n"
                        f"[dim]A API do Milvus exige obrigatoriamente o [bold white]e-mail oficial[/bold white] cadastrado do operador.\n"
                        f"Informe o e-mail abaixo para cadastrar no catálogo e enviar ao Milvus;\n"
                        f"ou use o comando [bold cyan]/definir-email Nome | email@dominio[/bold cyan].[/dim]",
                        title="[yellow]Atenção: E-mail Oficial do Operador[/yellow]",
                        border_style="yellow",
                    )
                )
                try:
                    novo_email = Prompt.ask(
                        f"[bold cyan]Digite o e-mail do operador para envio ao Milvus (ou Enter para ignorar)[/bold cyan]",
                        default="",
                    ).strip()
                except (KeyboardInterrupt, EOFError):
                    novo_email = ""

                if novo_email and "@" in novo_email and " " not in novo_email:
                    cache.definir_email_operador(operador_solicitado, novo_email)
                    operador_email_final = novo_email
                    console.print(f"[dim green]✓ Operador '{operador_solicitado}' cadastrado: '{novo_email}'.[/dim green]")
                elif novo_email:
                    console.print("[yellow]E-mail inválido. Mantendo sem atribuição.[/yellow]")
                    operador_email_final = None
                else:
                    operador_email_final = None

    if operador_email_final:
        rascunho.chamado_tecnico = operador_email_final

    # 6. Construção do Payload do Milvus
    payload = MilvusCreateTicketPayload(
        cliente_id=empresa["token"],
        chamado_assunto=extraction.chamado_assunto,
        chamado_descricao=extraction.chamado_descricao,
        is_b2c=False,
        chamado_contato=extraction.chamado_contato,
        chamado_telefone=extraction.chamado_telefone,
        chamado_email=extraction.chamado_email,
        chamado_tecnico=operador_email_final,
        chamado_mesa=extraction.chamado_mesa,
    )

    # 7. Painel de Conferência Pré-Abertura
    exibir_painel_conferencia(empresa, payload, rascunho.anexo_paths, operador_nome_display=operador_display)

    # 8. Confirmação Humana Obrigatória (3 opções claras: s, e, c)
    console.print(
        "\n[bold yellow]AÇÕES DISPONÍVEIS:[/bold yellow]\n"
        "  [bold green]'s'[/bold green] -> Confirmar e criar o chamado no Milvus (envia prints e finaliza rascunho)\n"
        "  [bold cyan]'e'[/bold cyan] -> Continuar editando no chat (mantém 100% do rascunho ativo)\n"
        "  [bold red]'c'[/bold red] -> Cancelar e limpar o rascunho atual"
    )
    try:
        confirma = Prompt.ask(
            "[bold white]Escolha uma ação[/bold white] ([bold green]s[/bold green]/[bold cyan]e[/bold cyan]/[bold red]c[/bold red])",
            choices=["s", "e", "c"],
            default="e",
        ).strip().lower()
    except (KeyboardInterrupt, EOFError):
        confirma = "e"

    if confirma == "e":
        console.print(
            "\n[bold cyan]✓ Modo de edição mantido.[/bold cyan] "
            "[dim]O rascunho continua 100% ativo. Digite o que deseja alterar ou adicionar no chat "
            "(ex: 'muda a mesa para Suporte N2' ou 'acrescente o telefone 1199999999').[/dim]\n"
        )
        return False

    if confirma == "c":
        rascunho.limpar()
        console.print("\n[yellow]✓ Rascunho cancelado e limpo pelo operador. Nenhum dado foi gravado no Milvus.[/yellow]\n")
        return False

    # 9. Execução do POST /chamado/criar
    with console.status("[bold green]Criando chamado no Milvus Helpdesk (respeitando rate limit)...[/bold green]"):
        try:
            resposta_milvus = await creator.criar_chamado(payload)
        except CreationSafetyError as se:
            console.print(f"[bold red]VIOLAÇÃO DE SEGURANÇA:[/bold red] {se}")
            return False
        except Exception as api_err:
            console.print(f"[bold red]Erro ao criar chamado no Milvus:[/bold red] {api_err}")
            return False

    # 10. Apresentação do Sucesso
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
        f"[bold white]Código / ID Milvus:[/bold white] [bold cyan]{chamado_id}[/bold cyan]\n"
        f"[bold white]Empresa:[/bold white] {empresa['nome']} ({empresa['token']})\n"
        f"[bold white]Assunto:[/bold white] {payload.chamado_assunto}\n"
        f"[bold white]Mesa de Trabalho:[/bold white] {payload.chamado_mesa or '[dim]Padrão[/dim]'}\n"
        f"[bold white]Operador Atribuído:[/bold white] {payload.chamado_tecnico or '[dim]Não atribuído[/dim]'}\n"
        f"[bold white]Retorno Oficial Milvus:[/bold white] [bold yellow]{resposta_exibicao}[/bold yellow]"
    )

    console.print(
        Panel(
            detalhes_sucesso,
            title="[bold green]Sucesso - Chamado Aberto no Milvus[/bold green]",
            border_style="green",
        )
    )

    # 11. Upload Resiliente do(s) Print(s) Anexo(s) salvo(s) no rascunho
    if rascunho.anexo_paths and chamado_id and chamado_id != "Gerado":
        for idx_anexo, anexo_str in enumerate(rascunho.anexo_paths, start=1):
            p_anexo = Path(anexo_str)
            if not p_anexo.exists():
                continue

            enviar_anexo = Confirm.ask(
                f"\nDeseja anexar o print '{p_anexo.name}' ao chamado {chamado_id} agora?",
                default=True,
            )
            if enviar_anexo:
                await aguardar_com_contagem(
                    segundos=settings.milvus_rate_limit_seconds,
                    motivo=f"rate limit de segurança antes do upload do anexo ({p_anexo.name})",
                )

                sucesso_upload = False
                with console.status(f"[bold green]Enviando anexo '{p_anexo.name}' para o chamado {chamado_id}...[/bold green]"):
                    try:
                        res_anexo = await creator.anexar_arquivo(chamado_id, p_anexo)
                        console.print(f"[bold green]✓ Anexo enviado com sucesso com ID {chamado_id}:[/bold green] {res_anexo}\n")
                        sucesso_upload = True
                    except Exception as anexo_err:
                        console.print(
                            f"[yellow]Tentativa de upload com ID '{chamado_id}' falhou ({anexo_err}).\n"
                            f"O Milvus pode exigir o ID interno de 7/8 dígitos. Buscando ID interno via listagem...[/yellow]"
                        )

                if not sucesso_upload:
                    await aguardar_com_contagem(
                        segundos=settings.milvus_rate_limit_seconds,
                        motivo="rate limit antes da busca do ID interno no Milvus",
                    )
                    try:
                        with console.status(f"[bold green]Consultando ID interno para o chamado {chamado_id}...[/bold green]"):
                            id_interno = await creator.buscar_id_interno_chamado(chamado_id)

                        if id_interno and str(id_interno) != str(chamado_id):
                            console.print(f"[dim green]✓ ID interno de 7/8 dígitos localizado no Milvus: [bold]{id_interno}[/bold][/dim green]")
                            await aguardar_com_contagem(
                                segundos=settings.milvus_rate_limit_seconds,
                                motivo="rate limit antes do envio com ID interno",
                            )
                            with console.status(f"[bold green]Reenviando anexo com ID interno {id_interno}...[/bold green]"):
                                res_anexo2 = await creator.anexar_arquivo(id_interno, p_anexo)
                                console.print(f"[bold green]✓ Anexo enviado com sucesso usando ID interno {id_interno}:[/bold green] {res_anexo2}\n")
                        else:
                            console.print(f"[bold red]Não foi possível recuperar ID interno alternativo para o código {chamado_id}.[/bold red]\n")
                    except Exception as fallback_err:
                        console.print(f"[bold red]Falha no fallback de anexo com ID interno:[/bold red] {fallback_err}\n")

    # 12. Chamado efetivamente criado com sucesso: limpa o rascunho ativo da sessão
    rascunho.limpar()
    console.print("[dim green]✓ Chamado concluído com sucesso e rascunho limpo.[/dim green]\n")
    return True


def exibir_cabecalho_dossie(dossie: dict[str, Any]) -> None:
    """Exibe cabeçalho rico e elegante com os metadados oficiais e frescos do chamado no Milvus."""
    chamado = dossie.get("chamado") or {}
    codigo = dossie.get("codigo") or chamado.get("codigo") or "-"
    cliente = chamado.get("cliente") or "-"
    tecnico = chamado.get("tecnico") or "[dim]Não atribuído[/dim]"
    mesa = chamado.get("mesa_trabalho") or "[dim]Padrão[/dim]"
    status = chamado.get("status") or "Aberto"
    if chamado.get("motivo_pausa"):
        status = f"{status} ([dim yellow]{chamado.get('motivo_pausa')}[/dim yellow])"
    prioridade = chamado.get("prioridade") or "-"

    cat_prim = chamado.get("categoria_primaria") or ""
    cat_sec = chamado.get("categoria_secundaria") or ""
    if cat_prim and cat_sec:
        categoria = f"{cat_prim} / {cat_sec}"
    elif cat_prim:
        categoria = str(cat_prim)
    else:
        categoria = "[dim]N/D[/dim]"

    horas = chamado.get("total_horas") or "[dim]-[/dim]"

    aval = chamado.get("total_avaliacao")
    if aval is not None and str(aval).strip():
        aval_str = f"[bold yellow]★ {aval}/5[/bold yellow]"
    else:
        aval_str = "[dim]-[/dim]"

    # SLA Solução formatado
    sla = chamado.get("status_sla_solucao")
    if isinstance(sla, dict):
        sla_st = sla.get("status") or sla.get("descricao") or "Sem SLA"
        sla_pct = sla.get("porcentagem") or sla.get("percentual")
        sla_str = f"{sla_st} ({sla_pct}%)" if sla_pct is not None else str(sla_st)
    elif sla:
        sla_str = str(sla)
    else:
        sla_str = "Sem SLA"

    total_acomp = dossie.get("total_acompanhamentos", 0)
    origem = "cache local" if dossie.get("origem_cache_acompanhamentos") else "API Milvus"

    tabela = Table(title=f"Metadados Oficiais - Chamado #{codigo}", border_style="cyan")
    tabela.add_column("Código", style="bold cyan", justify="center")
    tabela.add_column("Empresa / Cliente", style="green")
    tabela.add_column("Operador", style="bold white")
    tabela.add_column("Mesa", style="magenta")
    tabela.add_column("Status", style="yellow")
    tabela.add_column("Prioridade", style="blue")
    tabela.add_column("Categoria", style="cyan")
    tabela.add_column("Horas", style="bold white", justify="center")
    tabela.add_column("Avaliação ★", justify="center")
    tabela.add_column("SLA Solução", style="bold yellow")
    tabela.add_column("Comentários", style="dim", justify="center")

    tabela.add_row(
        f"#{codigo}",
        str(cliente),
        str(tecnico),
        str(mesa),
        str(status),
        str(prioridade),
        str(categoria),
        str(horas),
        aval_str,
        sla_str,
        f"{total_acomp} ({origem})",
    )
    console.print("\n")
    console.print(tabela)


def exibir_parecer_advisor(conteudo_md: str, titulo: str) -> None:
    """Renderiza o parecer analítico do Ticket Manager formatado em Rich Markdown."""
    console.print("\n")
    console.print(
        Panel(
            Markdown(conteudo_md),
            title=f"[bold cyan]🎯 {titulo}[/bold cyan]",
            border_style="cyan",
            padding=(1, 2),
        )
    )
    console.print("\n")


async def executar_analise_chamado(
    codigo: str,
    pergunta: str,
    ro_client: MilvusReadOnlyClient,
    advisor: TicketAdvisorEngine,
    cache: LocalCache,
    forcar_api: bool = False,
    ultimo_analisado: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Executa a leitura do dossiê completo e gera o parecer do Ticket Manager."""
    with console.status(f"[bold green]Carregando dossiê completo do chamado #{codigo}...[/bold green]"):
        try:
            dossie = await ro_client.obter_dossie_completo_chamado(
                codigo=codigo,
                forcar_api=forcar_api,
                cache=cache,
            )
        except Exception as e:
            console.print(f"[bold red]Erro ao carregar dossiê do chamado #{codigo}:[/bold red] {e}")
            return ultimo_analisado or {}

    exibir_cabecalho_dossie(dossie)

    historico: list[dict[str, str]] = []
    if ultimo_analisado and str(ultimo_analisado.get("codigo")) == str(codigo):
        historico = ultimo_analisado.get("historico", [])

    with console.status("[bold green]Analisando chamado com o Copiloto do Ticket Manager (ITIL)...[/bold green]"):
        parecer = advisor.analisar_chamado_profundo(
            dossie=dossie,
            pergunta_gestor=pergunta,
            historico_conversa_chamado=historico,
        )

    exibir_parecer_advisor(parecer, f"Copiloto Ticket Manager - Parecer do Chamado #{dossie['codigo']}")

    historico.append({"pergunta": pergunta, "resposta": parecer})
    return {
        "codigo": str(codigo),
        "dossie": dossie,
        "historico": historico,
    }


async def executar_analise_carteira(
    termo_operador: str,
    pergunta: str,
    advisor: TicketAdvisorEngine,
    cache: LocalCache,
) -> None:
    """Filtra no DuckDB os chamados abertos do operador e gera o Plano Tático de Condução."""
    candidatos = cache.buscar_operadores_candidatos(termo_operador)
    if not candidatos:
        console.print(
            f"[yellow]Operador '{termo_operador}' não encontrado no catálogo oficial da Upgrade TI.[/yellow]\n"
            f"[dim]Use '/operadores' para listar os operadores cadastrados.[/dim]"
        )
        return

    nome_oficial, _ = candidatos[0]
    console.print(f"[dim green]✓ Operador oficial identificado: [bold cyan]{nome_oficial}[/bold cyan][/dim green]")

    df_todos = cache.obter_chamados_df()
    if df_todos.empty:
        console.print("[yellow]Nenhum chamado armazenado no DuckDB local. Execute '/sincronizar' primeiro.[/yellow]")
        return

    primeiro_nome = nome_oficial.split()[0].lower()
    mask_op = df_todos["tecnico"].astype(str).str.lower().str.contains(primeiro_nome, na=False)

    status_fechados = ["finalizado", "resolvido", "fechado", "cancelado"]
    mask_aberto = ~df_todos["status"].astype(str).str.lower().isin(status_fechados)

    df_op = df_todos[mask_op & mask_aberto]

    with console.status(f"[bold green]Gerando Plano Tático da carteira de {nome_oficial}...[/bold green]"):
        parecer = advisor.analisar_carteira_operador(
            nome_operador=nome_oficial,
            chamados_df=df_op,
            pergunta_gestor=pergunta,
        )

    exibir_parecer_advisor(parecer, f"Plano Tático da Carteira - {nome_oficial}")


def identificar_intencao_usuario(
    entrada: str,
    tem_anexo: bool,
    rascunho_ativo: bool,
    ultimo_analisado: Optional[dict[str, Any]],
    cache: LocalCache,
) -> tuple[str, dict[str, Any]]:
    """Identifica cirurgicamente a intenção da entrada sem conflitar com abertura de chamados."""
    entrada_strip = entrada.strip()
    entrada_lower = entrada_strip.lower()

    # 1. Comandos diretos de análise
    if entrada_lower.startswith("/analisar") or entrada_lower.startswith("/analise"):
        cmd = "/analisar" if entrada_lower.startswith("/analisar") else "/analise"
        resto = entrada_strip[len(cmd):].strip()
        cod = ""
        perg = ""
        if resto:
            partes = resto.split(maxsplit=1)
            cod = partes[0].lstrip("#").strip()
            perg = partes[1].strip() if len(partes) > 1 else ""
        if not cod:
            cod = Prompt.ask("[bold cyan]Digite o código do chamado para analisar[/bold cyan]").lstrip("#").strip()
        return "analisar_chamado", {"codigo": cod, "pergunta": perg, "forcar_api": False}

    if entrada_lower.startswith("/atualizar") or entrada_lower.startswith("/refresh"):
        cmd = "/atualizar" if entrada_lower.startswith("/atualizar") else "/refresh"
        resto = entrada_strip[len(cmd):].strip()
        cod = ""
        perg = ""
        if resto:
            partes = resto.split(maxsplit=1)
            cod = partes[0].lstrip("#").strip()
            perg = partes[1].strip() if len(partes) > 1 else ""
        if not cod:
            cod = Prompt.ask("[bold cyan]Digite o código do chamado para atualizar via API[/bold cyan]").lstrip("#").strip()
        return "analisar_chamado", {"codigo": cod, "pergunta": perg, "forcar_api": True}

    if entrada_lower.startswith("/carteira") or entrada_lower.startswith("/fila"):
        cmd = "/carteira" if entrada_lower.startswith("/carteira") else "/fila"
        resto = entrada_strip[len(cmd):].strip()
        op = ""
        perg = ""
        if "|" in resto:
            p_op, p_perg = resto.split("|", 1)
            op = p_op.strip()
            perg = p_perg.strip()
        else:
            for op_nome, _ in cache.listar_operadores_oficiais():
                if normalizar_para_busca(op_nome) in normalizar_para_busca(resto):
                    op = op_nome
                    perg = resto
                    break
            if not op:
                op = resto
        if not op:
            op = Prompt.ask("[bold cyan]Digite o nome do operador[/bold cyan]").strip()
        return "analisar_carteira", {"operador": op, "pergunta": perg}

    # 2. Se houver anexo (@colar / @arquivo) -> É Abertura de Chamado!
    if tem_anexo:
        return "comando_abertura", {}

    # 3. Se usuário pedir explicitamente para abrir novo chamado -> É Abertura de Chamado!
    if re.search(r"(?i)\b(abrir\s+chamado|novo\s+chamado|criar\s+chamado|novo\s+ticket|abertura\s+de\s+chamado)\b", entrada_strip):
        return "comando_abertura", {}

    # 4. Detecção de análise de chamado por código (#12345 ou 12345 com intenção de consulta)
    match_cod = re.search(r"#?(\d{4,8})\b", entrada_strip)
    if match_cod:
        cod_encontrado = match_cod.group(1)
        tem_intencao_consulta = bool(
            re.search(
                r"(?i)(analis|entend|resum|verifiq|verificar|audit|orient|dossi[eê]|aconteceu|situac|situaç|status|como\s+est[aá]|o\s+que\s+houve|detalhe|expliq|hist[oó]rico|coment[aá]rio)",
                entrada_strip,
            )
        )
        if tem_intencao_consulta or entrada_lower in (f"#{cod_encontrado}", cod_encontrado, f"chamado #{cod_encontrado}", f"chamado {cod_encontrado}"):
            return "analisar_chamado", {"codigo": cod_encontrado, "pergunta": entrada_strip, "forcar_api": False}

    # 5. Detecção de análise de carteira de operador em linguagem natural
    tem_intencao_carteira = bool(
        re.search(
            r"(?i)\b(carteira|fila|chamados\s+d[eo]|como\s+est[aã]o\s+os\s+chamados|operar\s+o|conduzir\s+o|desempenho\s+d[eo])\b",
            entrada_strip,
        )
    )
    if tem_intencao_carteira:
        for op_nome, _ in cache.listar_operadores_oficiais():
            if (
                normalizar_para_busca(op_nome) in normalizar_para_busca(entrada_strip)
                or normalizar_para_busca(op_nome.split()[0]) in normalizar_para_busca(entrada_strip)
            ):
                return "analisar_carteira", {"operador": op_nome, "pergunta": entrada_strip}

    # 6. Continuação inteligente de conversa sobre o último chamado analisado
    if ultimo_analisado and not rascunho_ativo:
        return "continuacao_chamado", {
            "codigo": ultimo_analisado["codigo"],
            "pergunta": entrada_strip,
        }

    # 7. Caso padrão (inclui rascunho ativo ou pedido geral de abertura)
    return "comando_abertura", {}


async def main() -> None:
    """Ponto de entrada do Agente Atendente e Copiloto Ticket Manager."""
    exibir_cabecalho()

    settings = get_settings()
    if not settings.gemini_api_key and not settings.nvidia_api_key:
        console.print(
            "[bold red]ERRO DE CONFIGURAÇÃO:[/bold red] Nenhuma chave de IA (GEMINI_API_KEY ou NVIDIA_API_KEY) foi encontrada no `.env`.\n"
            "Configure as chaves no arquivo `.env` para utilizar o Agente Atendente."
        )
        return

    extractor = GeminiTicketExtractor(settings=settings)
    advisor = TicketAdvisorEngine(settings=settings)
    cache = LocalCache(settings=settings)

    # Catálogo Oficial de Operadores Ativos da Upgrade TI
    operadores_iniciais = cache.listar_operadores_oficiais()
    console.print(f"[dim green]✓ {len(operadores_iniciais)} operadores ativos no catálogo oficial da Upgrade TI.[/dim green]")

    async with MilvusTicketCreatorClient(settings=settings) as creator, MilvusReadOnlyClient(settings=settings) as ro_client:
        # Modo Linha de Comando (CLI)
        if len(sys.argv) > 1:
            comando_cli = " ".join(sys.argv[1:])
            console.print(f"[dim]Processando solicitação via linha de comando:[/dim] [cyan]{comando_cli}[/cyan]\n")
            try:
                _, img_cli = processar_anexos_comando(comando_cli)
                intencao_cli, params_cli = identificar_intencao_usuario(
                    comando_cli,
                    tem_anexo=img_cli is not None,
                    rascunho_ativo=False,
                    ultimo_analisado=None,
                    cache=cache,
                )
                if intencao_cli == "analisar_chamado":
                    await executar_analise_chamado(
                        params_cli["codigo"],
                        params_cli.get("pergunta") or comando_cli,
                        ro_client,
                        advisor,
                        cache,
                        forcar_api=params_cli.get("forcar_api", False),
                    )
                elif intencao_cli == "analisar_carteira":
                    await executar_analise_carteira(
                        params_cli["operador"],
                        params_cli.get("pergunta") or comando_cli,
                        advisor,
                        cache,
                    )
                else:
                    rascunho_cli = RascunhoSessao()
                    await processar_solicitacao(comando_cli, extractor, creator, cache, settings, rascunho_cli)
            except Exception as e:
                console.print(f"[bold red]Erro no processamento da solicitação CLI:[/bold red] {e}\n")
            return

        # Modo Interativo (REPL) Multi-modo
        rascunho_atual = RascunhoSessao()
        ultimo_chamado_analisado: Optional[dict[str, Any]] = None

        console.print("[dim]Digite sua solicitação (abertura com @colar ou consulta como '/analisar 69118'), ou 'sair'.[/dim]\n")

        while True:
            try:
                if rascunho_atual.esta_ativo():
                    prompt_label = "[bold yellow]Anakin [Rascunho Ativo - /cancelar p/ limpar][/bold yellow]"
                elif ultimo_chamado_analisado:
                    prompt_label = f"[bold cyan]Anakin [Ticket Manager #{ultimo_chamado_analisado['codigo']}][/bold cyan]"
                else:
                    prompt_label = "[bold cyan]Anakin [Atendente][/bold cyan]"
                entrada = Prompt.ask(prompt_label).strip()
            except (KeyboardInterrupt, EOFError):
                console.print("\n[yellow]Sessão encerrada pelo operador.[/yellow]")
                break

            if not entrada:
                if rascunho_atual.esta_ativo():
                    exibir_painel_rascunho(rascunho_atual, "Rascunho atual em andamento:")
                continue

            entrada_lower = entrada.lower()
            if entrada_lower in ("sair", "exit", "quit", "q"):
                console.print("[yellow]Encerrando o Agente Atendente. Até logo![/yellow]")
                break

            if entrada_lower in ("/cancelar", "cancelar", "/limpar", "limpar"):
                rascunho_atual.limpar()
                ultimo_chamado_analisado = None
                console.print("[yellow]✓ Sessão e rascunho limpos com sucesso.[/yellow]\n")
                continue

            if entrada_lower in ("/rascunho", "rascunho", "/draft", "draft", "/status", "status"):
                if rascunho_atual.esta_ativo():
                    exibir_painel_rascunho(rascunho_atual)
                else:
                    console.print("[yellow]Nenhum rascunho de abertura ativo no momento.[/yellow]")
                continue

            if entrada_lower in ("/sincronizar", "sincronizar", "/sync"):
                await sincronizar_operadores(settings, cache)
                continue

            if entrada_lower in ("/operadores", "operadores", "/tecnicos", "tecnicos"):
                exibir_operadores_mapeados(cache)
                continue

            if entrada_lower.startswith("/definir-email") or entrada_lower.startswith("/definiremail"):
                cmd_prefix = "/definir-email" if entrada_lower.startswith("/definir-email") else "/definiremail"
                resto = entrada[len(cmd_prefix):].strip()
                if "|" in resto:
                    partes = resto.split("|", 1)
                    nome_def = partes[0].strip()
                    email_def = partes[1].strip()
                elif " " in resto:
                    partes = resto.rsplit(" ", 1)
                    nome_def = partes[0].strip()
                    email_def = partes[1].strip()
                else:
                    nome_def = resto.strip()
                    email_def = ""

                if not nome_def:
                    try:
                        nome_def = Prompt.ask("[bold cyan]Digite o Nome do Operador[/bold cyan]").strip()
                    except (KeyboardInterrupt, EOFError):
                        nome_def = ""

                if not email_def and nome_def:
                    try:
                        email_def = Prompt.ask(f"[bold cyan]Digite o e-mail oficial para '{nome_def}'[/bold cyan]").strip()
                    except (KeyboardInterrupt, EOFError):
                        email_def = ""

                if nome_def:
                    ok, msg = cache.definir_email_operador(nome_def, email_def)
                    if ok:
                        console.print(f"[bold green]✓ {msg}[/bold green]\n")
                        exibir_operadores_mapeados(cache)
                    else:
                        console.print(f"[bold red]Falha ao definir e-mail:[/bold red] {msg}\n")
                continue

            # Roteador de Intenções
            _, img_anexo = processar_anexos_comando(entrada)
            intencao, params = identificar_intencao_usuario(
                entrada,
                tem_anexo=img_anexo is not None,
                rascunho_ativo=rascunho_atual.esta_ativo(),
                ultimo_analisado=ultimo_chamado_analisado,
                cache=cache,
            )

            try:
                if intencao == "analisar_chamado":
                    ultimo_chamado_analisado = await executar_analise_chamado(
                        codigo=params["codigo"],
                        pergunta=params.get("pergunta") or entrada,
                        ro_client=ro_client,
                        advisor=advisor,
                        cache=cache,
                        forcar_api=params.get("forcar_api", False),
                        ultimo_analisado=ultimo_chamado_analisado,
                    )
                elif intencao == "continuacao_chamado":
                    ultimo_chamado_analisado = await executar_analise_chamado(
                        codigo=params["codigo"],
                        pergunta=params["pergunta"],
                        ro_client=ro_client,
                        advisor=advisor,
                        cache=cache,
                        forcar_api=False,
                        ultimo_analisado=ultimo_chamado_analisado,
                    )
                elif intencao == "analisar_carteira":
                    await executar_analise_carteira(
                        termo_operador=params["operador"],
                        pergunta=params.get("pergunta") or entrada,
                        advisor=advisor,
                        cache=cache,
                    )
                else:
                    await processar_solicitacao(entrada, extractor, creator, cache, settings, rascunho_atual)
            except Exception as e:
                console.print(f"[bold red]Erro durante a operação:[/bold red] {e}\n")

            console.print("\n" + "─" * 80 + "\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Operação cancelada.[/yellow]")
        sys.exit(0)
