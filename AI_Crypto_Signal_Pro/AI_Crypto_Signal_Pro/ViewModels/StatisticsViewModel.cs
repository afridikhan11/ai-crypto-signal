using System;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using AI_Crypto_Signal_Pro.Models;
using AI_Crypto_Signal_Pro.Services;

namespace AI_Crypto_Signal_Pro.ViewModels
{
    public partial class StatisticsViewModel : ObservableObject
    {
        private readonly ApiService _apiService = new();

        [ObservableProperty]
        private int totalSignals;

        [ObservableProperty]
        private int activeSignals;

        [ObservableProperty]
        private int closedSignals;

        [ObservableProperty]
        private double winRate;

        [ObservableProperty]
        private double avgConfidence;

        [ObservableProperty]
        private double avgRiskReward;

        [ObservableProperty]
        private string bestPerformingSymbol = "Loading...";

        [ObservableProperty]
        private bool isLoading;

        public StatisticsViewModel()
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
                var stats = await _apiService.GetAsync<StatsDto>("stats");

                if (stats != null)
                {
                    TotalSignals = stats.TotalSignals;
                    ActiveSignals = stats.ActiveSignals;
                    ClosedSignals = stats.ClosedSignals;
                    WinRate = stats.WinRate;
                    AvgConfidence = stats.AvgConfidence;
                    AvgRiskReward = stats.AvgRiskReward;

                    BestPerformingSymbol =
                        string.IsNullOrWhiteSpace(stats.BestPerformingSymbol)
                            ? "No Closed Trades Yet"
                            : stats.BestPerformingSymbol;
                }
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine(ex);

                BestPerformingSymbol = "Backend Offline";
            }
            finally
            {
                IsLoading = false;
            }
        }
    }
}