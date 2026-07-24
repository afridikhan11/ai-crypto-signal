// ========== ViewModels/MarketViewModel.cs ==========
using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;

namespace AI_Crypto_Signal_Pro.ViewModels
{
    public partial class MarketViewModel : ObservableObject
    {
        [ObservableProperty]
        private ObservableCollection<MarketItem> _topGainers = new();

        [ObservableProperty]
        private ObservableCollection<MarketItem> _topLosers = new();

        [ObservableProperty]
        private ObservableCollection<MarketItem> _trendingCoins = new();

        public MarketViewModel()
        {
            LoadSampleData();
        }

        private void LoadSampleData()
        {
            TopGainers.Clear();
            TopLosers.Clear();
            TrendingCoins.Clear();

            TopGainers.Add(new MarketItem { Coin = "SOLUSDT", Price = 145.20m, Change24h = 12.5m, Volume = 1500000 });
            TopGainers.Add(new MarketItem { Coin = "AVAXUSDT", Price = 42.80m, Change24h = 9.3m, Volume = 980000 });
            TopGainers.Add(new MarketItem { Coin = "LINKUSDT", Price = 18.60m, Change24h = 7.1m, Volume = 750000 });

            TopLosers.Add(new MarketItem { Coin = "ADAUSDT", Price = 0.52m, Change24h = -5.4m, Volume = 2200000 });
            TopLosers.Add(new MarketItem { Coin = "DOGEUSDT", Price = 0.12m, Change24h = -4.8m, Volume = 1800000 });
            TopLosers.Add(new MarketItem { Coin = "MATICUSDT", Price = 0.98m, Change24h = -3.7m, Volume = 1200000 });

            TrendingCoins.Add(new MarketItem { Coin = "BTCUSDT", Price = 65000m, Change24h = 2.1m, Volume = 3200000 });
            TrendingCoins.Add(new MarketItem { Coin = "ETHUSDT", Price = 3200m, Change24h = 1.8m, Volume = 2500000 });
            TrendingCoins.Add(new MarketItem { Coin = "XRPUSDT", Price = 0.68m, Change24h = 3.2m, Volume = 3000000 });
        }
    }

    public class MarketItem
    {
        public string Coin { get; set; } = string.Empty;
        public decimal Price { get; set; }
        public decimal Change24h { get; set; }
        public decimal Volume { get; set; }
    }
}