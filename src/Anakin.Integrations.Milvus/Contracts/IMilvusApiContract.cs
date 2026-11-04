using System.Collections.Generic;
using System.Text.Json.Serialization;
using System.Threading.Tasks;
using Refit;

namespace Anakin.Integrations.Milvus.Contracts;

public interface IMilvusApiContract
{
    [Get("/chamado/lista")]
    Task<ApiResponse<MilvusTicketListResponse>> ListarChamadosAsync([Header("Authorization")] string token);

    [Get("/operador/lista")]
    Task<ApiResponse<MilvusOperatorListResponse>> ListarOperadoresAsync([Header("Authorization")] string token);

    [Post("/chamado/alterar-operador")]
    Task<ApiResponse<MilvusActionResponse>> AtribuirOperadorAsync(
        [Body] AtribuirOperadorRequest request,
        [Header("Authorization")] string token);
}

public sealed record MilvusTicketDto(
    [property: JsonPropertyName("id")] int Id,
    [property: JsonPropertyName("assunto")] string Assunto,
    [property: JsonPropertyName("cliente")] string Cliente,
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("prioridade")] int Prioridade,
    [property: JsonPropertyName("categoria")] string Categoria,
    [property: JsonPropertyName("operador_email")] string? OperadorEmail,
    [property: JsonPropertyName("data_criacao")] string DataCriacao,
    [property: JsonPropertyName("data_alteracao")] string DataAlteracao);

public sealed record MilvusTicketListResponse(
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("total")] int Total,
    [property: JsonPropertyName("chamados")] List<MilvusTicketDto> Chamados);

public sealed record MilvusOperatorDto(
    [property: JsonPropertyName("email")] string Email,
    [property: JsonPropertyName("nome")] string Nome,
    [property: JsonPropertyName("tier")] string Tier,
    [property: JsonPropertyName("ativo")] bool Ativo);

public sealed record MilvusOperatorListResponse(
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("operadores")] List<MilvusOperatorDto> Operadores);

public sealed record AtribuirOperadorRequest(
    [property: JsonPropertyName("chamado_id")] int ChamadoId,
    [property: JsonPropertyName("operador_email")] string OperadorEmail);

public sealed record MilvusActionResponse(
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("mensagem")] string Mensagem);
