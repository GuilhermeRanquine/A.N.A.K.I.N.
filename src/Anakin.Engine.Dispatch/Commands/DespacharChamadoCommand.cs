using MediatR;
using Anakin.Core.Domain;

namespace Anakin.Engine.Dispatch.Commands;

/// <summary>
/// Comando de mutação para despachar um chamado para um operador específico.
/// </summary>
public sealed record DespacharChamadoCommand(
    int ChamadoId,
    string OperadorEmail,
    ChamadoPrioridade Prioridade = ChamadoPrioridade.Media) : IRequest<bool>;
