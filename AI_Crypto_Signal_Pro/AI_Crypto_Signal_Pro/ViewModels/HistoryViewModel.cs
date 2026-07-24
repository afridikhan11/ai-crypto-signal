// ========== ViewModels/HistoryViewModel.cs ==========
using System;
using System.Collections.ObjectModel;
using System.Linq;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using AI_Crypto_Signal_Pro.Models;

namespace AI_Crypto_Signal_Pro.ViewModels
{
    public partial class HistoryViewModel : ObservableObject
    {
        [ObservableProperty]
        private string _searchText = string.Empty;

        [ObservableProperty]
        private ObservableCollection<TradeHistoryItem> _trades = new();

        [ObservableProperty]
        private ObservableCollection<string> _dateFilters = new() { "All", "Today", "Last 7 days", "Last 30 days" };

        [ObservableProperty]
        private string _selectedDateFilter = "All";

        [ObservableProperty]
        private ObservableCollection<string> _statusFilters = new() { "All", "Open", "Closed" };

        [ObservableProperty]
        private string _selectedStatusFilter = "All";

        public HistoryViewModel()
        {
            LoadSampleData();
        }

        private void LoadSampleData()
        {
            Trades.Clear();
            Trades.Add(new TradeHistoryItem
            {
                Date = DateTime.Today.AddDays(-1),
                Coin = "BTCUSDT",
                Direction = "LONG",
                EntryPrice = 45200.00m,
                ExitPrice = 45800.00m,
                PnL = 600.00m,
                WinLoss = "WIN",
                WinLossColor = "#00E676",
                Status = "Closed"
            });
            Trades.Add(new TradeHistoryItem
            {
                Date = DateTime.Today.AddDays(-2),
                Coin = "ETHUSDT",
                Direction = "SHORT",
                EntryPrice = 3200.00m,
                ExitPrice = 3150.00m,
                PnL = 50.00m,
                WinLoss = "WIN",
                WinLossColor = "#00E676",
                Status = "Closed"
            });
            Trades.Add(new TradeHistoryItem
            {
                Date = DateTime.Today.AddDays(-3),
                Coin = "SOLUSDT",
                Direction = "LONG",
                EntryPrice = 100.00m,
                ExitPrice = 95.00m,
                PnL = -5.00m,
                WinLoss = "LOSS",
                WinLossColor = "#FF5252",
                Status = "Closed"
            });
        }

        [RelayCommand]
        private async Task ApplyFilter()
        {
            await Task.Delay(200);
        }

        [RelayCommand]
        private async Task Refresh()
        {
            await Task.Delay(200);
            LoadSampleData();
        }

        [RelayCommand]
        private async Task Export()
        {
            await Task.Delay(200);
        }
    }

    public class TradeHistoryItem
    {
        public DateTime Date { get; set; }
        public string Coin { get; set; } = string.Empty;
        public string Direction { get; set; } = string.Empty;
        public decimal EntryPrice { get; set; }
        public decimal ExitPrice { get; set; }
        public decimal PnL { get; set; }
        public string WinLoss { get; set; } = string.Empty;
        public string WinLossColor { get; set; } = "#FFFFFF";
        public string Status { get; set; } = string.Empty;
    }
}