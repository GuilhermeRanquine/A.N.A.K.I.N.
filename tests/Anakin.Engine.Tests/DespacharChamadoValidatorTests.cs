using FluentAssertions;
using Xunit;
using Anakin.Core.Domain;
using Anakin.Engine.Dispatch.Commands;
using Anakin.Engine.Dispatch.Validators;

namespace Anakin.Engine.Tests;

public sealed class DespacharChamadoValidatorTests
{
    private readonly DespacharChamadoValidator _validator = new();

    [Fact]
    public void Validador_QuandoChamadoValido_DeveRetornarSucesso()
    {
        // Arrange
        var command = new DespacharChamadoCommand(500, "analista.suporte@empresa.com.br", ChamadoPrioridade.Media);

        // Act
        var result = _validator.Validate(command);

        // Assert
        result.IsValid.Should().BeTrue();
    }

    [Theory]
    [InlineData(0)]
    [InlineData(-1)]
    [InlineData(-99)]
    public void Validador_QuandoChamadoIdInvalido_DeveFalhar(int idInvalido)
    {
        // Arrange
        var command = new DespacharChamadoCommand(idInvalido, "analista.suporte@empresa.com.br", ChamadoPrioridade.Media);

        // Act
        var result = _validator.Validate(command);

        // Assert
        result.IsValid.Should().BeFalse();
        result.Errors.Should().Contain(e => e.PropertyName == nameof(DespacharChamadoCommand.ChamadoId));
    }

    [Theory]
    [InlineData("")]
    [InlineData(" ")]
    [InlineData("email-invalido-sem-arroba")]
    public void Validador_QuandoEmailInvalido_DeveFalhar(string emailInvalido)
    {
        // Arrange
        var command = new DespacharChamadoCommand(100, emailInvalido, ChamadoPrioridade.Media);

        // Act
        var result = _validator.Validate(command);

        // Assert
        result.IsValid.Should().BeFalse();
        result.Errors.Should().Contain(e => e.PropertyName == nameof(DespacharChamadoCommand.OperadorEmail));
    }

    [Theory]
    [InlineData("coordenador@empresa.com.br")]
    [InlineData("gerente@empresa.com.br")]
    [InlineData("diretor@empresa.com.br")]
    public void Validador_QuandoEmailPertencerAGestor_DeveBloquearComViolacaoDeGovernanca(string emailGestor)
    {
        // Arrange
        var command = new DespacharChamadoCommand(100, emailGestor, ChamadoPrioridade.Alta);

        // Act
        var result = _validator.Validate(command);

        // Assert
        result.IsValid.Should().BeFalse();
        result.Errors.Should().Contain(e => e.ErrorMessage.Contains("VIOLAÇÃO DE GOVERNANÇA"));
    }
}
