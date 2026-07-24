using System;
using System.Collections.ObjectModel;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using AI_Crypto_Signal_Pro.Models;
using AI_Crypto_Signal_Pro.Services;

namespace AI_Crypto_Signal_Pro.ViewModels;

public partial class DashboardViewModel : ObservableObject
{
    private readonly ApiService _apiService = new();

    [ObservableProperty]
    private int totalSignals;

    [ObservableProperty]
    private double winRate;

    [ObservableProperty]
    private int activeSignals;

    [ObservableProperty]
    private double avgConfidence;

    [ObservableProperty]
    private ObservableCollection<SignalModel> recentSignals = new();

    [ObservableProperty]
    private bool isLoading;

    public DashboardViewModel()
    {
        _ = LoadDataAsync();
    }

    [RelayCommand]
    public async Task LoadDataAsync()
    {
        if (IsLoading)
            return;

        IsLoading = true;

        try
        {
            var statsTask = _apiService.GetAsync<StatsDto>("stats");
            var signalsTask = _apiService.GetAsync<SignalListResponseDto>("signals");

            await Task.WhenAll(statsTask, signalsTask);

            var stats = await statsTask;
            var signalResponse = await signalsTask;

            if (stats != null)
            {
                TotalSignals = stats.TotalSignals;
                ActiveSignals = stats.ActiveSignals;
                WinRate = stats.WinRate;
                AvgConfidence = stats.AvgConfidence;
            }

            RecentSignals = new ObservableCollection<SignalModel>();

            if (signalResponse?.Items != null)
            {
                foreach (var dto in signalResponse.Items)
                {
                    RecentSignals.Add(new SignalModel
                    {
                        Coin = dto.CoinSymbol,
                        Direction = dto.Direction,
                        Entry = dto.EntryPrice,
                        StopLoss = dto.StopLoss,
                        TakeProfit = dto.TakeProfit1,
                        Confidence = dto.Confidence,
                        Status = dto.Status
                    });
                }
            }
        }
        catch (Exception ex)
        {
            System.Diagnostics.Debug.WriteLine(ex);

            TotalSignals = 0;
            ActiveSignals = 0;
            WinRate = 0;
            AvgConfidence = 0;

            RecentSignals = new ObservableCollection<SignalModel>();
        }
        finally
        {
            IsLoading = false;
        }
    }
}