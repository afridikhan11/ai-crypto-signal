using System;
using System.Collections.ObjectModel;
using System.Threading.Tasks;
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
    }

    public async Task LoadSignalsAsync()
    {
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
                    TakeProfit = s.TakeProfit1,
                    TakeProfit2 = s.TakeProfit2,
                    TakeProfit3 = s.TakeProfit3,
                    RiskReward = s.RiskReward,
                    Confidence = s.Confidence,
                    Reason = s.Reason,
                    Status = s.Status,
                    Timeframe = s.Timeframe,
                    AiModelVersion = s.AiModelVersion,
                    Session = s.Session,
                    HtfBias = s.HtfBias,
                    BiasStrength = s.BiasStrength,
                    MaxTpHit = s.MaxTpHit,
                    CreatedAt = s.CreatedAt,
                    UpdatedAt = s.UpdatedAt,
                    ClosedAt = s.ClosedAt
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
