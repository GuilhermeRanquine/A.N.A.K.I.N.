namespace Anakin.Core.Domain;

/// <summary>
/// Nível operacional (Tier) do operador no ecossistema de atendimento.
/// </summary>
public enum OperadorTier
{
    N1 = 1,
    N2 = 2,
    Gestao = 99
}

/// <summary>
/// Prioridade de negócio do chamado.
/// </summary>
public enum ChamadoPrioridade
{
    Baixa = 1,
    Media = 2,
    Alta = 3,
    Critica = 4
}

/// <summary>
/// Estado atual do ciclo de vida do chamado.
/// </summary>
public enum ChamadoStatus
{
    AFazer = 1,
    Atendendo = 2,
    Pausado = 3,
    Finalizado = 4,
    Cancelado = 5
}
