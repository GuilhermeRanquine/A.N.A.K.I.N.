using System;

namespace Anakin.Core.Domain;

/// <summary>
/// Representação de um Chamado com dados táticos e metadados visuais de renderização.
/// </summary>
public readonly record struct ChamadoModel(
    string Id,
    string Codigo,
    string Titulo,
    string Cliente,
    string Status,
    string Prioridade,
    string Categoria,
    string? OperadorEmail,
    string? OperadorNome,
    string DataCriacao,
    string? SlaStatus = null)
{
    // Construtor retrocompatível para testes numéricos
    public ChamadoModel(
        int id,
        string titulo,
        string cliente,
        ChamadoStatus status,
        ChamadoPrioridade prioridade,
        string categoria,
        string? operadorEmail,
        DateTimeOffset dataCriacao,
        DateTimeOffset dataAtualizacao,
        DateTimeOffset? ultimaInteracao)
        : this(
            id.ToString(System.Globalization.CultureInfo.InvariantCulture),
            id.ToString(System.Globalization.CultureInfo.InvariantCulture),
            titulo,
            cliente,
            status.ToString(),
            prioridade.ToString(),
            categoria,
            operadorEmail,
            null,
            dataCriacao.ToString("yyyy-MM-dd HH:mm:ss", System.Globalization.CultureInfo.InvariantCulture),
            "Dentro do prazo")
    {
    }

    public string PrioridadeCor => Prioridade?.ToUpperInvariant() switch
    {
        "CRÍTICO" or "CRITICA" or "CRITICO" => "#FF0055",
        "ALTO" or "ALTA" => "#FF9A3C",
        "MÉDIO" or "MEDIA" or "MEDIO" => "#00F3FF",
        "BAIXO" or "BAIXA" or "PLANEJADO" => "#00FFA3",
        _ => "#8A99AD"
    };

    public string StatusCor => Status?.ToUpperInvariant() switch
    {
        "A FAZER" or "AFAZER" => "#FF9A3C",
        "ATENDENDO" => "#00FFA3",
        "PAUSADO" => "#8A99AD",
        _ => "#00F3FF"
    };

    public string CodigoFormatado => string.IsNullOrWhiteSpace(Codigo) ? $"#{Id}" : $"#{Codigo}";
}

/// <summary>
/// Modelo de Operador Oficial para balanceamento de carga e governança.
/// </summary>
public readonly record struct OperadorModel(
    string Email,
    string Nome,
    string Tier,
    bool Ativo,
    int ScoreCargaAtual,
    int ChamadosAtendendo = 0,
    int ChamadosAFazer = 0,
    int ChamadosPausados = 0,
    DateTimeOffset? UltimoChamadoAtribuidoEm = null)
{
    public OperadorModel(
        string email,
        string nome,
        OperadorTier tier,
        bool ativo,
        int scoreCargaAtual,
        DateTimeOffset? ultimoChamadoAtribuidoEm)
        : this(
            email,
            nome,
            tier.ToString(),
            ativo,
            scoreCargaAtual,
            0,
            0,
            0,
            ultimoChamadoAtribuidoEm)
    {
    }

    public double PercentualCarga => Math.Min(1.0, Math.Max(0.05, ScoreCargaAtual / 25.0));

    public string ScoreCor => ScoreCargaAtual switch
    {
        <= 5 => "#00FFA3",
        <= 12 => "#00F3FF",
        <= 18 => "#FF9A3C",
        _ => "#FF0055"
    };
}

/// <summary>
/// Métricas gerais agregadas de chamados no banco DuckDB.
/// </summary>
public readonly record struct DashboardKpiModel(
    int TotalChamados,
    int TotalAFazer,
    int TotalAtendendo,
    int TotalPausados,
    int TotalFinalizados,
    double ScoreMedio);

/// <summary>
/// Resultado detalhado de score e métricas de um operador.
/// </summary>
public readonly record struct ScoreCargaModel(
    string OperadorEmail,
    int ChamadosAtendendo,
    int ChamadosAFazer,
    int ChamadosPausados,
    int ScoreTotal,
    DateTimeOffset? UltimoChamadoAtribuidoEm);

/// <summary>
/// Payload estruturado de pacotes de comunicação do Gemini Live API.
/// </summary>
public sealed record GeminiLivePacket(
    string Tipo,
    string? MensagemTexto,
    byte[]? AudioPcmData,
    string? ToolNome,
    string? ToolArgumentosJson);
