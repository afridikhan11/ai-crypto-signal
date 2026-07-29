using System;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Threading;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using AI_Crypto_Signal_Pro.Models;
using AI_Crypto_Signal_Pro.Services;

namespace AI_Crypto_Signal_Pro.ViewModels;

/// <summary>
/// Backs MainWindow's status bar with real state instead of the previous
/// hardcoded "Backend Online / DB Connected / WebSocket Active" text:
///   - Backend/DB status is polled from GET /api/v1/health every 15s.
///   - WebSocket status reflects the real WebSocketService connection.
/// </summary>
public partial class MainWindowViewModel : ObservableObject
{
    private static readonly string ColorOnline = "#00E676";
    private static readonly string ColorOffline = "#FF5252";
    private static readonly string ColorUnknown = "#9E9E9E";

    private readonly ApiService _apiService = new();
    private readonly DispatcherTimer _healthTimer;

    [ObservableProperty]
    private string currentPageTitle = "Dashboard";

    [ObservableProperty]
    private string backendStatusText = "Checking...";

    [ObservableProperty]
    private string backendStatusColor = ColorUnknown;

    [ObservableProperty]
    private string dbStatusText = "Checking...";

    [ObservableProperty]
    private string dbStatusColor = ColorUnknown;

    [ObservableProperty]
    private string webSocketStatusText = "Connecting...";

    [ObservableProperty]
    private string webSocketStatusColor = ColorUnknown;

    [ObservableProperty]
    private string binanceStatusText = "Checking...";

    [ObservableProperty]
    private string binanceStatusColor = ColorUnknown;

    public MainWindowViewModel()
    {
        WebSocketService.Instance.ConnectionStateChanged += OnWebSocketConnectionStateChanged;

        _healthTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(15) };
        _healthTimer.Tick += async (_, _) => await CheckHealthAsync();
        _healthTimer.Start();

        _ = CheckHealthAsync();
    }

    private void OnWebSocketConnectionStateChanged(object? sender, bool connected)
    {
        Application.Current?.Dispatcher.Invoke(() =>
        {
            WebSocketStatusText = connected ? "Live Feed Active" : "Reconnecting...";
            WebSocketStatusColor = connected ? ColorOnline : ColorOffline;
        });
    }

    private async Task CheckHealthAsync()
    {
        try
        {
            var health = await _apiService.GetAsync<HealthDto>("health");

            BackendStatusText = health != null ? "Backend Online" : "Backend Unreachable";
            BackendStatusColor = health != null ? ColorOnline : ColorOffline;

            DbStatusText = health?.Database == "ok" ? "DB Connected" : "DB Unavailable";
            DbStatusColor = health?.Database == "ok" ? ColorOnline : ColorOffline;

            BinanceStatusText = health?.Binance switch
            {
                "ok" => "Binance Connected",
                "error" => "Binance Disconnected",
                _ => "Binance Status Unknown",
            };
            BinanceStatusColor = health?.Binance == "ok" ? ColorOnline
                : health?.Binance == "error" ? ColorOffline
                : ColorUnknown;
        }
        catch
        {
            BackendStatusText = "Backend Unreachable";
            BackendStatusColor = ColorOffline;
            DbStatusText = "DB Unavailable";
            DbStatusColor = ColorOffline;
            BinanceStatusText = "Binance Status Unknown";
            BinanceStatusColor = ColorUnknown;
        }
    }

    [RelayCommand]
    private void ShowDashboard()
    {
        CurrentPageTitle = "Dashboard";
    }

    [RelayCommand]
    private void ShowLiveSignals()
    {
        CurrentPageTitle = "Live Signals";
    }

    [RelayCommand]
    private void ShowMarket()
    {
        CurrentPageTitle = "Market";
    }

    [RelayCommand]
    private void ShowHistory()
    {
        CurrentPageTitle = "History";
    }

    [RelayCommand]
    private void ShowSettings()
    {
        CurrentPageTitle = "Settings";
    }
}
