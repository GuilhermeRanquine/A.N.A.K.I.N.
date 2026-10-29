"""Modelos de dados (Pydantic) de chamados e acompanhamentos do Milvus.

Projetados com alta tolerância a tipos dinâmicos e campos nulos vindos da API.
"""

from typing import Any, Optional
import re
from pydantic import BaseModel, ConfigDict, model_validator


def _clean_text(text: str | None) -> str:
    """Remove tags HTML e quebras excessivas de linha de descrições."""
    if not text:
        return ""
    # Remove tags HTML simples caso a API retorne rich text
    clean = re.sub(r"<[^>]+>", " ", text)
    # Substitui múltiplos espaços por um único
    clean = re.sub(r"[ \t]+", " ", clean)
    # Substitui mais de 2 quebras de linha por duas
    clean = re.sub(r"\n\s*\n\s*\n+", "\n\n", clean)
    return clean.strip()


class TicketSummary(BaseModel):
    """Resumo de chamado retornado na listagem do Milvus."""

    model_config = ConfigDict(extra="ignore")

    id: Optional[str | int] = None
    codigo: Optional[str | int] = None
    cliente: Optional[str] = None
    cliente_token: Optional[str] = None
    contato: Optional[str] = None
    assunto: Optional[str] = None
    descricao: Optional[str] = None
    status: Optional[str | int] = None
    prioridade: Optional[str | int] = None
    tecnico: Optional[str] = None
    email_tecnico: Optional[str] = None
    mesa_trabalho: Optional[str] = None
    categoria_primaria: Optional[str] = None
    categoria_secundaria: Optional[str] = None
    tipo_ticket: Optional[str] = None
    dispositivo: Optional[str] = None
    telefone: Optional[str] = None
    email_contato: Optional[str] = None
    data_criacao: Optional[str] = None
    data_modificacao: Optional[str] = None
    data_solucao: Optional[str] = None
    status_sla_resposta: Optional[str | float | int | dict[str, Any]] = None
    status_sla_solucao: Optional[str | float | int | dict[str, Any]] = None
    raw_json: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def normalizar_campos(cls, data: Any) -> Any:
        """Mapeia variações de nomenclatura da API do Milvus para os atributos do modelo."""
        if not isinstance(data, dict):
            return data

        # Mapeamento de possíveis aliases da API Milvus
        def get_first(*keys: str) -> Any:
            for k in keys:
                if k in data and data[k] is not None:
                    return data[k]
            return None

        normalized = dict(data)
        normalized["id"] = get_first("id", "chamado_id", "_id", "codigo")
        normalized["codigo"] = get_first("codigo", "chamado_codigo", "numero", "id")
        normalized["cliente"] = get_first("cliente", "cliente_nome", "razao_social", "empresa", "fantasia")
        normalized["cliente_token"] = get_first("cliente_token", "token_cliente")
        normalized["contato"] = get_first("contato", "contato_nome", "nome_contato")
        normalized["assunto"] = get_first("assunto", "chamado_assunto", "titulo")
        normalized["descricao"] = get_first("descricao", "chamado_descricao", "texto")
        normalized["status"] = get_first("status", "status_nome", "chamado_status")
        normalized["prioridade"] = get_first("prioridade", "prioridade_nome")
        normalized["tecnico"] = get_first("tecnico", "tecnico_nome", "analista", "usuario_nome", "responsavel")
        normalized["email_tecnico"] = get_first("email_tecnico", "tecnico_email", "email_analista", "usuario_email", "email_responsavel")
        normalized["mesa_trabalho"] = get_first("mesa_trabalho", "mesa_trabalho_nome", "mesa")
        normalized["categoria_primaria"] = get_first("categoria_primaria", "categoria", "categoria_nome")
        normalized["categoria_secundaria"] = get_first("categoria_secundaria", "subcategoria", "subcategoria_nome")
        normalized["tipo_ticket"] = get_first("tipo_ticket", "tipo", "tipo_chamado", "origem", "tipo_nome")
        disp_val = get_first("dispositivo", "dispositivo_vinculado", "patrimonio", "hostname")
        if isinstance(disp_val, dict):
            normalized["dispositivo"] = disp_val.get("nome") or disp_val.get("hostname") or disp_val.get("patrimonio") or str(disp_val)
        elif disp_val is not None:
            normalized["dispositivo"] = str(disp_val)
        else:
            normalized["dispositivo"] = None

        tel_val = get_first("telefone", "contato_telefone", "celular")
        normalized["telefone"] = str(tel_val) if tel_val is not None else None

        email_c = get_first("email_contato", "contato_email", "email_conferencia", "email")
        normalized["email_contato"] = str(email_c) if email_c is not None else None
        normalized["data_criacao"] = get_first("data_criacao", "criado_em", "data_hora_criacao", "data_abertura")
        normalized["data_modificacao"] = get_first("data_modificacao", "modificado_em", "data_hora_alteracao")
        normalized["data_solucao"] = get_first("data_solucao", "solucionado_em", "data_hora_solucao")
        normalized["status_sla_resposta"] = get_first("status_sla_resposta", "sla_resposta", "sla_primeira_resposta")
        normalized["status_sla_solucao"] = get_first("status_sla_solucao", "sla_solucao", "sla_fechamento")
        if "raw_json" not in normalized or not normalized.get("raw_json"):
            import json as _json
            try:
                normalized["raw_json"] = _json.dumps(data, ensure_ascii=False)
            except Exception:
                normalized["raw_json"] = None

        return normalized

    @property
    def descricao_limpa(self) -> str:
        """Retorna a descrição sem tags HTML e com quebras normalizadas."""
        return _clean_text(self.descricao)

    @property
    def sla_solucao_formatado(self) -> str:
        """Formata o status de SLA para exibição amigável."""
        if self.status_sla_solucao is None:
            return "N/D"
        if isinstance(self.status_sla_solucao, dict):
            status = self.status_sla_solucao.get("status") or self.status_sla_solucao.get("descricao")
            pct = self.status_sla_solucao.get("porcentagem") or self.status_sla_solucao.get("percentual")
            if status and pct is not None:
                return f"{status} ({pct}%)"
            if status:
                return str(status)
            if pct is not None:
                return f"{pct}%"
            return str(self.status_sla_solucao)
        return str(self.status_sla_solucao)


