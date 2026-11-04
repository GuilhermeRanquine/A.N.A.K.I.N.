using System;
using System.Collections.Generic;
using FluentValidation;
using Anakin.Engine.Dispatch.Commands;

namespace Anakin.Engine.Dispatch.Validators;

/// <summary>
/// Validador de governança corporativa e integridade para despacho de chamados.
/// Bloqueia terminantemente atribuições a perfis de gestão e previne dados inválidos.
/// </summary>
public sealed class DespacharChamadoValidator : AbstractValidator<DespacharChamadoCommand>
{
    private static readonly HashSet<string> GestoresBloqueados = new(StringComparer.OrdinalIgnoreCase)
    {
        "coordenador@empresa.com.br",
        "gerente@empresa.com.br",
        "diretor@empresa.com.br"
    };

    public DespacharChamadoValidator()
    {
        RuleFor(x => x.ChamadoId)
            .GreaterThan(0)
            .WithMessage("O identificador do chamado deve ser estritamente maior que zero.");

        RuleFor(x => x.OperadorEmail)
            .NotEmpty()
            .WithMessage("O e-mail do operador não pode ser nulo ou vazio.")
            .EmailAddress()
            .WithMessage("Formato de e-mail inválido para o operador.")
            .Must(email => !GestoresBloqueados.Contains(email))
            .WithMessage("VIOLAÇÃO DE GOVERNANÇA: Gestores e diretores não podem receber chamados na fila automática.");
    }
}
