"""Extrator multimodal com Google Gemini e Fallback NVIDIA NIM para abertura de chamados.

Interpreta pedidos de suporte em linguagem natural e/ou prints de telas (WhatsApp, e-mail,
erros) e gera contratos estruturados (DraftTicketExtraction) com validação Pydantic.
Conta com cascata inteligente contra erro 429 / cota esgotada:
gemini-3.5-flash-lite -> gemini-3.1-flash-lite -> NVIDIA NIM (minimaxai/minimax-m3.1-flash).
"""

from pathlib import Path
from typing import Any, Optional
import base64
import json
import re
import httpx
from PIL import Image
from google import genai
from rich.console import Console

from anakin.config.settings import Settings, get_settings
from anakin.domain.ticket_creation_models import DraftTicketExtraction

console = Console()

SYSTEM_INSTRUCTION = (
    "Você é o Agente Anakin Atendente, especialista em triagem técnica e abertura cirúrgica de chamados "
    "de suporte no sistema Milvus Helpdesk. Sua missão é interpretar o texto do operador e/ou print de conversa "
    "(WhatsApp, e-mail, terminal ou tela de erro) e preencher com precisão absoluta os dados do chamado.\n\n"
    "DIRETRIZES FUNDAMENTAIS:\n"
    "1. empresa_alvo: Extraia o nome da empresa/cliente solicitante. NUNCA invente ou presuma uma empresa "
    "se não foi mencionada no texto ou na imagem. Se não souber a empresa, deixe como null.\n"
    "2. chamado_assunto: Crie um título conciso, profissional e objetivo (ex: 'Falha de login no sistema X', "
    "'Instalação de certificado digital').\n"
    "3. chamado_descricao: Escreva uma descrição técnica clara e detalhada contendo o relato do problema, "
    "mensagens de erro visíveis no print e ações já executadas.\n"
    "4. chamado_contato, chamado_telefone, chamado_email: Extraia dados da pessoa solicitante apenas se citados.\n"
    "5. chamado_tecnico: Extraia o nome ou e-mail do técnico a ser atribuído, se citado explicitamente no pedido.\n"
    "6. chamado_mesa: Extraia o nome da mesa de trabalho ou fila de atendimento, se citada explicitamente no pedido.\n"
    "7. dados_suficientes: Deve ser True EXCLUSIVAMENTE se 'empresa_alvo', 'chamado_assunto' e 'chamado_descricao' "
    "estiverem claros e definidos. Se faltar a empresa ou o problema não estiver claro, marque dados_suficientes=False.\n"
    "8. mensagem_para_operador: Se dados_suficientes for False, formule uma pergunta curta e educada indicando "
    "o que está faltando para abrir o chamado. Se for True, apresente um resumo breve do chamado."
)


