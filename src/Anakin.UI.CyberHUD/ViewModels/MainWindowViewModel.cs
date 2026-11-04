using System;
using System.Collections.ObjectModel;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.Extensions.Logging.Abstractions;
using Anakin.Core.Domain;
using Anakin.Engine.Dispatch.Algorithms;
using Anakin.Integrations.Audio;

namespace Anakin.UI.CyberHUD.ViewModels;

public partial class MainWindowViewModel : ObservableObject
{
    private readonly PonderedDispatchEngine _dispatchEngine = new();
    private readonly TacticalSoundSynthesizer _soundSynthesizer = new(NullLogger<TacticalSoundSynthesizer>.Instance);

    [ObservableProperty]
    private string _tituloHud = "A.N.A.K.I.N — TACTICAL CYBERHUD v2.0";

    [ObservableProperty]
    private string _statusMotor = "ONLINE // OPERACIONAL";

    [ObservableProperty]
    private string _statusGeminiLive = "STANDBY // WEBSOCKET WSS READY";

    [ObservableProperty]
    private int _totalChamadosPendentes;

    [ObservableProperty]
    private double _scoreCargaMedia;

    public ObservableCollection<OperadorModel> Operadores { get; } = new();
    public ObservableCollection<ChamadoModel> FilaChamados { get; } = new();
    public ObservableCollection<string> LogsOperacionais { get; } = new();

    public MainWindowViewModel()
    {
        CarregarOperadoresIniciais();
        CarregarChamadosIniciais();
        AdicionarLog("Sistema tático A.N.A.K.I.N inicializado.");
        AdicionarLog("Motor de balanceamento ponderado de carga carregado.");
        AdicionarLog("Banco analítico colunar DuckDB em modo de escuta.");
    }

    private void CarregarOperadoresIniciais()
    {
        Operadores.Add(new OperadorModel("carlos.n1@empresa.com.br", "Carlos Silva", OperadorTier.N1, true, 4, DateTimeOffset.UtcNow.AddMinutes(-35)));
        Operadores.Add(new OperadorModel("mariana.n1@empresa.com.br", "Mariana Costa", OperadorTier.N1, true, 2, DateTimeOffset.UtcNow.AddMinutes(-50)));
        Operadores.Add(new OperadorModel("bruno.n1@empresa.com.br", "Bruno Rocha", OperadorTier.N1, true, 6, DateTimeOffset.UtcNow.AddMinutes(-15)));
        Operadores.Add(new OperadorModel("rodrigo.n2@empresa.com.br", "Rodrigo Especialista", OperadorTier.N2, true, 3, DateTimeOffset.UtcNow.AddMinutes(-40)));
        Operadores.Add(new OperadorModel("aline.n2@empresa.com.br", "Aline Especialista", OperadorTier.N2, true, 1, DateTimeOffset.UtcNow.AddMinutes(-80)));
    }

    private void CarregarChamadosIniciais()
    {
        FilaChamados.Add(new ChamadoModel(1041, "Falha de Roteador de Borda", "Hospital São Lucas", ChamadoStatus.AFazer, ChamadoPrioridade.Critica, "Infraestrutura", null, DateTimeOffset.UtcNow.AddMinutes(-10), DateTimeOffset.UtcNow, null));
        FilaChamados.Add(new ChamadoModel(1042, "Lentidão no ERP Financeiro", "Distribuidora Nacional", ChamadoStatus.AFazer, ChamadoPrioridade.Alta, "Sistemas", null, DateTimeOffset.UtcNow.AddMinutes(-8), DateTimeOffset.UtcNow, null));
        FilaChamados.Add(new ChamadoModel(1043, "Reset de Senha de Domínio", "Advocacia Central", ChamadoStatus.AFazer, ChamadoPrioridade.Baixa, "Suporte Geral", null, DateTimeOffset.UtcNow.AddMinutes(-5), DateTimeOffset.UtcNow, null));

        AtualizarMetricas();
    }

    [RelayCommand]
    private void DespacharProximoChamado()
    {
        if (FilaChamados.Count == 0)
        {
            AdicionarLog("[INFO] Nenhum chamado na fila para despacho.");
            _soundSynthesizer.PlayWarningAlert();
            return;
        }

        var chamado = FilaChamados[0];
        var operadoresLista = new System.Collections.Generic.List<OperadorModel>(Operadores);

        var melhorOperador = _dispatchEngine.SelecionarMelhorOperador(operadoresLista, chamado);
        if (melhorOperador == null)
        {
            AdicionarLog($"[ALERTA] Nenhum operador disponível para o Chamado #{chamado.Id}.");
            _soundSynthesizer.PlayCriticalAlert();
            return;
        }

        // Remove da fila pendente
        FilaChamados.RemoveAt(0);

        // Atualiza operador com novo score e timestamp
        for (int i = 0; i < Operadores.Count; i++)
        {
            if (Operadores[i].Email.Equals(melhorOperador.Value.Email, StringComparison.OrdinalIgnoreCase))
            {
                var opAtual = Operadores[i];
                Operadores[i] = new OperadorModel(
                    opAtual.Email,
                    opAtual.Nome,
                    opAtual.Tier,
                    opAtual.Ativo,
                    opAtual.ScoreCargaAtual + (chamado.Status == ChamadoStatus.Atendendo ? 3 : 2),
                    DateTimeOffset.UtcNow);
                break;
            }
        }

        AdicionarLog($"[DESPACHO] Chamado #{chamado.Id} ({chamado.Prioridade}) atribuído a {melhorOperador.Value.Nome} ({melhorOperador.Value.Tier})");
        _soundSynthesizer.PlaySuccessAlert();

        AtualizarMetricas();
    }

    [RelayCommand]
    private void SimularNovoChamado()
    {
        var random = new Random();
        var id = 1050 + FilaChamados.Count + random.Next(1, 99);
        var prioridades = new[] { ChamadoPrioridade.Baixa, ChamadoPrioridade.Media, ChamadoPrioridade.Alta, ChamadoPrioridade.Critica };
        var prioridade = prioridades[random.Next(prioridades.Length)];

        var novoChamado = new ChamadoModel(
            id,
            $"Incidente Telemetria #{id}",
            "Cliente Corporativo",
            ChamadoStatus.AFazer,
            prioridade,
            "Suporte Técnico",
            null,
            DateTimeOffset.UtcNow,
            DateTimeOffset.UtcNow,
            null);

        FilaChamados.Add(novoChamado);
        AdicionarLog($"[ENTRADA] Novo chamado #{id} ({prioridade}) recebido no radar.");
        _soundSynthesizer.PlayTacticalChime(920.0, 80);

        AtualizarMetricas();
    }

    [RelayCommand]
    private void LimparLogs()
    {
        LogsOperacionais.Clear();
        AdicionarLog("[SISTEMA] Console de auditoria limpo.");
    }

    private void AdicionarLog(string mensagem)
    {
        var timestamp = DateTime.Now.ToString("HH:mm:ss", System.Globalization.CultureInfo.InvariantCulture);
        LogsOperacionais.Insert(0, $"[{timestamp}] {mensagem}");
        if (LogsOperacionais.Count > 100)
        {
            LogsOperacionais.RemoveAt(LogsOperacionais.Count - 1);
        }
    }

    private void AtualizarMetricas()
    {
        TotalChamadosPendentes = FilaChamados.Count;
        if (Operadores.Count > 0)
        {
            double soma = 0;
            foreach (var op in Operadores)
            {
                soma += op.ScoreCargaAtual;
            }
            ScoreCargaMedia = Math.Round(soma / Operadores.Count, 1);
        }
    }
}
