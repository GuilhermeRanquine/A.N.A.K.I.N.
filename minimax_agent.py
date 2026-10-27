import os
import sys
import json
import requests
from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

# Força codificação UTF-8 no console Windows
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stdin and hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

console = Console()

# Carrega as variáveis de ambiente do arquivo .env
load_dotenv()

API_KEY = os.getenv("NVIDIA_API_KEY")
if not API_KEY:
    console.print("[bold red]ERRO: A variável NVIDIA_API_KEY não foi encontrada no arquivo .env.[/bold red]")
    console.print("Configure a chave no arquivo .env antes de executar.")
    sys.exit(1)

INVOKE_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
MODEL = "minimaxai/minimax-m3.1-flash"

SYSTEM_INSTRUCTION = (
    "Você é um Staff Python Engineer, Arquiteto Sênior de APIs e Especialista em Análise de Dados "
    "(Pandas, DuckDB, SciPy e Matplotlib), responsável pelo código do agente Anakin (anakin-ops). "
    "Escreva sempre Python 3.12+ limpo, modular, assíncrono quando envolver rede (httpx, asyncio, aiolimiter), "
    "com Type Hints estritos e Pydantic. Para a API do Milvus, respeite sempre o rate limit de 1 requisição por minuto "
    "(62s de margem), lembre que os nomes dos campos da API do Milvus são estritamente em português conforme documentação "
    "oficial (ex: `filtro_body`, nunca `filter_body`; `chamado_assunto`, `chamado_descricao`, `cliente_id`) e que "
    "listagens usam POST com `filtro_body`. Para Google Gemini, use exclusivamente o SDK atual `google-genai >= 2.30.1` "
    "com `client.interactions.create` e o modelo `gemini-3.8-flash` (nunca use o legado `google-generativeai` nem `gemini-2.5`)."
)

OUTPUT_FILE = "ultima_resposta_agente.md"
PROMPT_FILE = "prompt_minimax.md"

def main() -> None:
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Accept": "text/event-stream",
        "Content-Type": "application/json"
    }

    messages = [
        {
            "role": "system",
            "content": SYSTEM_INSTRUCTION
        }
    ]

    console.print(Panel.fit(
        "[bold cyan]Agente Especialista Anakin (Minimax M3.1 Flash - NVIDIA NIM)[/bold cyan]\n"
        "[dim]Digite sua mensagem, [bold yellow]@prompt[/bold yellow] para enviar o arquivo prompt_minimax.md, ou [bold red]sair[/bold red] para encerrar.[/dim]",
        border_style="cyan"
    ))

    while True:
        try:
            user_input = input("\nVocê: ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Encerrando sessão.[/yellow]")
            break

        if not user_input:
            continue

        if user_input.lower() in ("sair", "exit", "quit"):
            console.print("[yellow]Encerrando sessão. Até logo![/yellow]")
            break

        # Suporte ao comando @prompt para ler do prompt_minimax.md
        if user_input.lower() == "@prompt":
            if not os.path.exists(PROMPT_FILE):
                console.print(f"[bold red]Arquivo '{PROMPT_FILE}' não encontrado na raiz.[/bold red]")
                continue
            with open(PROMPT_FILE, "r", encoding="utf-8") as pf:
                file_content = pf.read().strip()
            if not file_content:
                console.print(f"[bold yellow]O arquivo '{PROMPT_FILE}' está vazio. Escreva seu prompt nele antes de usar @prompt.[/bold yellow]")
                continue
            console.print(f"[dim cyan]Lendo prompt de '{PROMPT_FILE}' ({len(file_content)} caracteres)...[/dim cyan]")
            user_input = file_content

        messages.append({"role": "user", "content": user_input})

        payload = {
            "model": MODEL,
            "messages": messages,
            "temperature": 0.2,
            "top_p": 0.95,
            "max_tokens": 16384,
            "stream": True
        }

        console.print("\n[bold green]Agente (Streaming):[/bold green] ", end="")

        full_response_parts: list[str] = []

        try:
            with requests.post(INVOKE_URL, headers=headers, json=payload, stream=True) as response:
                response.encoding = "utf-8"
                if response.status_code != 200:
                    error_text = response.text
                    console.print(f"\n[bold red][Erro na API ({response.status_code})]:[/bold red] {error_text}")
                    messages.pop()
                    continue

                for raw_bytes in response.iter_lines(decode_unicode=False):
                    if not raw_bytes:
                        continue
                    line = raw_bytes.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue

                    if line.startswith("data: "):
                        data_str = line[6:].strip()
                    elif line.startswith("data:"):
                        data_str = line[5:].strip()
                    else:
                        continue

                    if data_str == "[DONE]":
                        break

                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    choices = chunk.get("choices")
                    if not choices or not isinstance(choices, list):
                        continue

                    delta = choices[0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        print(content, end="", flush=True)
                        full_response_parts.append(content)

            print()  # Quebra de linha após o stream

            full_response_text = "".join(full_response_parts)
            if full_response_text:
                # Armazena a resposta no histórico da conversa
                messages.append({"role": "assistant", "content": full_response_text})

                # Salva no arquivo ultima_resposta_agente.md
                with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                    f.write(full_response_text)

                # Exibe resumo formatado em Markdown com Rich
                console.print("\n")
                console.print(Panel(
                    Markdown(full_response_text),
                    title="[bold green]Visualização Formatada (Markdown)[/bold green]",
                    subtitle=f"[dim]Salvo em {OUTPUT_FILE}[/dim]",
                    border_style="green"
                ))

        except requests.RequestException as e:
            console.print(f"\n[bold red][Erro de conexão]:[/bold red] {e}")
            messages.pop()

if __name__ == "__main__":
    main()
