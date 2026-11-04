using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using Avalonia.Threading;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.Extensions.Logging.Abstractions;
using Anakin.Core.Domain;
using Anakin.Engine.Dispatch.Algorithms;
using Anakin.Integrations.Audio;
using Anakin.Storage.DuckDb;

namespace Anakin.UI.CyberHUD.ViewModels;

public partial class MainWindowViewModel : ObservableObject
{
    private readonly DuckDbAnalyticsRepository _duckDbRepo;
    private readonly PonderedDispatchEngine _dispatchEngine = new();
    private readonly TacticalSoundSynthesizer _soundSynthesizer = new(NullLogger<TacticalSoundSynthesizer>.Instance);

    private readonly DispatcherTimer _clockTimer;
    private readonly DispatcherTimer _tickerTimer;
    private int _countdownSegundos = 62;
    private List<ChamadoModel> _todosChamadosCache = new();

    [ObservableProperty]
    private string _tituloHud = "A.N.A.K.I.N // OPERATIONAL CYBERHUD CONSOLE";

    [ObservableProperty]
    private string _statusMotor = "ONLINE // DISPATCH ENGINE v2.0";

    [ObservableProperty]
    private string _statusGeminiLive = "STANDBY // WSS DUPLEX STREAM ACTIVE";

    [ObservableProperty]
    private string _relogioTatico = "--:--:--";

    [ObservableProperty]
    private string _dataTatica = "--/--/----";

    [ObservableProperty]
    private string _countdownMilvus = "MILVUS: 62s";

    [ObservableProperty]
    private int _kpiTotalAtivos;

    [ObservableProperty]
    private int _kpiAFazer;

    [ObservableProperty]
    private int _kpiAtendendo;

    [ObservableProperty]
    private int _kpiPausados;

    [ObservableProperty]
    private double _kpiScoreMedio;

    [ObservableProperty]
    private string _proximoRecomendado = "Carregando...";

    [ObservableProperty]
    private string _filtroAtivo = "TODOS";

    public ObservableCollection<OperadorModel> Operadores { get; } = new();
    public ObservableCollection<ChamadoModel> FilaChamados { get; } = new();
    public ObservableCollection<string> LogsOperacionais { get; } = new();

    public MainWindowViewModel()
    {
        var duckDbPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "data", "anakin_readonly.duckdb");
        if (!File.Exists(duckDbPath))
        {
            duckDbPath = Path.GetFullPath("data/anakin_readonly.duckdb");
        }

        _duckDbRepo = new DuckDbAnalyticsRepository(duckDbPath, NullLogger<DuckDbAnalyticsRepository>.Instance);

