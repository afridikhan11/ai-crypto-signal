// ========== ViewModels/HistoryViewModel.cs ==========
using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using AI_Crypto_Signal_Pro.Models;
using AI_Crypto_Signal_Pro.Services;

namespace AI_Crypto_Signal_Pro.ViewModels
{
    public partial class HistoryViewModel : ObservableObject
    {
        private readonly ApiService _apiService = new();

        // Full, unfiltered set of closed signals fetched from the backend.
        private List<TradeHistoryItem> _allTrades = new();

        [ObservableProperty]
        private string _searchText = string.Empty;

        [ObservableProperty]
        private ObservableCollection<TradeHistoryItem> _trades = new();

        [ObservableProperty]
        private ObservableCollection<string> _dateFilters = new() { "All", "Today", "Last 7 days", "Last 30 days" };

        [ObservableProperty]
        private string _selectedDateFilter = "All";

        [ObservableProperty]
        private ObservableCollection<string> _statusFilters = new() { "All", "Win", "Loss", "Cancelled" };

        [ObservableProperty]
        private string _selectedStatusFilter = "All";

        [ObservableProperty]
        private bool _isLoading;

        [ObservableProperty]
        private string? _errorMessage;

        public HistoryViewModel()
        {
            _ = LoadDataAsync();
        }

        private async Task LoadDataAsync()
        {
            if (IsLoading)
                return;

            IsLoading = true;
            ErrorMessage = null;

            try
            {
                var response = await _apiService.GetAsync<HistoryListResponseDto>(
                    "history?page=1&page_size=100&sort_by=closed_at&sort_order=desc");

                _allTrades = (response?.Items ?? new List<HistoryItemDto>())
                    .Select(ToTradeHistoryItem)
                    .ToList();

                ApplyFilterInternal();
            }
            catch (Exception ex)
            {
                ErrorMessage = ex.Message;
                _allTrades = new List<TradeHistoryItem>();
                Trades = new ObservableCollection<TradeHistoryItem>();
            }
            finally
            {
                IsLoading = false;
            }
        }

        private static TradeHistoryItem ToTradeHistoryItem(HistoryItemDto dto)
        {
            var (label, color) = ResultDisplay(dto.Result);

            return new TradeHistoryItem
            {
                Date = dto.ClosedAt ?? dto.CreatedAt,
                Coin = dto.CoinSymbol,
                Direction = dto.Direction,
                EntryPrice = dto.EntryPrice,
                StopLoss = dto.StopLoss,
                TakeProfit = dto.TakeProfit,
                Confidence = dto.Confidence,
                Result = dto.Result,
                ResultLabel = label,
                ResultColor = color,
            };
        }

        private static (string Label, string Color) ResultDisplay(string result) => result switch
        {
            "TP_HIT" => ("TP Hit", "#00E676"),
            "STOPPED" => ("Stopped", "#FF5252"),
            "CANCELLED" => ("Cancelled", "#9E9E9E"),
            _ => (result, "#FFFFFF"),
        };

        [RelayCommand]
        private Task ApplyFilter()
        {
            ApplyFilterInternal();
            return Task.CompletedTask;
        }

        private void ApplyFilterInternal()
        {
            IEnumerable<TradeHistoryItem> filtered = _allTrades;

            if (!string.IsNullOrWhiteSpace(SearchText))
            {
                filtered = filtered.Where(t =>
                    t.Coin.Contains(SearchText, StringComparison.OrdinalIgnoreCase));
            }

            filtered = SelectedStatusFilter switch
            {
                "Win" => filtered.Where(t => t.Result == "TP_HIT"),
                "Loss" => filtered.Where(t => t.Result == "STOPPED"),
                "Cancelled" => filtered.Where(t => t.Result == "CANCELLED"),
                _ => filtered,
            };

            var cutoff = SelectedDateFilter switch
            {
                "Today" => DateTime.Today,
                "Last 7 days" => DateTime.Today.AddDays(-7),
                "Last 30 days" => DateTime.Today.AddDays(-30),
                _ => (DateTime?)null,
            };

            if (cutoff.HasValue)
            {
                filtered = filtered.Where(t => t.Date >= cutoff.Value);
            }

            Trades = new ObservableCollection<TradeHistoryItem>(filtered.OrderByDescending(t => t.Date));
        }

        [RelayCommand]
        private async Task Refresh()
        {
            await LoadDataAsync();
        }

        [RelayCommand]
        private async Task Export()
        {
            // Full CSV export is served directly by the backend at GET /api/v1/history/export.
            await Task.CompletedTask;
        }
    }

    public class TradeHistoryItem
    {
        public DateTime Date { get; set; }
        public string Coin { get; set; } = string.Empty;
        public string Direction { get; set; } = string.Empty;
        public decimal EntryPrice { get; set; }
        public decimal StopLoss { get; set; }
        public decimal TakeProfit { get; set; }
        public int Confidence { get; set; }

        /// <summary>Raw backend status value (e.g. TP_HIT, STOPPED, CANCELLED).</summary>
        public string Result { get; set; } = string.Empty;

        /// <summary>Human-readable label for display.</summary>
        public string ResultLabel { get; set; } = string.Empty;
        public string ResultColor { get; set; } = "#FFFFFF";
    }
}
