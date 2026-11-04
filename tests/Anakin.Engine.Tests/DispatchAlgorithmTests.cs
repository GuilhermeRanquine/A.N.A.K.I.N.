using System;
using System.Collections.Generic;
using FluentAssertions;
using Xunit;
using Anakin.Core.Domain;
using Anakin.Engine.Dispatch.Algorithms;

namespace Anakin.Engine.Tests;

public sealed class DispatchAlgorithmTests
{
    private readonly PonderedDispatchEngine _engine = new();

    [Fact]
    public void SelecionarMelhorOperador_DeveEscolherOperadorComMenorScore()
    {
        // Arrange
        var op1 = new OperadorModel("op1@empresa.com", "Operador 1", OperadorTier.N1, true, 8, DateTimeOffset.UtcNow.AddMinutes(-30));
        var op2 = new OperadorModel("op2@empresa.com", "Operador 2", OperadorTier.N1, true, 3, DateTimeOffset.UtcNow.AddMinutes(-10));
        var op3 = new OperadorModel("op3@empresa.com", "Operador 3", OperadorTier.N1, true, 6, DateTimeOffset.UtcNow.AddMinutes(-20));

        var chamado = new ChamadoModel(101, "Chamado Teste", "Cliente A", ChamadoStatus.AFazer, ChamadoPrioridade.Media, "Redes", null, DateTimeOffset.UtcNow, DateTimeOffset.UtcNow, null);

        // Act
        var escolhido = _engine.SelecionarMelhorOperador(new[] { op1, op2, op3 }, chamado);

        // Assert
        escolhido.Should().NotBeNull();
        escolhido!.Value.Email.Should().Be("op2@empresa.com");
    }

    [Fact]
    public void SelecionarMelhorOperador_QuandoScoresForemIguais_DeveDesempatarPelaAtribuicaoMaisAntiga()
    {
        // Arrange
        var agora = DateTimeOffset.UtcNow;
        var opA = new OperadorModel("opA@empresa.com", "Operador A", OperadorTier.N1, true, 5, agora.AddMinutes(-40));
        var opB = new OperadorModel("opB@empresa.com", "Operador B", OperadorTier.N1, true, 5, agora.AddMinutes(-15));

        var chamado = new ChamadoModel(102, "Chamado Teste 2", "Cliente B", ChamadoStatus.AFazer, ChamadoPrioridade.Media, "Software", null, agora, agora, null);

        // Act
        var escolhido = _engine.SelecionarMelhorOperador(new[] { opA, opB }, chamado);

        // Assert
        escolhido.Should().NotBeNull();
        escolhido!.Value.Email.Should().Be("opA@empresa.com");
    }

    [Fact]
    public void SelecionarMelhorOperador_DeveBloquearGestores_ConformeRegraDeGovernanca()
    {
        // Arrange
        var gestor = new OperadorModel("coordenador@empresa.com.br", "Coordenador Geral", OperadorTier.Gestao, true, 0, null);
        var opN1 = new OperadorModel("analista@empresa.com", "Analista N1", OperadorTier.N1, true, 4, DateTimeOffset.UtcNow.AddMinutes(-5));

        var chamado = new ChamadoModel(103, "Chamado Governança", "Cliente C", ChamadoStatus.AFazer, ChamadoPrioridade.Baixa, "Suporte", null, DateTimeOffset.UtcNow, DateTimeOffset.UtcNow, null);

        // Act
        var escolhido = _engine.SelecionarMelhorOperador(new[] { gestor, opN1 }, chamado);

        // Assert
        escolhido.Should().NotBeNull();
        escolhido!.Value.Email.Should().Be("analista@empresa.com");
    }

    [Fact]
    public void SelecionarMelhorOperador_QuandoPrioridadeForCritica_DevePriorizarOperadorN2()
    {
        // Arrange
        var opN1 = new OperadorModel("n1@empresa.com", "Analista N1", OperadorTier.N1, true, 1, DateTimeOffset.UtcNow.AddMinutes(-20));
        var opN2 = new OperadorModel("n2@empresa.com", "Especialista N2", OperadorTier.N2, true, 3, DateTimeOffset.UtcNow.AddMinutes(-10));

        var chamadoCritico = new ChamadoModel(104, "Incidente Crítico de Infra", "Cliente Enterprise", ChamadoStatus.AFazer, ChamadoPrioridade.Critica, "Infraestrutura", null, DateTimeOffset.UtcNow, DateTimeOffset.UtcNow, null);

        // Act
        var escolhido = _engine.SelecionarMelhorOperador(new[] { opN1, opN2 }, chamadoCritico);

        // Assert
        escolhido.Should().NotBeNull();
        escolhido!.Value.Email.Should().Be("n2@empresa.com");
    }

    [Fact]
    public void SelecionarMelhorOperador_DeveIgnorarOperadoresInativos()
    {
        // Arrange
        var opInativo = new OperadorModel("inativo@empresa.com", "Operador Inativo", OperadorTier.N1, false, 0, null);
        var opAtivo = new OperadorModel("ativo@empresa.com", "Operador Ativo", OperadorTier.N1, true, 7, DateTimeOffset.UtcNow);

        var chamado = new ChamadoModel(105, "Chamado Simples", "Cliente D", ChamadoStatus.AFazer, ChamadoPrioridade.Baixa, "Geral", null, DateTimeOffset.UtcNow, DateTimeOffset.UtcNow, null);

        // Act
        var escolhido = _engine.SelecionarMelhorOperador(new[] { opInativo, opAtivo }, chamado);

        // Assert
        escolhido.Should().NotBeNull();
        escolhido!.Value.Email.Should().Be("ativo@empresa.com");
    }
}
