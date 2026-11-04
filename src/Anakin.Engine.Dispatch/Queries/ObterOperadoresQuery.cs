using System.Collections.Generic;
using MediatR;
using Anakin.Core.Domain;

namespace Anakin.Engine.Dispatch.Queries;

/// <summary>
/// Consulta CQRS para listagem de operadores com métricas e filtros por Tier.
/// </summary>
public sealed record ObterOperadoresQuery(OperadorTier? Tier = null) : IRequest<IReadOnlyList<OperadorModel>>;
