"""Motor de Triagem Autônoma de Chamados com Inteligência Artificial.

Utiliza Google Gemini com fallback automático para NVIDIA NIM (Minimax) para:
1. Classificar a Prioridade (Crítico, Alto, Médio, Baixo) baseada em impacto de negócio.
2. Classificar o Tipo de Ticket (Requisição vs Incidente).
3. Selecionar a melhor Categoria Primária e Secundária a partir do catálogo real homologado no DuckDB.
4. Extrair dados do solicitante (nome, e-mail e telefone) do texto e da assinatura do chamado.
"""

from pathlib import Path
from typing import Any, Optional
import json
import re
import unicodedata
import httpx
from google import genai
from pydantic import BaseModel, Field
from rich.console import Console

from anakin.config.settings import Settings, get_settings
from anakin.domain.ticket_models import TicketSummary
from anakin.storage.local_cache import LocalCache

console = Console()


class ResultadoTriagemIA(BaseModel):
    """Contrato estruturado de retorno da triagem de chamado pela IA."""

    prioridade: str = Field(
        description="Prioridade classificada com base no impacto: 'Crítico', 'Alta' (ou 'Alto'), 'Média' (ou 'Médio') ou 'Baixa' (ou 'Baixo')"
    )
    tipo_ticket: str = Field(
        description="Tipo classificado: 'Requisição' (solicitação/acesso) ou 'Incidente' (falha/parou)"
    )
    categoria_primaria: str = Field(
        description="Categoria primária escolhida estritamente do catálogo da Upgrade TI"
    )
    categoria_secundaria: Optional[str] = Field(
        default=None,
        description="Categoria secundária/subcategoria correspondente do catálogo da Upgrade TI",
    )
    contato_nome: Optional[str] = Field(
        default=None,
        description="Nome completo do colaborador solicitante extraído do chamado",
    )
    contato_email: Optional[str] = Field(
        default=None,
        description="E-mail de contato do colaborador solicitante",
    )
    contato_telefone: Optional[str] = Field(
        default=None,
        description="Telefone, ramal ou celular do colaborador solicitante",
    )
    justificativa_prioridade: str = Field(
        default="",
        description="Justificativa concisa da classificação de prioridade",
    )
    confianca: float = Field(
        default=1.0,
        description="Grau de confiança da IA na triagem (0.0 a 1.0)",
    )

    @property
    def prioridade_id(self) -> str:
        """Retorna o ID numérico ('1', '2', '3' ou '4') mantido para compatibilidade."""
        return obter_prioridade_id(self.prioridade)

    @property
    def prioridade_formatada(self) -> str:
        """Retorna o nome textual da prioridade (ex: 'Médio')."""
        return self.prioridade


PRIORIDADE_ID_MAP: dict[str, str] = {
    "baixo": "1",
    "baixa": "1",
    "médio": "2",
    "medio": "2",
    "média": "2",
    "media": "2",
    "alto": "3",
    "alta": "3",
    "crítico": "4",
    "critico": "4",
    "crítica": "4",
    "critica": "4",
}


def obter_prioridade_id(prioridade_raw: str | int | None) -> str:
    """Converte string ou int de prioridade para o ID numérico oficial do Milvus ('1', '2', '3' ou '4')."""
    if not prioridade_raw:
        return "2"
    val_str = str(prioridade_raw).strip().lower()
    if val_str in ("1", "2", "3", "4"):
        return val_str
    nfkd = unicodedata.normalize("NFKD", val_str)
    sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()
    return PRIORIDADE_ID_MAP.get(sem_acento, PRIORIDADE_ID_MAP.get(val_str, "2"))


