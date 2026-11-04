using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using MediatR;
using Anakin.Core.Domain;
using Anakin.Core.Interfaces;
using Anakin.Engine.Dispatch.Queries;

namespace Anakin.Engine.Dispatch.Handlers;

/// <summary>
/// Manipulador da consulta CQRS para leitura otimizada de operadores.
/// </summary>
public sealed class ObterOperadoresHandler : IRequestHandler<ObterOperadoresQuery, IReadOnlyList<OperadorModel>>
{
    private readonly IAnalyticsRepository _analyticsRepository;

    public ObterOperadoresHandler(IAnalyticsRepository analyticsRepository)
    {
        _analyticsRepository = analyticsRepository;
    }

    public Task<IReadOnlyList<OperadorModel>> Handle(ObterOperadoresQuery request, CancellationToken cancellationToken)
    {
        return _analyticsRepository.ObterOperadoresAsync(request.Tier, cancellationToken);
    }
}
