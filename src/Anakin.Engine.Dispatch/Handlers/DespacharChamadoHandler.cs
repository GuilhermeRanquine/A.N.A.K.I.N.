using System.Threading;
using System.Threading.Tasks;
using FluentValidation;
using MediatR;
using Microsoft.Extensions.Logging;
using Anakin.Core.Domain;
using Anakin.Core.Interfaces;
using Anakin.Engine.Dispatch.Commands;

namespace Anakin.Engine.Dispatch.Handlers;

/// <summary>
/// Manipulador de comando para efetivar o despacho de chamado com logging de alta performance gerado em tempo de compilação.
/// </summary>
public sealed partial class DespacharChamadoHandler : IRequestHandler<DespacharChamadoCommand, bool>
{
    private readonly IValidator<DespacharChamadoCommand> _validator;
    private readonly IAnalyticsRepository _analyticsRepository;
    private readonly ISoundSynthesizer _soundSynthesizer;
    private readonly ILogger<DespacharChamadoHandler> _logger;

    public DespacharChamadoHandler(
        IValidator<DespacharChamadoCommand> validator,
        IAnalyticsRepository analyticsRepository,
        ISoundSynthesizer soundSynthesizer,
        ILogger<DespacharChamadoHandler> logger)
    {
        _validator = validator;
        _analyticsRepository = analyticsRepository;
        _soundSynthesizer = soundSynthesizer;
        _logger = logger;
    }

    public async Task<bool> Handle(DespacharChamadoCommand request, CancellationToken cancellationToken)
    {
        var validationResult = await _validator.ValidateAsync(request, cancellationToken);
        if (!validationResult.IsValid)
        {
            LogValidacaoFalhou(_logger, request.ChamadoId, string.Join("; ", validationResult.Errors));
            _soundSynthesizer.PlayWarningAlert();
            return false;
        }

        LogDespachandoChamado(_logger, request.ChamadoId, request.OperadorEmail, request.Prioridade);

        // Dispara feedback sonoro tático de sucesso
        _soundSynthesizer.PlaySuccessAlert();

        return true;
    }

    [LoggerMessage(EventId = 1001, Level = LogLevel.Warning, Message = "Falha na validação do comando de despacho do chamado {ChamadoId}: {Errors}")]
    private static partial void LogValidacaoFalhou(ILogger logger, int chamadoId, string errors);

    [LoggerMessage(EventId = 1002, Level = LogLevel.Information, Message = "Despachando Chamado #{ChamadoId} para o Operador {OperadorEmail} (Prioridade: {Prioridade})")]
    private static partial void LogDespachandoChamado(ILogger logger, int chamadoId, string operadorEmail, ChamadoPrioridade prioridade);
}
