using System.Windows;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;

namespace AI_Crypto_Signal_Pro;

public partial class App : Application
{
    public static IHost? Host { get; private set; }

    public App()
    {
        Host = Microsoft.Extensions.Hosting.Host
            .CreateDefaultBuilder()
            .ConfigureServices((context, services) =>
            {
                // Services اور ViewModels یہاں Register ہوں گے
            })
            .Build();
    }

    protected override async void OnStartup(StartupEventArgs e)
    {
        await Host!.StartAsync();

        base.OnStartup(e);
    }

    protected override async void OnExit(ExitEventArgs e)
    {
        await Host!.StopAsync();

        Host.Dispose();

        base.OnExit(e);
    }
}