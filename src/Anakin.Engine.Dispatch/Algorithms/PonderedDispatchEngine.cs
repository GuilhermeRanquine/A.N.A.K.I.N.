using System;
using System.Collections.Generic;
using System.Linq;
using Anakin.Core.Domain;
using Anakin.Core.Interfaces;

namespace Anakin.Engine.Dispatch.Algorithms;

/// <summary>
/// Motor de despacho com balanceamento ponderado de carga e regras de governança estritas.
/// Garante matematicamente a menor carga de trabalho e desempate temporal determinístico.
/// </summary>
public sealed class PonderedDispatchEngine : IDispatchEngine
{
    private static readonly HashSet<string> DefaultBlockedEmails = new(StringComparer.OrdinalIgnoreCase)
    {
        "coordenador@empresa.com.br",
        "gerente@empresa.com.br",
        "diretor@empresa.com.br"
    };

    public OperadorModel? SelecionarMelhorOperador(
        IReadOnlyList<OperadorModel> operadores,
        ChamadoModel chamado,
        IReadOnlySet<string>? emailsBloqueados = null)
    {
        if (operadores == null || operadores.Count == 0)
        {
            return null;
        }

        var blocked = emailsBloqueados ?? DefaultBlockedEmails;

        // 1. Filtrar elegíveis: Ativo, Não-Gestor, Não-Bloqueado
        var elegiveis = operadores
            .Where(op => op.Ativo)
            .Where(op => !string.Equals(op.Tier, nameof(OperadorTier.Gestao), StringComparison.OrdinalIgnoreCase))
            .Where(op => !blocked.Contains(op.Email))
            .ToList();

        if (elegiveis.Count == 0)
        {
            return null;
        }

        // 2. Determinar Tier alvo com base na prioridade do chamado
        var isCritico = string.Equals(chamado.Prioridade, "Critica", StringComparison.OrdinalIgnoreCase)
                     || string.Equals(chamado.Prioridade, "Crítico", StringComparison.OrdinalIgnoreCase);

        var tierAlvo = isCritico ? nameof(OperadorTier.N2) : nameof(OperadorTier.N1);

        var poolTier = elegiveis
            .Where(op => string.Equals(op.Tier, tierAlvo, StringComparison.OrdinalIgnoreCase))
            .ToList();

        // Se não houver operadores no tier alvo, faz fallback para todos os elegíveis
        var candidatos = poolTier.Count > 0 ? poolTier : elegiveis;

        // 3. Critério de seleção:
        //    a) Menor Score de Carga Atual
        //    b) Desempate: Atribuição mais antiga (ou nula se nunca recebeu)
        //    c) Desempate determinístico por Email
        return candidatos
            .OrderBy(op => op.ScoreCargaAtual)
            .ThenBy(op => op.UltimoChamadoAtribuidoEm ?? DateTimeOffset.MinValue)
            .ThenBy(op => op.Email, StringComparer.OrdinalIgnoreCase)
            .First();
    }
}
