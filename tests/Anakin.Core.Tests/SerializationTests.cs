using System;
using System.Text.Json;
using FluentAssertions;
using Xunit;
using Anakin.Core.Domain;
using Anakin.Core.Serialization;

namespace Anakin.Core.Tests;

public sealed class SerializationTests
{
    [Fact]
    public void SerializarEDesserializar_ChamadoModel_ComSourceGenerator_DeveSerIdentico()
    {
        // Arrange
        var agora = DateTimeOffset.UtcNow;
        var chamado = new ChamadoModel(
            123,
            "Problema de Rede",
            "Acme Corp",
            ChamadoStatus.Atendendo,
            ChamadoPrioridade.Critica,
            "Infraestrutura",
            "operador@empresa.com",
            agora,
            agora,
            null);

        // Act - Serialização sem reflexão via Source Generator
        var json = JsonSerializer.Serialize(chamado, AnakinJsonContext.Default.ChamadoModel);
        var restaurado = JsonSerializer.Deserialize(json, AnakinJsonContext.Default.ChamadoModel);

        // Assert
        restaurado.Id.Should().Be(chamado.Id);
        restaurado.Titulo.Should().Be(chamado.Titulo);
        restaurado.Cliente.Should().Be(chamado.Cliente);
        restaurado.Status.Should().Be(chamado.Status);
        restaurado.Prioridade.Should().Be(chamado.Prioridade);
        restaurado.Categoria.Should().Be(chamado.Categoria);
        restaurado.OperadorEmail.Should().Be(chamado.OperadorEmail);
    }
}
