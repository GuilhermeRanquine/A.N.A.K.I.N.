using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using Anakin.Core.Domain;

namespace Anakin.Core.Interfaces;

/// <summary>
/// Motor algorítmico matemático de balanceamento ponderado de chamados.
/// </summary>
public interface IDispatchEngine
{
    OperadorModel? SelecionarMelhorOperador(
        IReadOnlyList<OperadorModel> operadores,
        ChamadoModel chamado,
        IReadOnlySet<string>? emailsBloqueados = null);
}

/// <summary>
/// Contrato do repositório analítico local ultrarrápido (DuckDB).
/// </summary>
public interface IAnalyticsRepository
{
    Task InicializarBancoAsync(CancellationToken ct = default);
    Task InserirOuAtualizarChamadoAsync(ChamadoModel chamado, CancellationToken ct = default);
    Task InserirOuAtualizarOperadorAsync(OperadorModel operador, CancellationToken ct = default);
    Task<int> ObterScoreOperadorAsync(string operadorEmail, CancellationToken ct = default);
    Task<IReadOnlyList<OperadorModel>> ObterOperadoresAsync(OperadorTier? tier = null, CancellationToken ct = default);
    Task<IReadOnlyList<ChamadoModel>> ObterChamadosNaoAtribuidosAsync(CancellationToken ct = default);
    Task<IReadOnlyList<OperadorModel>> ObterOperadoresReaisAsync(CancellationToken ct = default);
    Task<IReadOnlyList<ChamadoModel>> ObterChamadosAtivosReaisAsync(CancellationToken ct = default);
    Task<DashboardKpiModel> ObterMetricasGeraisAsync(CancellationToken ct = default);
}

/// <summary>
/// Gerador sonoro tático para feedback em tempo real.
/// </summary>
public interface ISoundSynthesizer
{
    void PlayTacticalChime(double frequency = 880.0, int durationMs = 120);
    void PlaySuccessAlert();
    void PlayWarningAlert();
    void PlayCriticalAlert();
}
