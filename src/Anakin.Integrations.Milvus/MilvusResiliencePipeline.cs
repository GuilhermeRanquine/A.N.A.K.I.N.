using System;
using System.Net;
using System.Net.Http;
using System.Threading.RateLimiting;
using Polly;
using Polly.Retry;

namespace Anakin.Integrations.Milvus;

/// <summary>
/// Provedor central das políticas de resiliência Polly v8 para a API Milvus Helpdesk.
/// Protege limites de taxa (Rate Limit) e mitiga falhas transitórias com Retry Exponencial + Jitter Decorrelacionado.
/// </summary>
public static class MilvusResiliencePipeline
{
    public static ResiliencePipeline<HttpResponseMessage> CriarPipelineHttp()
    {
        return new ResiliencePipelineBuilder<HttpResponseMessage>()
            .AddRateLimiter(new SlidingWindowRateLimiter(new SlidingWindowRateLimiterOptions
            {
                PermitLimit = 30,
                Window = TimeSpan.FromMinutes(1),
                SegmentsPerWindow = 6,
                QueueProcessingOrder = QueueProcessingOrder.OldestFirst,
                QueueLimit = 10
            }))
            .AddRetry(new RetryStrategyOptions<HttpResponseMessage>
            {
                MaxRetryAttempts = 3,
                BackoffType = DelayBackoffType.Exponential,
                UseJitter = true,
                Delay = TimeSpan.FromSeconds(2),
                ShouldHandle = new PredicateBuilder<HttpResponseMessage>()
                    .Handle<HttpRequestException>()
                    .HandleResult(r => r.StatusCode == HttpStatusCode.TooManyRequests
                                    || (int)r.StatusCode >= 500)
            })
            .AddTimeout(TimeSpan.FromSeconds(15))
            .Build();
    }
}
