using System;
using System.Collections.ObjectModel;
using System.Threading.Tasks;
using System.Windows;
using CommunityToolkit.Mvvm.ComponentModel;
using AI_Crypto_Signal_Pro.Models;
using AI_Crypto_Signal_Pro.Services;

namespace AI_Crypto_Signal_Pro.ViewModels;

public partial class LiveSignalsViewModel : ObservableObject
{
    private readonly ApiService _apiService = new();

    [ObservableProperty]
    private bool isLoading;

    [ObservableProperty]
    private string? errorMessage;

    public ObservableCollection<SignalModel> Signals { get; } = new();

    public LiveSignalsViewModel()
    {
        _ = LoadSignalsAsync();

        // Push-driven refresh: the backend broadcasts over /ws/signals whenever
        // a new signal is saved, so the list updates itself instead of only
        // refreshing when the user reopens this tab. Call Cleanup() when this
        // screen is navigated away from to unsubscribe.
        WebSocketService.Instance.SignalReceived += OnSignalReceived;
    }

    private void OnSignalReceived(object? sender, EventArgs e)
    {
        Application.Current?.Dispatcher.InvokeAsync(async () => await LoadSignalsAsync());
    }

    /// <summary>Call this when navigating away from Live Signals so the WebSocket subscription doesn't leak.</summary>
    public void Cleanup()
    {
        WebSocketService.Instance.SignalReceived -= OnSignalReceived;
    }

    public async Task LoadSignalsAsync()
    {
        if (IsLoading)
            return;

        try
        {
            IsLoading = true;
            ErrorMessage = null;

            var response =
                await _apiService.GetAsync<SignalListResponseDto>("signals");

            Signals.Clear();

            if (response?.Items == null)
                return;

            foreach (var s in response.Items)
            {
                Signals.Add(new SignalModel
                {
                    Id = s.Id,
                    CoinId = s.CoinId,
                    Coin = s.CoinSymbol,
                    Direction = s.Direction,
                    Entry = s.EntryPrice,
                    StopLoss = s.StopLoss,
                    TakeProfit = s.TakeProfit,
                    RiskReward = s.RiskReward,
                    Confidence = s.Confidence,
                    Reason = s.Reason,
                    Status = s.Status,
                    Timeframe = s.Timeframe,
                    AiModelVersion = s.AiModelVersion,
                    CreatedAt = s.CreatedAt,
                    UpdatedAt = s.UpdatedAt,
                    ClosedAt = s.ClosedAt,
                    SuggestedRiskUsd = s.SuggestedRiskUsd,
                    SuggestedQuantity = s.SuggestedQuantity,
                    SuggestedNotionalUsd = s.SuggestedNotionalUsd,
                    SuggestedProfitUsd = s.SuggestedProfitUsd,
                    SuggestedLossSlUsd = s.SuggestedLossSlUsd,
                    Executed = s.Executed,
                    ExecutedOrderId = s.ExecutedOrderId,
                    ExecutedAt = s.ExecutedAt,
                    ExecutedEnvironment = s.ExecutedEnvironment,
                    EntryType = s.EntryType,
                    EntryZoneTop = s.EntryZoneTop,
                    EntryZoneBottom = s.EntryZoneBottom,
                    EntryExpiresAt = s.EntryExpiresAt,
                    FilledAt = s.FilledAt,
                    ActualFillPrice = s.ActualFillPrice,
                    EntryOrderId = s.EntryOrderId
                });
            }
        }
        catch (Exception ex)
        {
            ErrorMessage = ex.Message;
        }
        finally
        {
            IsLoading = false;
        }
    }

    public Task RefreshAsync() => LoadSignalsAsync();
}
