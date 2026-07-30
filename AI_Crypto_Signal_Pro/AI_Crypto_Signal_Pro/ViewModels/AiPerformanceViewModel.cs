using System;
using System.Globalization;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using AI_Crypto_Signal_Pro.Models;
using AI_Crypto_Signal_Pro.Services;

namespace AI_Crypto_Signal_Pro.ViewModels
{
    /// <summary>
    /// AI Performance screen - read-only view over GET /performance/overview and
    /// GET /performance/journal (Phase 7, with Phase 8's optional date-range filtering
    /// on the overview). Every figure is a real count/average over already-stored
    /// signals - never a new scoring engine, never a fabricated statistic. A confidence
    /// bucket or breakdown row with zero trades shows "Not Available" for its win rate,
    /// never a fabricated 0%.
    /// </summary>
    public partial class AiPerformanceViewModel : ObservableObject
    {
        private const int JournalPageSize = 20;

        private readonly ApiService _apiService = new();

        // Separate re-entrancy guard from the shared IsLoading flag: the constructor
        // fires RefreshOverview() and LoadJournalPage() concurrently (fire-and-forget),
        // and RefreshOverview() sets IsLoading = true synchronously before its first
        // await. Guarding LoadJournalPage() on the same IsLoading flag meant it saw
        // IsLoading already true and returned immediately - the Trade Journal tab
        // never loaded on startup. This flag guards only the journal fetch.
        private bool _isLoadingJournal;

        [ObservableProperty]
        private bool isLoading;

        [ObservableProperty]
        private string? errorMessage;

        [ObservableProperty]
        private string? statusMessage;

        [ObservableProperty]
        private AiPerformanceOverviewDto? overview;

        [ObservableProperty]
        private DateTime? filterStartDate;

        [ObservableProperty]
        private DateTime? filterEndDate;

        [ObservableProperty]
        private TradeJournalResponseDto? journal;

        [ObservableProperty]
        private int journalPage = 1;

        public int JournalTotalPages =>
            Journal is null || Journal.PageSize <= 0
                ? 1
                : Math.Max(1, (int)Math.Ceiling(Journal.Total / (double)Journal.PageSize));

        public AiPerformanceViewModel()
        {
            _ = RefreshOverview();
            _ = LoadJournalPage();
        }

        [RelayCommand]
        private async Task RefreshOverview()
        {
            if (IsLoading)
                return;

            IsLoading = true;
            ErrorMessage = null;

            try
            {
                var endpoint = "performance/overview";
                var query = string.Empty;
                if (FilterStartDate is not null)
                    query += $"start_date={Uri.EscapeDataString(FilterStartDate.Value.ToString("o", CultureInfo.InvariantCulture))}";
                if (FilterEndDate is not null)
                    query += (query.Length > 0 ? "&" : string.Empty) +
                              $"end_date={Uri.EscapeDataString(FilterEndDate.Value.ToString("o", CultureInfo.InvariantCulture))}";
                if (query.Length > 0)
                    endpoint += "?" + query;

                var result = await _apiService.GetAsync<AiPerformanceOverviewDto>(endpoint);
                if (result is null)
                    return;

                Overview = result;
                StatusMessage = $"Refreshed ({DateTime.Now:HH:mm:ss}).";
            }
            catch (Exception ex)
            {
                ErrorMessage = $"Could not load AI Performance overview: {ex.Message}";
            }
            finally
            {
                IsLoading = false;
            }
        }

        [RelayCommand]
        private async Task ClearDateFilter()
        {
            FilterStartDate = null;
            FilterEndDate = null;
            await RefreshOverview();
        }

        [RelayCommand]
        private async Task LoadJournalPage()
        {
            if (_isLoadingJournal)
                return;

            _isLoadingJournal = true;
            IsLoading = true;
            ErrorMessage = null;

            try
            {
                var result = await _apiService.GetAsync<TradeJournalResponseDto>(
                    $"performance/journal?page={JournalPage}&page_size={JournalPageSize}&sort_by=closed_at&sort_order=desc");
                if (result is null)
                    return;

                Journal = result;
                OnPropertyChanged(nameof(JournalTotalPages));
            }
            catch (Exception ex)
            {
                ErrorMessage = $"Could not load trade journal: {ex.Message}";
            }
            finally
            {
                IsLoading = false;
                _isLoadingJournal = false;
            }
        }

        [RelayCommand]
        private async Task NextJournalPage()
        {
            if (JournalPage >= JournalTotalPages)
                return;
            JournalPage++;
            await LoadJournalPage();
        }

        [RelayCommand]
        private async Task PreviousJournalPage()
        {
            if (JournalPage <= 1)
                return;
            JournalPage--;
            await LoadJournalPage();
        }
    }
}
