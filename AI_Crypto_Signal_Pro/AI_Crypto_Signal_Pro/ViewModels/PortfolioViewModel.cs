// ========== ViewModels/PortfolioViewModel.cs ==========
using CommunityToolkit.Mvvm.ComponentModel;

namespace AI_Crypto_Signal_Pro.ViewModels
{
    public partial class PortfolioViewModel : ObservableObject
    {
        [ObservableProperty]
        private decimal _totalBalance = 48320.50m;

        [ObservableProperty]
        private decimal _todayPnl = 450.20m;

        [ObservableProperty]
        private decimal _weeklyPnl = 1200.00m;

        [ObservableProperty]
        private decimal _monthlyPnl = 3850.00m;

        [ObservableProperty]
        private int _openPositions = 9;

        [ObservableProperty]
        private double _riskPercentage = 2.5;

        public PortfolioViewModel()
        {
        }
    }
}