        // Timer do Relógio Tático (100ms)
        _clockTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(100) };
        _clockTimer.Tick += (_, _) =>
        {
            var agora = DateTime.Now;
            RelogioTatico = agora.ToString("HH:mm:ss.ff", CultureInfo.InvariantCulture);
            DataTatica = agora.ToString("dd/MM/yyyy // 'BRT-03:00'", CultureInfo.InvariantCulture);
        };
        _clockTimer.Start();

        // Timer do Ticker Countdown Milvus (1s)
        _tickerTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(1) };
        _tickerTimer.Tick += async (_, _) =>
        {
            _countdownSegundos--;
            if (_countdownSegundos <= 0)
            {
                _countdownSegundos = 62;
                CountdownMilvus = "MILVUS: SINCRONIZANDO...";
                await RecarregarDadosDuckDbAsync().ConfigureAwait(false);
            }
            else
            {
                CountdownMilvus = $"MILVUS: {_countdownSegundos}s";
            }
        };
        _tickerTimer.Start();

        AdicionarLog("Cockpit Tático A.N.A.K.I.N inicializado.");
        AdicionarLog("Motor matemático de carga ponderada carregado.");
        AdicionarLog("Acessando banco colunar DuckDB (dados reais Milvus)...");

        _ = InicializarDadosAsync();
    }

    private async Task InicializarDadosAsync()
    {
        await RecarregarDadosDuckDbAsync().ConfigureAwait(false);
    }

    [RelayCommand]
    private async Task RecarregarDadosDuckDbAsync()
    {
        try
        {
            var sw = System.Diagnostics.Stopwatch.StartNew();

            var operadoresReais = await _duckDbRepo.ObterOperadoresReaisAsync().ConfigureAwait(true);
            var chamadosReais = await _duckDbRepo.ObterChamadosAtivosReaisAsync().ConfigureAwait(true);
            var metricas = await _duckDbRepo.ObterMetricasGeraisAsync().ConfigureAwait(true);

            sw.Stop();

            _todosChamadosCache = chamadosReais.ToList();

            // Atualiza operadores
            Operadores.Clear();
            foreach (var op in operadoresReais)
            {
                Operadores.Add(op);
            }

            // Atualiza chamados com base no filtro atual
            AplicarFiltroChamados();

            // Atualiza KPIs
            KpiTotalAtivos = chamadosReais.Count;
            KpiAFazer = metricas.TotalAFazer;
            KpiAtendendo = metricas.TotalAtendendo;
            KpiPausados = metricas.TotalPausados;

            if (Operadores.Count > 0)
            {
                KpiScoreMedio = Math.Round(Operadores.Average(o => o.ScoreCargaAtual), 1);
                var proximo = Operadores.OrderBy(o => o.ScoreCargaAtual).First();
                ProximoRecomendado = $"{proximo.Nome} (Score: {proximo.ScoreCargaAtual})";
            }

            AdicionarLog($"[DUCKDB] {chamadosReais.Count} chamados reais e {operadoresReais.Count} operadores sincronizados em {sw.ElapsedMilliseconds}ms.");
        }
        catch (Exception ex)
        {
            AdicionarLog($"[ERRO DUCKDB] Falha ao ler banco: {ex.Message}");
        }
    }

    [RelayCommand]
    private void DespacharProximoChamado()
    {
        if (FilaChamados.Count == 0)
        {
            AdicionarLog("[ALERTA] Nenhum chamado elegível na fila para despacho imediato.");
            _soundSynthesizer.PlayWarningAlert();
            return;
        }

        var chamado = FilaChamados[0];
        var listaOps = Operadores.ToList();

        var melhorOperador = _dispatchEngine.SelecionarMelhorOperador(listaOps, chamado);
        if (melhorOperador == null)
        {
            AdicionarLog($"[BLOQUEIO] Nenhum operador disponível para o Chamado {chamado.CodigoFormatado}.");
            _soundSynthesizer.PlayCriticalAlert();
            return;
        }

        // Remove da fila pendente
        FilaChamados.RemoveAt(0);
        _todosChamadosCache.RemoveAll(c => c.Id == chamado.Id);

        // Atualiza operador com novo score e atribuição
        for (int i = 0; i < Operadores.Count; i++)
        {
            if (Operadores[i].Email.Equals(melhorOperador.Value.Email, StringComparison.OrdinalIgnoreCase))
            {
                var opAtual = Operadores[i];
                var peso = chamado.Status.Equals("Atendendo", StringComparison.OrdinalIgnoreCase) ? 3 : 2;
                Operadores[i] = new OperadorModel(
                    opAtual.Email,
                    opAtual.Nome,
                    opAtual.Tier,
                    opAtual.Ativo,
                    opAtual.ScoreCargaAtual + peso,
                    opAtual.ChamadosAtendendo + 1,
                    opAtual.ChamadosAFazer,
                    opAtual.ChamadosPausados,
                    DateTimeOffset.UtcNow);
                break;
            }
        }

        // Recalcula próximo recomendado
        var novoProximo = Operadores.OrderBy(o => o.ScoreCargaAtual).First();
        ProximoRecomendado = $"{novoProximo.Nome} (Score: {novoProximo.ScoreCargaAtual})";
        KpiTotalAtivos = FilaChamados.Count;

        AdicionarLog($"[DESPACHO] Chamado {chamado.CodigoFormatado} atribuído para {melhorOperador.Value.Nome} (Novo Score: {melhorOperador.Value.ScoreCargaAtual + 2})");
        _soundSynthesizer.PlaySuccessAlert();
    }

    [RelayCommand]
    private void DefinirFiltro(string filtro)
    {
        FiltroAtivo = filtro;
        AplicarFiltroChamados();
        _soundSynthesizer.PlayTacticalChime(950.0, 60);
    }

    private void AplicarFiltroChamados()
    {
        FilaChamados.Clear();
        var filtrados = FiltroAtivo switch
        {
            "A_FAZER" => _todosChamadosCache.Where(c => c.Status.Equals("A fazer", StringComparison.OrdinalIgnoreCase)),
            "ATENDENDO" => _todosChamadosCache.Where(c => c.Status.Equals("Atendendo", StringComparison.OrdinalIgnoreCase)),
            "PAUSADOS" => _todosChamadosCache.Where(c => c.Status.Equals("Pausado", StringComparison.OrdinalIgnoreCase)),
            _ => _todosChamadosCache
        };

        foreach (var c in filtrados.Take(50))
        {
            FilaChamados.Add(c);
        }
    }

    [RelayCommand]
    private void LimparLogs()
    {
        LogsOperacionais.Clear();
        AdicionarLog("[SISTEMA] Console de telemetria limpo.");
    }

    private void AdicionarLog(string mensagem)
    {
        var timestamp = DateTime.Now.ToString("HH:mm:ss.ff", CultureInfo.InvariantCulture);
        LogsOperacionais.Insert(0, $"[{timestamp}] {mensagem}");
        if (LogsOperacionais.Count > 120)
        {
            LogsOperacionais.RemoveAt(LogsOperacionais.Count - 1);
        }
    }
}
