using System;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Media;
using Avalonia.Threading;

namespace Anakin.UI.CyberHUD.Controls;

/// <summary>
/// Controle visual de osciloscópio tático futurista acelerado por GPU (SkiaSharp).
/// Desenha ondas harmônicas em tempo real simulando o núcleo de IA e streaming de voz.
/// </summary>
public sealed class TacticalWaveformControl : Control
{
    private double _phase;
    private readonly DispatcherTimer _timer;

    public TacticalWaveformControl()
    {
        _timer = new DispatcherTimer
        {
            Interval = TimeSpan.FromMilliseconds(25)
        };
        _timer.Tick += (_, _) =>
        {
            _phase += 0.08;
            InvalidateVisual();
        };
        _timer.Start();
    }

    public override void Render(DrawingContext context)
    {
        base.Render(context);
        var width = Bounds.Width;
        var height = Bounds.Height;
        if (width <= 0 || height <= 0) return;

        var midY = height / 2.0;

        // Fundo de grade holográfica sutil
        var gridPen = new Pen(new SolidColorBrush(Color.FromArgb(20, 0, 243, 255)), 1);
        for (double x = 0; x < width; x += 24)
        {
            context.DrawLine(gridPen, new Point(x, 0), new Point(x, height));
        }
        for (double y = 0; y < height; y += 16)
        {
            context.DrawLine(gridPen, new Point(0, y), new Point(width, y));
        }

        // Pen das ondas holográficas
        var cyanPen = new Pen(new SolidColorBrush(Color.FromArgb(220, 0, 243, 255)), 1.8);
        var magentaPen = new Pen(new SolidColorBrush(Color.FromArgb(160, 255, 0, 85)), 1.2);
        var greenPen = new Pen(new SolidColorBrush(Color.FromArgb(140, 0, 255, 163)), 1.0);

        Point? prevCyan = null;
        Point? prevMagenta = null;
        Point? prevGreen = null;

        for (double x = 0; x <= width; x += 3)
        {
            var normX = x / width;
            var envelope = Math.Sin(normX * Math.PI);

            var yCyan = midY + Math.Sin(normX * 10.0 + _phase) * (height * 0.38) * envelope
                             + Math.Sin(normX * 22.0 - _phase * 1.4) * (height * 0.15) * envelope;

            var yMagenta = midY + Math.Sin(normX * 14.0 - _phase * 1.1) * (height * 0.28) * envelope;
            var yGreen = midY + Math.Sin(normX * 7.0 + _phase * 0.6) * (height * 0.20) * envelope;

            var ptCyan = new Point(x, yCyan);
            var ptMagenta = new Point(x, yMagenta);
            var ptGreen = new Point(x, yGreen);

            if (prevCyan.HasValue) context.DrawLine(cyanPen, prevCyan.Value, ptCyan);
            if (prevMagenta.HasValue) context.DrawLine(magentaPen, prevMagenta.Value, ptMagenta);
            if (prevGreen.HasValue) context.DrawLine(greenPen, prevGreen.Value, ptGreen);

            prevCyan = ptCyan;
            prevMagenta = ptMagenta;
            prevGreen = ptGreen;
        }
    }
}