class TicketFollowUp(BaseModel):
    """Acompanhamento ou comentário de um chamado no Milvus."""

    model_config = ConfigDict(extra="ignore")

    pessoa: Optional[str] = None
    perfil: Optional[str] = None
    texto: Optional[str] = None
    texto_html: Optional[str] = None
    log_tipo_text: Optional[str] = None
    total_horas: Optional[str] = None
    data: Optional[str] = None
    privado: Optional[bool | int | str] = None
    tipo: Optional[str | int] = None
    is_excluido: Optional[bool | int | str] = None

    @model_validator(mode="before")
    @classmethod
    def normalizar_campos(cls, data: Any) -> Any:
        """Mapeia variações de nomenclatura nos acompanhamentos da API."""
        if not isinstance(data, dict):
            return data

        def get_first(*keys: str) -> Any:
            for k in keys:
                if k in data and data[k] is not None:
                    return data[k]
            return None

        normalized = dict(data)
        normalized["pessoa"] = get_first("pessoa", "tecnico", "usuario", "usuario_nome", "nome_usuario", "autor")
        normalized["perfil"] = get_first("perfil", "tipo_usuario", "cargo")
        normalized["texto"] = get_first("texto", "descricao_formatada", "descricao", "mensagem", "comentario", "conteudo")
        normalized["texto_html"] = get_first("texto_html", "html", "conteudo_html")
        normalized["log_tipo_text"] = get_first("log_tipo_text", "tipo_texto", "log_tipo")
        normalized["total_horas"] = get_first("total_horas", "horas")
        normalized["data"] = get_first("data", "data_criacao", "data_hora", "criado_em")
        normalized["privado"] = get_first("privado", "is_privado", "interno", "visibilidade")
        normalized["tipo"] = get_first("tipo", "tipo_acompanhamento")
        normalized["is_excluido"] = get_first("is_excluido", "excluido", "deleted")

        return normalized

    @property
    def texto_limpo(self) -> str:
        """Retorna o texto do comentário com formatação limpa."""
        return _clean_text(self.texto)
