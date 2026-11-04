using System.Text.Json.Serialization;
using Anakin.Core.Domain;

namespace Anakin.Core.Serialization;

/// <summary>
/// Contexto gerador de código fonte JSON em tempo de compilação.
/// Elimina reflexão em tempo de execução, zera alocações na Heap e habilita suporte total ao Native AOT.
/// </summary>
[JsonSourceGenerationOptions(
    WriteIndented = false,
    PropertyNamingPolicy = JsonKnownNamingPolicy.CamelCase,
    GenerationMode = JsonSourceGenerationMode.Default,
    DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull)]
[JsonSerializable(typeof(ChamadoModel))]
[JsonSerializable(typeof(ChamadoModel[]))]
[JsonSerializable(typeof(OperadorModel))]
[JsonSerializable(typeof(OperadorModel[]))]
[JsonSerializable(typeof(ScoreCargaModel))]
[JsonSerializable(typeof(GeminiLivePacket))]
public partial class AnakinJsonContext : JsonSerializerContext
{
}
