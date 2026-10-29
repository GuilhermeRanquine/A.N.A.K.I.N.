"""Módulo de monitoramento e distribuição automática de chamados do A.N.A.K.I.N."""

from anakin.dispatch.auto_dispatcher import (
    AutoDispatcher,
    OperadorElegivel,
    ScoreOperador,
    OPERADORES_ELEGIVEIS,
    OPERADORES_BLOQUEADOS,
    AtribuicaoRegistro,
    verificar_seguranca_operador,
    eh_chamado_sem_tecnico,
    SecurityDispatchError,
)

__all__ = [
    "AutoDispatcher",
    "OperadorElegivel",
    "ScoreOperador",
    "OPERADORES_ELEGIVEIS",
    "OPERADORES_BLOQUEADOS",
    "AtribuicaoRegistro",
    "verificar_seguranca_operador",
    "eh_chamado_sem_tecnico",
    "SecurityDispatchError",
]
