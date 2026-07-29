using System;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using AI_Crypto_Signal_Pro.Models;
using AI_Crypto_Signal_Pro.Services;

namespace AI_Crypto_Signal_Pro.ViewModels
{
    /// <summary>
    /// Portfolio Intelligence screen - read-only view over GET /portfolio/intelligence
    /// (Phase 7). Composes the existing Phase 1 portfolio risk/daily-loss summaries with
    /// exposure, correlation, and diversification analysis - all reused/derived from
    /// real account and signal data, never a second scoring engine (see
    /// app/services/portfolio_intelligence.py's module docstring). Every section here
    /// shows the backend's own honest "not available" message when it has one (e.g. no
    /// linked Binance account, fewer than 2 symbols held) rather than inventing a number.
    /// </summary>
    public partial class PortfolioIntelligenceViewModel : ObservableObject
    {
        private readonly ApiService _apiService = new();

        [ObservableProperty]
        private bool isLoading;

        [ObservableProperty]
        private string? errorMessage;

        [ObservableProperty]
        private string? statusMessage;

        [ObservableProperty]
        private PortfolioIntelligenceDto? intelligence;

        public PortfolioIntelligenceViewModel()
        {
            _ = Refresh();
        }

        [RelayCommand]
        private async Task Refresh()
        {
            if (IsLoading)
                return;

            IsLoading = true;
            ErrorMessage = null;

            try
            {
                var result = await _apiService.GetAsync<PortfolioIntelligenceDto>("portfolio/intelligence");
                if (result is null)
                    return;

                Intelligence = result;
                StatusMessage = $"Refreshed ({DateTime.Now:HH:mm:ss}).";
            }
            catch (Exception ex)
            {
                ErrorMessage = $"Could not load Portfolio Intelligence: {ex.Message}";
            }
            finally
            {
                IsLoading = false;
            }
        }
    }
}
