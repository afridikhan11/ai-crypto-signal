using System;
using System.Collections.ObjectModel;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Threading;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using AI_Crypto_Signal_Pro.Models;
using AI_Crypto_Signal_Pro.Services;

namespace AI_Crypto_Signal_Pro.ViewModels
{
    /// <summary>
    /// Auto Trading tab: the ONE screen in this app that can place a real
    /// order. Balance/positions come from the exact same read-only
    /// GET /account/snapshot the Account tab already uses (no separate data
    /// source - see AccountViewModel). The only new capability here is the
    /// per-signal Execute button, which calls POST /trading/execute/{id} -
    /// that backend endpoint is the single place in the whole system that
    /// sends a signed order-placing request to Binance, and it only ever
    /// fires from this explicit user click.
    /// </summary>
    public partial class AutoTradingViewModel : ObservableObject
    {
        private static readonly TimeSpan AutoRefreshInterval = TimeSpan.FromSeconds(15);

        private readonly ApiService _apiService = new();
        private readonly DispatcherTimer _autoRefreshTimer;

        [ObservableProperty]
        private bool isLoading;

        [ObservableProperty]
        private string? statusMessage;

        [ObservableProperty]
        private string? errorMessage;

        [ObservableProperty]
        private bool hasCredentials;

        [ObservableProperty]
        private string environment = "";

        [ObservableProperty]
        private AccountInfoDto? account;

        [ObservableProperty]
        private decimal? totalWalletValueUsdt;

        [ObservableProperty]
        private FuturesAccountInfoDto? futures;

        public ObservableCollection<FuturesPositionDto> OpenPositions { get; } = new();
        public ObservableCollection<SignalModel> ExecutableSignals { get; } = new();
        public ObservableCollection<OpenOrderInfoDto> OpenOrders { get; } = new();
        public ObservableCollection<OrderHistoryItemDto> OrderHistory { get; } = new();
        public ObservableCollection<TradeRecordDto> TradeHistory { get; } = new();
        public ObservableCollection<IncomeHistoryItemDto> IncomeHistory { get; } = new();
        public ObservableCollection<AssetBalanceDto> Balances { get; } = new();

        public AutoTradingViewModel()
        {
            _autoRefreshTimer = new DispatcherTimer { Interval = AutoRefreshInterval };
            _autoRefreshTimer.Tick += (_, _) => _ = Refresh();
            _autoRefreshTimer.Start();

            _ = InitializeAsync();
        }

        /// <summary>Call this when navigating away from Auto Trading so background polling stops.</summary>
        public void Cleanup()
        {
            _autoRefreshTimer.Stop();
        }

        private async Task InitializeAsync()
        {
            try
            {
                var status = await _apiService.GetAsync<CredentialsStatusDto>("account/credentials/status");
                HasCredentials = status?.HasCredentials ?? false;
                Environment = status?.Environment ?? "";

                if (HasCredentials)
                {
                    await Refresh();
                }
                else
                {
                    StatusMessage = "No Binance API credentials saved yet. Add a trade-enabled key in Settings first.";
                }
            }
            catch (Exception ex)
            {
                ErrorMessage = ex.Message;
            }
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
                // Same endpoint, same data, as the Account tab - Auto Trading
                // never fetches balance/positions through a separate path.
                var snapshot = await _apiService.GetAsync<AccountSnapshotDto>("account/snapshot");
                if (snapshot != null)
                {
                    Environment = snapshot.Environment;
                    Account = snapshot.Account;
                    TotalWalletValueUsdt = snapshot.TotalWalletValueUsdt;
                    Futures = snapshot.Futures;

                    OpenPositions.Clear();
                    foreach (var p in snapshot.Futures?.OpenPositions ?? new())
                        OpenPositions.Add(p);

                    OpenOrders.Clear();
                    foreach (var o in snapshot.OpenOrders)
                        OpenOrders.Add(o);

                    TradeHistory.Clear();
                    foreach (var t in snapshot.TradeHistory)
                        TradeHistory.Add(t);

                    Balances.Clear();
                    foreach (var b in snapshot.Balances)
                        Balances.Add(b);
                }

                // Order History / Transaction History aren't part of the shared
                // snapshot (separate, heavier endpoints - see BinanceAccountService) -
                // fetched here specifically for the tab strip's own two panels.
                try
                {
                    var orderHistory = await _apiService.GetAsync<OrderHistoryResponseDto>("account/order-history?limit=50");
                    OrderHistory.Clear();
                    foreach (var o in orderHistory?.Items ?? new())
                        OrderHistory.Add(o);
                }
                catch (Exception ex)
                {
                    // Non-fatal - the rest of the tab (positions, balances, etc.) should still work.
                    ErrorMessage ??= $"Order history unavailable: {ex.Message}";
                }

                try
                {
                    var incomeHistory = await _apiService.GetAsync<IncomeHistoryResponseDto>("account/income-history?limit=50");
                    IncomeHistory.Clear();
                    foreach (var i in incomeHistory?.Items ?? new())
                        IncomeHistory.Add(i);
                }
                catch (Exception ex)
                {
                    ErrorMessage ??= $"Transaction history unavailable: {ex.Message}";
                }

                var signalsResponse =
                    await _apiService.GetAsync<SignalListResponseDto>("signals?status=ACTIVE&page_size=100");

                ExecutableSignals.Clear();
                if (signalsResponse?.Items != null)
                {
                    foreach (var s in signalsResponse.Items)
                    {
                        ExecutableSignals.Add(new SignalModel
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
                            CreatedAt = s.CreatedAt,
                            SuggestedQuantity = s.SuggestedQuantity,
                            SuggestedNotionalUsd = s.SuggestedNotionalUsd,
                            SuggestedProfitUsd = s.SuggestedProfitUsd,
                            SuggestedLossSlUsd = s.SuggestedLossSlUsd,
                            Executed = s.Executed,
                            ExecutedOrderId = s.ExecutedOrderId,
                            ExecutedAt = s.ExecutedAt,
                            ExecutedEnvironment = s.ExecutedEnvironment,
                        });
                    }
                }

