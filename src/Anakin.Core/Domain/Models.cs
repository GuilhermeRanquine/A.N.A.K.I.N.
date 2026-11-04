using System;

namespace Anakin.Core.Domain;

/// <summary>
/// Representação imutável de um Chamado com alocação otimizada.
/// </summary>
public readonly record struct ChamadoModel(
    int Id,
    string Titulo,
    string Cliente,
    ChamadoStatus Status,
    ChamadoPrioridade Prioridade,
    string Categoria,
    string? OperadorEmail,
    DateTimeOffset DataCriacao,
    DateTimeOffset DataAtualizacao,
    DateTimeOffset? UltimaInteracao);

/// <summary>
/// Modelo de Operador para balanceamento de carga e governança.
/// </summary>
public readonly record struct OperadorModel(
    string Email,
    string Nome,
    OperadorTier Tier,
    bool Ativo,
    int ScoreCargaAtual,
    DateTimeOffset? UltimoChamadoAtribuidoEm);

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
