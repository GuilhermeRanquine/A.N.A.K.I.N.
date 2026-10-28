"""Modelos de dados para extração estruturada (Gemini) e abertura segura de chamados no Milvus."""

import html
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, model_validator


class DraftTicketExtraction(BaseModel):
    """Estrutura extraída pelo Gemini a partir de pedidos em linguagem natural e/ou prints."""

    model_config = ConfigDict(extra="ignore")

    empresa_alvo: str | None = Field(default=None, description="Nome da empresa/cliente para abrir o chamado")
    chamado_assunto: str | None = Field(default=None, description="Título objetivo e claro do chamado")
    chamado_descricao: str | None = Field(default=None, description="Descrição detalhada do problema ou solicitação")
    chamado_contato: str | None = Field(default=None, description="Nome da pessoa solicitante, se mencionado")
    chamado_telefone: str | None = Field(default=None, description="Telefone do solicitante apenas para o chamado, se mencionado")
    chamado_email: str | None = Field(default=None, description="E-mail do solicitante para o chamado, se mencionado")
    chamado_tecnico: str | None = Field(default=None, description="Nome ou e-mail do Operador (técnico de suporte) a atribuir no Milvus, se mencionado")
    chamado_mesa: str | None = Field(default=None, description="Nome da Mesa de Trabalho solicitada no Milvus, se mencionada")
    dados_suficientes: bool = Field(description="True somente se houver empresa_alvo, chamado_assunto e chamado_descricao claros")
    mensagem_para_operador: str = Field(description="Resumo curto ou pergunta caso falte empresa, assunto ou descrição")


class MilvusCreateTicketPayload(BaseModel):
    """Payload oficial para envio ao endpoint POST /chamado/criar no Milvus."""

    model_config = ConfigDict(extra="ignore")

    cliente_id: str = Field(description="Token de identificação do cliente no Milvus (ex: HZKRE9)")
    chamado_assunto: str = Field(description="Título ou assunto do chamado")
    chamado_descricao: str = Field(description="Texto plano da descrição do chamado")
    chamado_descricao_html: str = Field(default="", description="Descrição formatada em HTML (<p>...<br>...</p>)")
    is_b2c: bool = Field(default=False, description="Flag indicando se é cliente final B2C")
    chamado_contato: str | None = Field(default=None, description="Nome do contato para o chamado")
    chamado_telefone: str | None = Field(default=None, description="Telefone do contato")
    chamado_email: str | None = Field(default=None, description="E-mail do contato")
    chamado_tecnico: str | None = Field(default=None, description="Username ou e-mail do Operador responsável no Milvus")
    chamado_mesa: str | None = Field(default=None, description="Mesa de Trabalho no Milvus")

    @model_validator(mode="after")
    def gerar_html_descricao(self) -> "MilvusCreateTicketPayload":
        """Gera automaticamente a versão HTML da descrição caso não tenha sido preenchida."""
        if not self.chamado_descricao_html and self.chamado_descricao:
            escaped = html.escape(self.chamado_descricao).replace("\n", "<br>")
            self.chamado_descricao_html = f"<p>{escaped}</p>"
        return self

    def to_clean_payload(self) -> dict[str, Any]:
        """Gera dicionário limpo removendo chaves com valores nulos ou vazios.

        Evita enviar parâmetros desnecessários ou fictícios para a API de produção do Milvus.
        """
        raw_dict = self.model_dump()
        return {
            k: v for k, v in raw_dict.items()
            if v is not None and v != ""
        }