                StatusMessage = $"Refreshed ({DateTime.Now:HH:mm:ss}).";
            }
            catch (Exception ex)
            {
                ErrorMessage = $"Could not load Auto Trading data: {ex.Message}";
            }
            finally
            {
                IsLoading = false;
            }
        }

        [RelayCommand]
        private async Task Execute(SignalModel? signal)
        {
            if (signal is null || signal.Executed || IsLoading)
                return;

            var envLabel = Environment == "testnet" ? "TESTNET (no real funds)" : "MAINNET (REAL FUNDS)";
            var confirm = MessageBox.Show(
                $"Execute {signal.Direction} {signal.Coin} on {envLabel}?\n\n" +
                $"Entry: {signal.Entry}\nStop Loss: {signal.StopLoss}\nTake Profit (TP1): {signal.TakeProfit}\n" +
                $"Suggested quantity: {signal.PositionSizeDisplay}\n\n" +
                "This places a real MARKET order plus stop-loss/take-profit on Binance right now.",
                "Confirm Execute",
                MessageBoxButton.YesNo,
                Environment == "testnet" ? MessageBoxImage.Question : MessageBoxImage.Warning);

            if (confirm != MessageBoxResult.Yes)
                return;

            IsLoading = true;
            ErrorMessage = null;
            StatusMessage = null;

            try
            {
                var result = await _apiService.PostAsync<EmptyRequestDto, ExecuteSignalResponseDto>(
                    $"trading/execute/{signal.Id}", new EmptyRequestDto());

                if (result != null)
                {
                    signal.Executed = true;
                    signal.ExecutedOrderId = result.EntryOrder.OrderId.ToString();
                    signal.ExecutedEnvironment = result.Environment;

                    var warningText = result.Warnings.Count > 0 ? " Warnings: " + string.Join(" ", result.Warnings) : "";
                    StatusMessage = $"Executed {signal.Coin} on {result.Environment} - order #{result.EntryOrder.OrderId}.{warningText}";
                }

                await Refresh();
            }
            catch (Exception ex)
            {
                ErrorMessage = $"Execution failed: {ex.Message}";
            }
            finally
            {
                IsLoading = false;
            }
        }

        [RelayCommand]
        private async Task ClosePosition(FuturesPositionDto? position)
        {
            if (position is null || IsLoading)
                return;

            var envLabel = Environment == "testnet" ? "TESTNET (no real funds)" : "MAINNET (REAL FUNDS)";
            var confirm = MessageBox.Show(
                $"Close the entire {position.Symbol} position on {envLabel}?\n\n" +
                $"Size: {position.PositionAmt}\nEntry: {position.EntryPrice}\nUnrealized PnL: {position.UnrealizedPnl}\n\n" +
                "This places a real MARKET order to fully close this position right now.",
                "Confirm Close Position",
                MessageBoxButton.YesNo,
                Environment == "testnet" ? MessageBoxImage.Question : MessageBoxImage.Warning);

            if (confirm != MessageBoxResult.Yes)
                return;

            IsLoading = true;
            ErrorMessage = null;
            StatusMessage = null;

            try
            {
                var result = await _apiService.PostAsync<EmptyRequestDto, ClosePositionResponseDto>(
                    $"trading/close-position/{position.Symbol}", new EmptyRequestDto());

                if (result != null)
                {
                    StatusMessage = $"Closed {position.Symbol} on {result.Environment} - order #{result.Order.OrderId}.";
                }

                await Refresh();
            }
            catch (Exception ex)
            {
                ErrorMessage = $"Close position failed: {ex.Message}";
            }
            finally
            {
                IsLoading = false;
            }
        }
    }
}
