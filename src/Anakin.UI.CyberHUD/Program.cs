using System;
using Avalonia;
using Serilog;

namespace Anakin.UI.CyberHUD;

public static class Program
{
    [STAThread]
    public static int Main(string[] args)
    {
        Log.Logger = new LoggerConfiguration()
            .MinimumLevel.Information()
            .Enrich.FromLogContext()
            .Enrich.WithProperty("Application", "ANAKIN-CyberHUD")
            .WriteTo.Console(
                outputTemplate: "[{Timestamp:HH:mm:ss} {Level:u3}] [{Application}] {Message:lj}{NewLine}{Exception}",
                formatProvider: System.Globalization.CultureInfo.InvariantCulture)
            .CreateLogger();

        try
        {
            Log.Information("Iniciando Cockpit Tático A.N.A.K.I.N CyberHUD...");
            BuildAvaloniaApp().StartWithClassicDesktopLifetime(args);
            return 0;
        }
        catch (Exception ex)
        {
            Log.Fatal(ex, "Falha crítica na inicialização do CyberHUD");
            return 1;
        }
        finally
        {
            Log.CloseAndFlush();
        }
    }

    public static AppBuilder BuildAvaloniaApp()
        => AppBuilder.Configure<App>()
            .UsePlatformDetect()
            .WithInterFont()
            .LogToTrace();
}
