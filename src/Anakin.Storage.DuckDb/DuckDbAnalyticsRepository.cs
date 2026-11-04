using System;
using System.Collections.Generic;
using System.Data;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using DuckDB.NET.Data;
using Microsoft.Extensions.Logging;
using Anakin.Core.Domain;
using Anakin.Core.Interfaces;

namespace Anakin.Storage.DuckDb;

/// <summary>
/// Repositório analítico de alta performance utilizando banco de dados colunar embutido DuckDB.
/// Executa agregações e projeções com latência em microssegundos e zero overhead de cota externa.
/// </summary>
public sealed partial class DuckDbAnalyticsRepository : IAnalyticsRepository
{
    private readonly string _connectionString;
    private readonly ILogger<DuckDbAnalyticsRepository> _logger;

    public DuckDbAnalyticsRepository(string? databasePath, ILogger<DuckDbAnalyticsRepository> logger)
    {
        _logger = logger;
        if (string.IsNullOrWhiteSpace(databasePath))
        {
            _connectionString = "Data Source=:memory:";
        }
        else
        {
            var directory = Path.GetDirectoryName(databasePath);
            if (!string.IsNullOrEmpty(directory) && !Directory.Exists(directory))
            {
                Directory.CreateDirectory(directory);
            }
            _connectionString = $"Data Source={databasePath}";
        }
    }