def _preparar_imagem_base64(imagem_path: str | Path) -> tuple[str, str]:
    """Valida a imagem usando Pillow e retorna (mime_type, base64_str)."""
    p = Path(imagem_path).resolve()
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"Arquivo de imagem não encontrado: '{imagem_path}'")

    # Validação com Pillow
    with Image.open(p) as img:
        img_format = (img.format or "PNG").lower()
        if img_format == "jpeg":
            mime_type = "image/jpeg"
        elif img_format == "png":
            mime_type = "image/png"
        elif img_format == "webp":
            mime_type = "image/webp"
        else:
            mime_type = f"image/{img_format}"

    with open(p, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")

    return mime_type, encoded


class GeminiTicketExtractor:
    """Extrator cirúrgico de chamados com cascata resiliente Gemini e NVIDIA NIM."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        if not self.settings.gemini_api_key:
            raise ValueError(
                "A chave GEMINI_API_KEY não está configurada no ambiente ou no arquivo .env."
            )
        self.client = genai.Client(api_key=self.settings.gemini_api_key)
        self.model = self.settings.gemini_model or "gemini-3.5-flash-lite"

    def _executar_gemini_modelo(
        self,
        model_name: str,
        inputs: list[dict[str, Any]],
        texto_usuario: str,
        imagem_path: Optional[str | Path],
    ) -> str:
        """Tenta executar extração em um modelo Gemini específico."""
        json_schema = DraftTicketExtraction.model_json_schema()

        # 1. Tenta Interactions API
        try:
            interaction = self.client.interactions.create(
                model=model_name,
                system_instruction=SYSTEM_INSTRUCTION,
                input=inputs,
                response_format=[{
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": json_schema,
                }],
            )
            if interaction.output_text:
                return interaction.output_text
        except Exception as inter_err:
            err_str = str(inter_err).lower()
            if "429" in err_str or "resourceexhausted" in err_str or "too_many_requests" in err_str or "503" in err_str:
                raise inter_err

        # 2. Fallback para generate_content
        contents_list: list[Any] = []
        if imagem_path and Path(imagem_path).exists():
            with Image.open(imagem_path) as img:
                contents_list.append(img.copy())
        contents_list.append(texto_usuario)

        config = {
            "system_instruction": SYSTEM_INSTRUCTION,
            "response_mime_type": "application/json",
            "response_schema": DraftTicketExtraction,
        }
        resp = self.client.models.generate_content(
            model=model_name,
            contents=contents_list,
            config=config,
        )
        return resp.text or ""

    def _extrair_com_nvidia_nim(
        self,
        texto_usuario: str,
        imagem_path: Optional[str | Path] = None,
    ) -> DraftTicketExtraction:
        """Fallback resiliente usando NVIDIA NIM (minimaxai/minimax-m3.1-flash)."""
        nvidia_key = self.settings.nvidia_api_key
        if not nvidia_key:
            raise ValueError("Chave NVIDIA_API_KEY não configurada no .env para fallback do Extrator.")

        url = "https://integrate.api.nvidia.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {nvidia_key}",
            "Content-Type": "application/json",
        }

        schema_json = json.dumps(DraftTicketExtraction.model_json_schema(), ensure_ascii=False)
        system_prompt = (
            f"{SYSTEM_INSTRUCTION}\n\n"
            "IMPORTANTE: Você deve responder ESTRITAMENTE um objeto JSON válido, sem texto antes ou depois, "
            f"em conformidade exata com este JSON Schema Pydantic:\n{schema_json}"
        )

        user_content = texto_usuario
        if imagem_path:
            user_content = f"[Anexo de imagem informado pelo operador: {Path(imagem_path).name}]\n{user_content}"

        payload = {
            "model": "minimaxai/minimax-m3.1-flash",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.1,
            "max_tokens": 1200,
        }

        with httpx.Client(timeout=30.0) as http_client:
            resp = http_client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        raw_output = data["choices"][0]["message"]["content"]
        clean_json = raw_output.strip()
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", clean_json)
        if match:
            clean_json = match.group(1).strip()

        console.print("[dim green]✓ Extração concluída com sucesso via NVIDIA NIM (Minimax m3.1-flash).[/dim green]")
        return DraftTicketExtraction.model_validate_json(clean_json)

    def extrair_dados(
        self,
        texto_usuario: str,
        imagem_path: Optional[str | Path] = None,
    ) -> DraftTicketExtraction:
        """Executa a extração com cascata automática sem loop infinito em 429."""
        inputs: list[dict[str, Any]] = []

        if imagem_path:
            try:
                mime_type, b64_data = _preparar_imagem_base64(imagem_path)
                inputs.append({
                    "type": "image",
                    "mime_type": mime_type,
                    "data": b64_data,
                })
                console.print(f"[dim green]✓ Imagem '{Path(imagem_path).name}' carregada ({mime_type}).[/dim green]")
            except Exception as e:
                console.print(f"[yellow]Aviso: Não foi possível carregar a imagem '{imagem_path}': {e}. Prosseguindo apenas com texto.[/yellow]")

        inputs.append({
            "type": "text",
            "text": texto_usuario,
        })

        # Cascata de modelos Gemini: gemini-3.5-flash-lite -> gemini-3.1-flash-lite
        modelos_gemini = [self.model]
        if "gemini-3.1-flash-lite" not in modelos_gemini:
            modelos_gemini.append("gemini-3.1-flash-lite")

        for mod in modelos_gemini:
            try:
                raw_output = self._executar_gemini_modelo(mod, inputs, texto_usuario, imagem_path)
                if raw_output:
                    clean_json = raw_output.strip()
                    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", clean_json)
                    if match:
                        clean_json = match.group(1).strip()
                    console.print(f"[dim green]✓ Extração concluída com sucesso via Gemini ({mod}).[/dim green]")
                    return DraftTicketExtraction.model_validate_json(clean_json)
            except Exception as e:
                err_str = str(e)
                console.print(f"[yellow]Modelo Gemini '{mod}' indisponível ({err_str[:90]}...). Avançando cascata...[/yellow]")

        # Fallback definitivo para NVIDIA NIM Minimax caso a cota do Gemini esteja esgotada
        console.print("[bold cyan]Cota Gemini 429/indisponível. Ativando fallback automático NVIDIA NIM (Minimax m3.1-flash)...[/bold cyan]")
        return self._extrair_com_nvidia_nim(texto_usuario, imagem_path)
