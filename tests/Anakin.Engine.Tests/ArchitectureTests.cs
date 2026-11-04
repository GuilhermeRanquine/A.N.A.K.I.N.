using FluentAssertions;
using NetArchTest.Rules;
using Xunit;
using Anakin.Core.Domain;
using Anakin.Engine.Dispatch.Algorithms;

namespace Anakin.Engine.Tests;

public sealed class ArchitectureTests
{
    [Fact]
    public void AnakinCore_NaoDeveDependerDe_OutrasCamadasExternas()
    {
        // Camada Core é o centro do DDD e não pode depender de Engine, UI, Storage ou Integrations
        var result = Types.InAssembly(typeof(ChamadoModel).Assembly)
            .ShouldNot()
            .HaveDependencyOnAny(
                "Anakin.Engine.Dispatch",
                "Anakin.UI.CyberHUD",
                "Anakin.Integrations.Milvus",
                "Anakin.Integrations.GeminiLive",
                "Anakin.Integrations.Audio",
                "Anakin.Storage.DuckDb")
            .GetResult();

        result.IsSuccessful.Should().BeTrue("A camada Core deve ser pura e não possuir dependências externas ou de infraestrutura.");
    }
}