    public async Task InicializarBancoAsync(CancellationToken ct = default)
    {
        using var connection = new DuckDBConnection(_connectionString);
        await connection.OpenAsync(ct).ConfigureAwait(false);

        using var command = connection.CreateCommand();
        command.CommandText = @"
            CREATE TABLE IF NOT EXISTS operadores (
                email VARCHAR PRIMARY KEY,
                nome VARCHAR NOT NULL,
                tier INTEGER NOT NULL,
                ativo BOOLEAN NOT NULL,
                score_carga_atual INTEGER NOT NULL,
                ultimo_chamado_atribuido_em TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS chamados_ativos (
                id INTEGER PRIMARY KEY,
                titulo VARCHAR NOT NULL,
                cliente VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                prioridade INTEGER NOT NULL,
                categoria VARCHAR NOT NULL,
                operador_email VARCHAR,
                data_criacao TIMESTAMP NOT NULL,
                data_atualizacao TIMESTAMP NOT NULL,
                ultima_interacao TIMESTAMP
            );
        ";

        await command.ExecuteNonQueryAsync(ct).ConfigureAwait(false);
        LogBancoInicializado(_logger, _connectionString);
    }

    public async Task InserirOuAtualizarChamadoAsync(ChamadoModel chamado, CancellationToken ct = default)
    {
        using var connection = new DuckDBConnection(_connectionString);
        await connection.OpenAsync(ct).ConfigureAwait(false);

        using var command = connection.CreateCommand();
        command.CommandText = @"
            INSERT OR REPLACE INTO chamados_ativos (
                id, titulo, cliente, status, prioridade, categoria, 
                operador_email, data_criacao, data_atualizacao, ultima_interacao
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10
            );
        ";

        command.Parameters.Add(new DuckDBParameter(chamado.Id));
        command.Parameters.Add(new DuckDBParameter(chamado.Titulo));
        command.Parameters.Add(new DuckDBParameter(chamado.Cliente));
        command.Parameters.Add(new DuckDBParameter(chamado.Status.ToString()));
        command.Parameters.Add(new DuckDBParameter((int)chamado.Prioridade));
        command.Parameters.Add(new DuckDBParameter(chamado.Categoria));
        command.Parameters.Add(new DuckDBParameter(chamado.OperadorEmail ?? (object)DBNull.Value));
        command.Parameters.Add(new DuckDBParameter(chamado.DataCriacao.UtcDateTime));
        command.Parameters.Add(new DuckDBParameter(chamado.DataAtualizacao.UtcDateTime));
        command.Parameters.Add(new DuckDBParameter(chamado.UltimaInteracao.HasValue ? chamado.UltimaInteracao.Value.UtcDateTime : (object)DBNull.Value));

        await command.ExecuteNonQueryAsync(ct).ConfigureAwait(false);
    }

    public async Task InserirOuAtualizarOperadorAsync(OperadorModel operador, CancellationToken ct = default)
    {
        using var connection = new DuckDBConnection(_connectionString);
        await connection.OpenAsync(ct).ConfigureAwait(false);

        using var command = connection.CreateCommand();
        command.CommandText = @"
            INSERT OR REPLACE INTO operadores (
                email, nome, tier, ativo, score_carga_atual, ultimo_chamado_atribuido_em
            ) VALUES (
                $1, $2, $3, $4, $5, $6
            );
        ";

        command.Parameters.Add(new DuckDBParameter(operador.Email));
        command.Parameters.Add(new DuckDBParameter(operador.Nome));
        command.Parameters.Add(new DuckDBParameter((int)operador.Tier));
        command.Parameters.Add(new DuckDBParameter(operador.Ativo));
        command.Parameters.Add(new DuckDBParameter(operador.ScoreCargaAtual));
        command.Parameters.Add(new DuckDBParameter(operador.UltimoChamadoAtribuidoEm.HasValue ? operador.UltimoChamadoAtribuidoEm.Value.UtcDateTime : (object)DBNull.Value));

        await command.ExecuteNonQueryAsync(ct).ConfigureAwait(false);
    }

    public async Task<int> ObterScoreOperadorAsync(string operadorEmail, CancellationToken ct = default)
    {
        using var connection = new DuckDBConnection(_connectionString);
        await connection.OpenAsync(ct).ConfigureAwait(false);

        using var command = connection.CreateCommand();
        command.CommandText = @"
            SELECT 
                COALESCE(SUM(
                    CASE 
                        WHEN status = 'Atendendo' THEN 3
                        WHEN status = 'AFazer' THEN 2
                        WHEN status = 'Pausado' THEN 1
                        ELSE 1
                    END
                ), 0) AS ScoreCarga
            FROM chamados_ativos
            WHERE operador_email = $1;";

        command.Parameters.Add(new DuckDBParameter(operadorEmail));
        var result = await command.ExecuteScalarAsync(ct).ConfigureAwait(false);
        return Convert.ToInt32(result, System.Globalization.CultureInfo.InvariantCulture);
    }

    public async Task<IReadOnlyList<OperadorModel>> ObterOperadoresAsync(OperadorTier? tier = null, CancellationToken ct = default)
    {
        using var connection = new DuckDBConnection(_connectionString);
        await connection.OpenAsync(ct).ConfigureAwait(false);

        using var command = connection.CreateCommand();
        if (tier.HasValue)
        {
            command.CommandText = "SELECT email, nome, tier, ativo, score_carga_atual, ultimo_chamado_atribuido_em FROM operadores WHERE tier = $1 ORDER BY score_carga_atual ASC;";
            command.Parameters.Add(new DuckDBParameter((int)tier.Value));
        }
        else
        {
            command.CommandText = "SELECT email, nome, tier, ativo, score_carga_atual, ultimo_chamado_atribuido_em FROM operadores ORDER BY tier ASC, score_carga_atual ASC;";
        }

        var lista = new List<OperadorModel>();
        using var reader = await command.ExecuteReaderAsync(ct).ConfigureAwait(false);
        while (await reader.ReadAsync(ct).ConfigureAwait(false))
        {
            var email = reader.GetString(0);
            var nome = reader.GetString(1);
            var opTier = (OperadorTier)reader.GetInt32(2);
            var ativo = reader.GetBoolean(3);
            var score = reader.GetInt32(4);
            DateTimeOffset? ultimo = reader.IsDBNull(5) ? null : new DateTimeOffset(reader.GetDateTime(5), TimeSpan.Zero);

            lista.Add(new OperadorModel(email, nome, opTier, ativo, score, ultimo));
        }

        return lista;
    }

    public async Task<IReadOnlyList<ChamadoModel>> ObterChamadosNaoAtribuidosAsync(CancellationToken ct = default)
    {
        using var connection = new DuckDBConnection(_connectionString);
        await connection.OpenAsync(ct).ConfigureAwait(false);

        using var command = connection.CreateCommand();
        command.CommandText = @"
            SELECT id, titulo, cliente, status, prioridade, categoria, operador_email, data_criacao, data_atualizacao, ultima_interacao
            FROM chamados_ativos
            WHERE operador_email IS NULL OR operador_email = ''
            ORDER BY prioridade DESC, data_criacao ASC;
        ";

        var lista = new List<ChamadoModel>();
        using var reader = await command.ExecuteReaderAsync(ct).ConfigureAwait(false);
        while (await reader.ReadAsync(ct).ConfigureAwait(false))
        {
            var id = reader.GetInt32(0);
            var titulo = reader.GetString(1);
            var cliente = reader.GetString(2);
            var statusStr = reader.GetString(3);
            _ = Enum.TryParse<ChamadoStatus>(statusStr, out var status);
            var prioridade = (ChamadoPrioridade)reader.GetInt32(4);
            var categoria = reader.GetString(5);
            var operadorEmail = reader.IsDBNull(6) ? null : reader.GetString(6);
            var dataCriacao = new DateTimeOffset(reader.GetDateTime(7), TimeSpan.Zero);
            var dataAtualizacao = new DateTimeOffset(reader.GetDateTime(8), TimeSpan.Zero);
            DateTimeOffset? ultimaInteracao = reader.IsDBNull(9) ? null : new DateTimeOffset(reader.GetDateTime(9), TimeSpan.Zero);

            lista.Add(new ChamadoModel(id, titulo, cliente, status, prioridade, categoria, operadorEmail, dataCriacao, dataAtualizacao, ultimaInteracao));
        }

        return lista;
    }

    [LoggerMessage(EventId = 2001, Level = LogLevel.Information, Message = "Banco DuckDB analítico inicializado com sucesso. ConnectionString: {ConnectionString}")]
    private static partial void LogBancoInicializado(ILogger logger, string connectionString);
}
