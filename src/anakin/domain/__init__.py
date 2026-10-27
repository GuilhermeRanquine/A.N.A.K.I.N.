"""Módulo de domínio e contratos Pydantic (chamados, clientes, SLAs e criação)."""

from anakin.domain.ticket_models import TicketSummary, TicketFollowUp
from anakin.domain.ticket_creation_models import DraftTicketExtraction, MilvusCreateTicketPayload

__all__ = [
    "TicketSummary",
    "TicketFollowUp",
    "DraftTicketExtraction",
    "MilvusCreateTicketPayload",
]
