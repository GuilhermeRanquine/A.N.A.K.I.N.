using System;
using System.IO;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging;
using NAudio.Wave;
using Anakin.Core.Interfaces;

namespace Anakin.Integrations.Audio;

/// <summary>
/// Sintetizador de áudio tático procedural em memória.
/// Gera bips senoidais e chimes operacionais em tempo de execução com decaimento harmônico, sem tocar o disco.
/// </summary>
public sealed partial class TacticalSoundSynthesizer : ISoundSynthesizer
{
    private readonly ILogger<TacticalSoundSynthesizer> _logger;

    public TacticalSoundSynthesizer(ILogger<TacticalSoundSynthesizer> logger)
    {
        _logger = logger;
    }

    public void PlayTacticalChime(double frequency = 880.0, int durationMs = 120)
    {
        Task.Run(() => SynthesizeAndPlay(frequency, durationMs, 12000, 4.0));
    }

    public void PlaySuccessAlert()
    {
        Task.Run(() =>
        {
            SynthesizeAndPlay(784.0, 90, 10000, 3.5); // G5
            SynthesizeAndPlay(1046.5, 140, 12000, 3.0); // C6
        });
    }

    public void PlayWarningAlert()
    {
        Task.Run(() =>
        {
            SynthesizeAndPlay(587.33, 100, 12000, 4.0); // D5
            SynthesizeAndPlay(440.0, 160, 14000, 3.0); // A4
        });
    }

    public void PlayCriticalAlert()
    {
        Task.Run(() =>
        {
            for (int i = 0; i < 3; i++)
            {
                SynthesizeAndPlay(880.0, 70, 15000, 5.0);
            }
        });
    }

    private void SynthesizeAndPlay(double frequency, int durationMs, short amplitude, double decayFactor)
    {
        try
        {
            const int sampleRate = 44100;
            var samples = (int)(sampleRate * (durationMs / 1000.0));
            if (samples <= 0) return;

            var buffer = new byte[samples * 2];

            for (int i = 0; i < samples; i++)
            {
                double decay = Math.Exp(-decayFactor * i / samples);
                short sample = (short)(Math.Sin(2.0 * Math.PI * frequency * i / sampleRate) * amplitude * decay);
                buffer[i * 2] = (byte)(sample & 0xFF);
                buffer[i * 2 + 1] = (byte)((sample >> 8) & 0xFF);
            }

            using var ms = new MemoryStream(buffer);
            using var rawSource = new RawSourceWaveStream(ms, new WaveFormat(sampleRate, 16, 1));
            using var waveOut = new WaveOutEvent();
            waveOut.Init(rawSource);
            waveOut.Play();

            while (waveOut.PlaybackState == PlaybackState.Playing)
            {
                System.Threading.Thread.Sleep(5);
            }
        }
        catch (Exception ex)
        {
            LogFalhaSinteseAudio(_logger, ex.Message);
        }
    }

    [LoggerMessage(EventId = 3001, Level = LogLevel.Warning, Message = "Não foi possível reproduzir som tático procedural: {Error}")]
    private static partial void LogFalhaSinteseAudio(ILogger logger, string error);
}