def normalizar_prioridade(prioridade_raw: str | int | None) -> str:
    """Padroniza strings de prioridade para os quatro valores textuais oficiais da Upgrade TI no Milvus."""
    if not prioridade_raw:
        return "Médio"
    nfkd = unicodedata.normalize("NFKD", str(prioridade_raw))
    p = "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()
    if any(k in p for k in ["crit", "urgente", "emergencia", "gravissimo", "parada", "4"]):
        return "Crítico"
    elif any(k in p for k in ["alt", "high", "3"]):
        return "Alto"
    elif any(k in p for k in ["baix", "low", "plan", "1"]):
        return "Baixo"
    return "Médio"


def normalizar_tipo_ticket(tipo_raw: str | None) -> str:
    """Padroniza strings de tipo para 'Requisição' ou 'Incidente'."""
    if not tipo_raw:
        return "Incidente"
    nfkd = unicodedata.normalize("NFKD", str(tipo_raw))
    t = "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()
    if any(k in t for k in ["req", "solicita", "acesso", "duvida"]):
        return "Requisição"
    return "Incidente"


class TicketTriageAI:
    """Motor de triagem técnica e classificação autônoma de chamados com IA."""

    def __init__(
        self,
        settings: Settings | None = None,
        cache: LocalCache | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.cache = cache or LocalCache(settings=self.settings)
        self.gemini_key = self.settings.gemini_api_key
        self.model = self.settings.gemini_model or "gemini-3.5-flash-lite"
        self._gemini_client: Optional[genai.Client] = None

        if self.gemini_key:
            try:
                self._gemini_client = genai.Client(api_key=self.gemini_key)
            except Exception as e:
                console.print(f"[dim yellow]Aviso ao inicializar cliente Gemini: {e}[/dim yellow]")

    def _obter_prompt_sistema(self, texto_catalogo: str) -> str:
        """Monta o system prompt enriquecido com o catálogo de categorias homologadas."""
        return (
            "Você é o Especialista em Triagem e Classificação de Chamados do A.N.A.K.I.N (Upgrade TI).\n"
            "Sua responsabilidade é analisar os dados brutos de um chamado aberto no Milvus Helpdesk "
            "e classificá-lo tecnicamente com máxima precisão.\n\n"
            "REGRAS DE CLASSIFICAÇÃO:\n\n"
            "1. PRIORIDADE (classificação OBRIGATÓRIA em uma das 4 opções baseadas no impacto de negócio):\n"
            "   - 'Crítico': Parada total de operação da empresa, servidor ou rede central inoperante, faturamento interrompido.\n"
            "   - 'Alta': Problema urgente afetando múltiplos colaboradores ou diretoria sem contorno viável.\n"
            "   - 'Média': Falha em estação de trabalho individual com perda parcial de produtividade (ex: ERP lento, erro em software de uso diário).\n"
            "   - 'Baixa': Dúvidas, pequenas configurações, solicitações com prazo flexível ou de baixa urgência.\n\n"
            "2. TIPO DE TICKET:\n"
            "   - 'Requisição': Solicitação de novo serviço, criação/deleção de conta, instalação de software padrão, concessão de acesso ou dúvida.\n"
            "   - 'Incidente': Algo que funcionava normalmente e parou/quebrou (falha, erro, lentidão, tela azul, travamento, desconexão).\n\n"
            "3. CATEGORIA PRIMÁRIA E SECUNDÁRIA:\n"
            "   Você DEVE selecionar a Categoria Primária e Secundária ESTRITAMENTE a partir do catálogo real homologado da empresa abaixo:\n"
            "--- CATÁLOGO HOMOLOGADO UPGRADE TI ---\n"
            f"{texto_catalogo}\n"
            "--- FIM DO CATÁLOGO ---\n"
            "Selecione a categoria e subcategoria que melhor descrevem a causa-raiz ou o ativo envolvido.\n\n"
            "4. EXTRAÇÃO DE CONTATO:\n"
            "   Examine o texto da descrição, assinaturas de e-mail e dados do chamado para identificar o colaborador solicitante:\n"
            "   - contato_nome: Nome da pessoa (ex: 'João Silva')\n"
            "   - contato_email: E-mail profissional\n"
            "   - contato_telefone: Telefone, ramal ou celular (ex: '(21) 99999-9999')\n"
            "   Se algum dado não for encontrado, retorne null no respectivo campo.\n"
        )

    def _executar_gemini(
        self,
        prompt_sistema: str,
        dados_chamado_str: str,
    ) -> Optional[str]:
        """Tenta executar a extração via Gemini."""
        if not self._gemini_client:
            return None

        modelos = [self.model, "gemini-3.1-flash-lite"]
        for mod in modelos:
            try:
                response = self._gemini_client.models.generate_content(
                    model=mod,
                    contents=f"{prompt_sistema}\n\nDADOS DO CHAMADO:\n{dados_chamado_str}",
                    config={
                        "response_mime_type": "application/json",
                        "response_schema": ResultadoTriagemIA,
                        "temperature": 0.1,
                    },
                )
                if response and response.text:
                    return response.text
            except Exception as e:
                console.print(f"[dim yellow]Gemini ({mod}) falhou na triagem: {e}. Tentando alternativa...[/dim yellow]")

        return None

    def _executar_nvidia(
        self,
        prompt_sistema: str,
        dados_chamado_str: str,
    ) -> Optional[str]:
        """Executa fallback via NVIDIA NIM (Minimax)."""
        nvidia_key = self.settings.nvidia_api_key
        if not nvidia_key:
            return None

        url = "https://integrate.api.nvidia.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {nvidia_key}",
            "Content-Type": "application/json",
        }

        schema_json = json.dumps(ResultadoTriagemIA.model_json_schema(), ensure_ascii=False)
        sys_final = (
            f"{prompt_sistema}\n\n"
            "IMPORTANTE: Você deve responder ESTRITAMENTE um objeto JSON válido, sem texto antes ou depois, "
            f"em conformidade exata com este JSON Schema Pydantic:\n{schema_json}"
        )

        payload = {
            "model": "minimaxai/minimax-m3.1-flash",
            "messages": [
                {"role": "system", "content": sys_final},
                {"role": "user", "content": f"DADOS DO CHAMADO PARA TRIAGEM:\n{dados_chamado_str}"},
            ],
            "temperature": 0.1,
            "max_tokens": 1000,
        }

        try:
            with httpx.Client(timeout=25.0) as http_client:
                resp = http_client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
            raw_content = data["choices"][0]["message"]["content"]
            match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw_content)
            return match.group(1).strip() if match else raw_content.strip()
        except Exception as e:
            console.print(f"[dim yellow]NVIDIA NIM falhou na triagem: {e}[/dim yellow]")
            return None

    def _fallback_heuristico(
        self,
        chamado: TicketSummary,
        catalogo: dict[str, Any],
    ) -> ResultadoTriagemIA:
        """Classificador heurístico determinístico caso os provedores de IA estejam inacessíveis."""
        texto_completo = f"{chamado.assunto or ''} {chamado.descricao or ''}".lower()

        # Prioridade heurística
        if any(w in texto_completo for w in ["faturamento", "servidor fora", "parada total", "ransomware", "sem internet na empresa"]):
            prioridade = "Crítico"
            justificativa = "Palavras-chave de alto impacto operacional / parada de faturamento"
        elif any(w in texto_completo for w in ["diretoria", "todos os usuarios", "parou tudo", "urgente"]):
            prioridade = "Alto"
            justificativa = "Impacto corporativo em múltiplos colaboradores"
        elif any(w in texto_completo for w in ["instalacao", "configurar", "duvida", "impressora", "outlook"]):
            prioridade = "Baixo"
            justificativa = "Solicitação rotineira de baixa criticidade"
        else:
            prioridade = "Médio"
            justificativa = "Incidente localizado com impacto em usuário individual"

        # Tipo de Ticket
        if any(w in texto_completo for w in ["instalar", "configurar", "novo acesso", "criar email", "liberar"]):
            tipo_ticket = "Requisição"
        else:
            tipo_ticket = "Incidente"

        # Categoria Primária aproximada
        categoria_primaria = "Computadores"
        categoria_secundaria = None
        if "internet" in texto_completo or "rede" in texto_completo or "wifi" in texto_completo:
            categoria_primaria = "Rede"
            categoria_secundaria = "Falha na Internet"
        elif "email" in texto_completo or "e-mail" in texto_completo or "outlook" in texto_completo:
            categoria_primaria = "E-mail"
            categoria_secundaria = "Configuração de Cliente de E-mail"
        elif "impressora" in texto_completo:
            categoria_primaria = "Impressora"
            categoria_secundaria = "Falha/Erro"
        elif "software" in texto_completo or "sistema" in texto_completo or "erp" in texto_completo:
            categoria_primaria = "Software"
            categoria_secundaria = "Falha/não funciona"

        # Extração de telefone e e-mail via Regex
        email_match = re.search(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", chamado.descricao or "")
        email_extraido = email_match.group(0).rstrip(".") if email_match else chamado.email_contato
        tel_match = re.search(r"(?:\(?\d{2}\)?\s*)?(?:9\d{4}|\d{4})[-.\s]?\d{4}", chamado.descricao or "")

        return ResultadoTriagemIA(
            prioridade=prioridade,
            tipo_ticket=tipo_ticket,
            categoria_primaria=categoria_primaria,
            categoria_secundaria=categoria_secundaria,
            contato_nome=chamado.contato,
            contato_email=email_extraido,
            contato_telefone=tel_match.group(0) if tel_match else chamado.telefone,
            justificativa_prioridade=justificativa,
            confianca=0.7,
        )

    def triar_chamado(
        self,
        chamado: TicketSummary,
        catalogo: Optional[dict[str, Any]] = None,
    ) -> ResultadoTriagemIA:
        """Executa a triagem completa do chamado utilizando IA com fallback determinístico."""
        cat = catalogo or self.cache.obter_catalogo_categorias_e_tipos()
        texto_cat = cat.get("texto_formatado", "")

        prompt_sistema = self._obter_prompt_sistema(texto_cat)

        # Prepara bloco de dados do chamado
        dados_dict = {
            "codigo": chamado.codigo or chamado.id,
            "cliente": chamado.cliente,
            "assunto": chamado.assunto,
            "descricao": chamado.descricao_limpa or chamado.descricao,
            "contato_informado": chamado.contato,
            "dispositivo": chamado.dispositivo,
            "prioridade_atual": chamado.prioridade,
            "tipo_ticket_atual": chamado.tipo_ticket,
            "categoria_primaria_atual": chamado.categoria_primaria,
        }
        dados_str = json.dumps(dados_dict, ensure_ascii=False, indent=2)

        # 1. Tentativa via Gemini
        raw_json = self._executar_gemini(prompt_sistema, dados_str)

        # 2. Tentativa via NVIDIA NIM se Gemini falhar
        if not raw_json:
            raw_json = self._executar_nvidia(prompt_sistema, dados_str)

        # 3. Processamento do JSON retornado pela IA
        if raw_json:
            try:
                res = ResultadoTriagemIA.model_validate_json(raw_json)
                # Garante normalização de valores chave
                res.prioridade = normalizar_prioridade(res.prioridade)
                res.tipo_ticket = normalizar_tipo_ticket(res.tipo_ticket)
                return res
            except Exception as e:
                console.print(f"[dim yellow]Falha ao parsear retorno JSON da IA ({e}). Ativando fallback...[/dim yellow]")

        # 4. Fallback Heurístico
        res_heur = self._fallback_heuristico(chamado, cat)
        res_heur.prioridade = normalizar_prioridade(res_heur.prioridade)
        res_heur.tipo_ticket = normalizar_tipo_ticket(res_heur.tipo_ticket)
        return res_heur
