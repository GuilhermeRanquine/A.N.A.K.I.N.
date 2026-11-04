using System;
using System.IO;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Channels;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging;
using Anakin.Core.Domain;

namespace Anakin.Integrations.GeminiLive;

/// <summary>
/// Cliente WebSocket de altíssima performance para streaming bidirecional de voz com a Gemini Live API.
/// Gerencia canais assíncronos lock-free de áudio PCM e eventos de Tool Calling ao vivo.
/// </summary>
public sealed partial class GeminiLiveWebSocketClient : IAsyncDisposable
{
    private readonly ClientWebSocket _webSocket = new();
    private readonly ILogger<GeminiLiveWebSocketClient> _logger;
    private readonly Channel<byte[]> _audioInboundChannel;
    private readonly Channel<byte[]> _audioOutboundChannel;
    private CancellationTokenSource? _cts;

    public ChannelReader<byte[]> AudioInboundReader => _audioInboundChannel.Reader;
    public ChannelWriter<byte[]> AudioOutboundWriter => _audioOutboundChannel.Writer;

    public GeminiLiveWebSocketClient(ILogger<GeminiLiveWebSocketClient> logger)
    {
        _logger = logger;
        _audioInboundChannel = Channel.CreateBounded<byte[]>(new BoundedChannelOptions(500)
        {
            FullMode = BoundedChannelFullMode.DropOldest,
            SingleWriter = true,
            SingleReader = true
        });

        _audioOutboundChannel = Channel.CreateBounded<byte[]>(new BoundedChannelOptions(500)
        {
            FullMode = BoundedChannelFullMode.Wait,
            SingleWriter = false,
            SingleReader = true
        });
    }

    public async Task ConectarAsync(string apiKey, CancellationToken ct = default)
    {
        _cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        var uri = new Uri($"wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent?key={apiKey}");

        LogIniciandoConexao(_logger);
        await _webSocket.ConnectAsync(uri, ct).ConfigureAwait(false);
        LogConectadoComSucesso(_logger);

        // Dispara loops de streaming em background
        _ = Task.Run(() => LoopRecebimentoAsync(_cts.Token), _cts.Token);
        _ = Task.Run(() => LoopEnvioAsync(_cts.Token), _cts.Token);
    }

    private async Task LoopRecebimentoAsync(CancellationToken ct)
    {
        var buffer = new byte[8192];
        try
        {
            while (!ct.IsCancellationRequested && _webSocket.State == WebSocketState.Open)
            {
                var result = await _webSocket.ReceiveAsync(new ArraySegment<byte>(buffer), ct).ConfigureAwait(false);
                if (result.MessageType == WebSocketMessageType.Close)
                {
                    await _webSocket.CloseAsync(WebSocketCloseStatus.NormalClosure, "Fechado pelo servidor", ct).ConfigureAwait(false);
                    break;
                }

                if (result.MessageType == WebSocketMessageType.Binary && result.Count > 0)
                {
                    var chunk = new byte[result.Count];
                    Array.Copy(buffer, chunk, result.Count);
                    await _audioInboundChannel.Writer.WriteAsync(chunk, ct).ConfigureAwait(false);
                }
            }
        }
        catch (OperationCanceledException)
        {
            // Cancelamento gracioso
        }
        catch (Exception ex)
        {
            LogErroStreaming(_logger, ex.Message);
        }
    }

    private async Task LoopEnvioAsync(CancellationToken ct)
    {
        try
        {
            await foreach (var chunk in _audioOutboundChannel.Reader.ReadAllAsync(ct).ConfigureAwait(false))
            {
                if (_webSocket.State == WebSocketState.Open)
                {
                    await _webSocket.SendAsync(
                        new ArraySegment<byte>(chunk),
                        WebSocketMessageType.Binary,
                        endOfMessage: true,
                        ct).ConfigureAwait(false);
                }
            }
        }
        catch (OperationCanceledException)
        {
            // Cancelamento gracioso
        }
        catch (Exception ex)
        {
            LogErroStreaming(_logger, ex.Message);
        }
    }

    public async ValueTask DisposeAsync()
    {
        _cts?.Cancel();
        _audioInboundChannel.Writer.TryComplete();
        _audioOutboundChannel.Writer.TryComplete();

        if (_webSocket.State == WebSocketState.Open)
        {
            try
            {
                await _webSocket.CloseAsync(WebSocketCloseStatus.NormalClosure, "Desconectando", CancellationToken.None).ConfigureAwait(false);
            }
            catch
            {
                // Ignorar em teardown
            }
        }

        _webSocket.Dispose();
        _cts?.Dispose();
    }

    [LoggerMessage(EventId = 4000, Level = LogLevel.Information, Message = "Iniciando conexão WSS com Gemini Live API...")]
    private static partial void LogIniciandoConexao(ILogger logger);

    [LoggerMessage(EventId = 4001, Level = LogLevel.Information, Message = "Conexão WebSocket com Gemini Live API estabelecida com sucesso.")]
    private static partial void LogConectadoComSucesso(ILogger logger);

    [LoggerMessage(EventId = 4002, Level = LogLevel.Error, Message = "Erro durante o streaming bidirecional da Gemini Live API: {Error}")]
    private static partial void LogErroStreaming(ILogger logger, string error);
}
