using System;
using System.Collections.ObjectModel;
using System.Threading.Tasks;
using System.Windows;
using CommunityToolkit.Mvvm.ComponentModel;
using AI_Crypto_Signal_Pro.Models;
using AI_Crypto_Signal_Pro.Services;

namespace AI_Crypto_Signal_Pro.ViewModels;

/// <summary>
/// Same shape as LiveSignalsViewModel, but scoped to Binance's commodity
/// futures only (XAUUSDT/XAGUSDT/CLUSDT/BZUSDT - Gold, Silver, WTI Crude,
/// Brent). The backend tags each Coin's asset_class at creation time (see
/// app.core.constants.asset_class_for_symbol), so filtering here is just
/// one query param - the SMC scanner/scorer pipeline itself is identical
/// to crypto, no separate backend concept to duplicate.
/// </summary>
public partial class GoldSignalsViewModel : ObservableObject
{
    private readonly ApiService _apiService = new();

    [ObservableProperty]
    private bool isLoading;

    [ObservableProperty]
    private string? errorMessage;

    public ObservableCollection<SignalModel> Signals { get; } = new();

    public GoldSignalsViewModel()
    {
        _ = LoadSignalsAsync();

        // Same push-driven refresh as Live Signals: the backend broadcasts on
        // every new/closed signal regardless of asset class, so just reload
        // this (already filtered) list on any push.
        WebSocketService.Instance.SignalReceived += OnSignalReceived;
    }

    private void OnSignalReceived(object? sender, EventArgs e)
    {
        Application.Current?.Dispatcher.InvokeAsync(async () => await LoadSignalsAsync());
    }

    /// <summary>Call this when navigating away so the WebSocket subscription doesn't leak.</summary>
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
                await _apiService.GetAsync<SignalListResponseDto>("signals?asset_class=commodity&page_size=100");

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
