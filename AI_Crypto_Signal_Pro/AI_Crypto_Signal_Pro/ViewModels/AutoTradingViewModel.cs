using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
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
    /// source - see AccountViewModel). The per-signal Execute button calls
    /// POST /trading/execute/{id} - that backend endpoint is the single
    /// place in the whole system that sends a signed order-placing request
    /// to Binance, and it only ever fires from this explicit user click.
    ///
    /// AUTO TRADING CONTROL PANEL (2026-07-30): everything below the
    /// account/signals section - ON/OFF, Start/Pause/Stop/Resume, Emergency
    /// Kill Switch, Close All Positions, Cancel Pending Orders, status
    /// cards, risk limits, and Trading Logs - is a pure control/visualization
    /// layer. Every command here is a single call to a real backend endpoint
    /// in app/api/v1/endpoints/trading_control.py; no trading decision, no
    /// bulk-close/cancel loop, and no ICT/AI/Scanner logic is implemented in
    /// this file - see LIVE_EXECUTION_SAFETY_REPORT.md's sibling report for
    /// this feature for the exact backend endpoints each command below
    /// calls.
    /// </summary>
    public partial class AutoTradingViewModel : ObservableObject
    {
        private static readonly TimeSpan AutoRefreshInterval = TimeSpan.FromSeconds(15);
        private static readonly TimeSpan StatusPollInterval = TimeSpan.FromSeconds(10);
        private static readonly TimeSpan LogPollInterval = TimeSpan.FromSeconds(5);

        private readonly ApiService _apiService = new();
        private readonly DispatcherTimer _autoRefreshTimer;
        private readonly DispatcherTimer _statusPollTimer;
        private readonly DispatcherTimer _logPollTimer;

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

        // ---------------- Control Panel state (2026-07-30) ----------------

        [ObservableProperty]
        private TradingStatusDto tradingStatus = new();

        [ObservableProperty]
        private bool isControlActionRunning;

        [ObservableProperty]
        private string? controlStatusMessage;

        [ObservableProperty]
        private string? controlErrorMessage;

        [ObservableProperty]
        private string? selectedAssetFilter;

        [ObservableProperty]
        private string? selectedTimeframeFilter;

        public ObservableCollection<string> AssetFilterOptions { get; } = new() { "All" };
        public ObservableCollection<string> TimeframeFilterOptions { get; } = new() { "All" };
        public ObservableCollection<LogEntryDto> LogEntries { get; } = new();

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

            _statusPollTimer = new DispatcherTimer { Interval = StatusPollInterval };
            _statusPollTimer.Tick += (_, _) => _ = RefreshStatus();
            _statusPollTimer.Start();

            _logPollTimer = new DispatcherTimer { Interval = LogPollInterval };
            _logPollTimer.Tick += (_, _) => _ = RefreshLogs();
            _logPollTimer.Start();

            _ = InitializeAsync();
            _ = RefreshStatus();
            _ = RefreshLogs();
        }

        /// <summary>Call this when navigating away from Auto Trading so background polling stops.</summary>
        public void Cleanup()
        {
            _autoRefreshTimer.Stop();
            _statusPollTimer.Stop();
            _logPollTimer.Stop();
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

                // Asset/Timeframe Selection (2026-07-30) - real filters against the
                // existing GET /signals query params (symbol/timeframe), not a
                // client-side re-filter of already-fetched data. "All" (or unset)
                // omits the param entirely, exactly matching the endpoint's own
                // optional-filter semantics.
                var signalsUrl = "signals?status=ACTIVE&page_size=100";
                if (!string.IsNullOrEmpty(SelectedAssetFilter) && SelectedAssetFilter != "All")
                    signalsUrl += $"&symbol={Uri.EscapeDataString(SelectedAssetFilter)}";
                if (!string.IsNullOrEmpty(SelectedTimeframeFilter) && SelectedTimeframeFilter != "All")
                    signalsUrl += $"&timeframe={Uri.EscapeDataString(SelectedTimeframeFilter)}";

                var signalsResponse = await _apiService.GetAsync<SignalListResponseDto>(signalsUrl);

                ExecutableSignals.Clear();
                if (signalsResponse?.Items != null)
                {
                    // Grow the filter dropdowns from symbols/timeframes actually
                    // seen on real signals - never a hand-picked/fabricated list.
                    foreach (var symbol in signalsResponse.Items.Select(s => s.CoinSymbol).Distinct().OrderBy(s => s))
                        if (!AssetFilterOptions.Contains(symbol))
                            AssetFilterOptions.Add(symbol);
                    foreach (var tf in signalsResponse.Items.Select(s => s.Timeframe).Distinct().OrderBy(s => s))
                        if (!TimeframeFilterOptions.Contains(tf))
                            TimeframeFilterOptions.Add(tf);

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
                            EntryType = s.EntryType,
                            EntryZoneTop = s.EntryZoneTop,
                            EntryZoneBottom = s.EntryZoneBottom,
                            EntryExpiresAt = s.EntryExpiresAt,
                            FilledAt = s.FilledAt,
                            ActualFillPrice = s.ActualFillPrice,
                            EntryOrderId = s.EntryOrderId,
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

            // The confirmation must describe what will ACTUALLY be placed.
            // Under ICT pending entry that is a resting LIMIT order that may
            // never fill - materially different from an immediate market
            // fill, so it is never described as one.
            var actionVerb = signal.IsPendingEntry ? "Arm ICT pending entry for" : "Execute";
            var orderDescription = signal.IsPendingEntry
                ? $"This rests a real LIMIT order at {signal.Entry} on Binance.\n" +
                  "It fills ONLY if price trades into the ICT entry zone. The stop-loss and\n" +
                  "take-profit are placed automatically the moment it fills. If price never\n" +
                  "reaches the zone, the entry expires and the order is cancelled."
                : "This places a real MARKET order plus stop-loss/take-profit on Binance right now.";

            var confirm = MessageBox.Show(
                $"{actionVerb} {signal.Direction} {signal.Coin} on {envLabel}?\n\n" +
                $"Entry: {signal.Entry}\nStop Loss: {signal.StopLoss}\nTake Profit (TP1): {signal.TakeProfit}\n" +
                $"Suggested quantity: {signal.PositionSizeDisplay}\n\n" +
                orderDescription,
                signal.IsPendingEntry ? "Confirm Arm Entry" : "Confirm Execute",
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

        // ==================================================================
        // Auto Trading Control Panel (2026-07-30)
        //
        // Every method below is a single call to a real endpoint in
        // app/api/v1/endpoints/trading_control.py. None of them contain a
        // decision, a loop over positions/orders, or any ICT/AI/Scanner
        // logic - that all lives in the backend (see that file and
        // app/services/trading_control_service.py). This file only sends
        // the request and renders the response.
        // ==================================================================

        /// <summary>CommunityToolkit.Mvvm partial hook - fires whenever
        /// <see cref="SelectedAssetFilter"/> changes (including from the
        /// combo box binding), re-querying GET /signals with the new
        /// symbol filter applied server-side.</summary>
        partial void OnSelectedAssetFilterChanged(string? value) => _ = Refresh();

        /// <summary>Same as above for <see cref="SelectedTimeframeFilter"/>.</summary>
        partial void OnSelectedTimeframeFilterChanged(string? value) => _ = Refresh();

        [RelayCommand]
        private async Task RefreshStatus()
        {
            try
            {
                var status = await _apiService.GetAsync<TradingStatusDto>("trading/status");
                if (status != null)
                    TradingStatus = status;
                ControlErrorMessage = null;
            }
            catch (Exception ex)
            {
                // Non-fatal and silent-by-default: this polls every 10s in
                // the background, and a transient backend hiccup shouldn't
                // spam the user - the status card itself will simply show
                // its last-known values until the next successful poll.
                ControlErrorMessage = $"Status unavailable: {ex.Message}";
            }
        }

        [RelayCommand]
        private async Task RefreshLogs()
        {
            try
            {
                var logs = await _apiService.GetAsync<LogsResponseDto>("trading/logs?limit=200");
                if (logs?.Items != null)
                {
                    LogEntries.Clear();
                    foreach (var entry in logs.Items)
                        LogEntries.Add(entry);
                }
            }
            catch
            {
                // Silent - logs are a convenience view, never critical path.
            }
        }

        [RelayCommand]
        private async Task ToggleAutoTrading()
        {
            if (IsControlActionRunning)
                return;

            var turningOn = !TradingStatus.AutoTradingEnabled;
            if (!turningOn)
            {
                var confirm = MessageBox.Show(
                    "Turn Auto Trading OFF?\n\nNo new signal can be executed until you turn it back on. " +
                    "Positions and orders already open are NOT affected.",
                    "Confirm Auto Trading OFF", MessageBoxButton.YesNo, MessageBoxImage.Question);
                if (confirm != MessageBoxResult.Yes)
                    return;
            }

            IsControlActionRunning = true;
            ControlErrorMessage = null;
            try
            {
                var endpoint = turningOn ? "trading/auto-trading/enable" : "trading/auto-trading/disable";
                var result = await _apiService.PostAsync<EmptyRequestDto, AutoTradingToggleResponseDto>(endpoint, new EmptyRequestDto());
                ControlStatusMessage = result?.Message;
                await RefreshStatus();
            }
            catch (Exception ex)
            {
                ControlErrorMessage = $"Could not change Auto Trading: {ex.Message}";
            }
            finally
            {
                IsControlActionRunning = false;
            }
        }

        private async Task RunEngineAction(string endpoint, string confirmedLabel)
        {
            if (IsControlActionRunning)
                return;

            IsControlActionRunning = true;
            ControlErrorMessage = null;
            try
            {
                var result = await _apiService.PostAsync<EmptyRequestDto, EngineActionResponseDto>(endpoint, new EmptyRequestDto());
                ControlStatusMessage = result?.Message ?? confirmedLabel;
                await RefreshStatus();
            }
            catch (Exception ex)
            {
                ControlErrorMessage = $"Could not {confirmedLabel}: {ex.Message}";
            }
            finally
            {
                IsControlActionRunning = false;
            }
        }

        [RelayCommand]
        private Task StartEngine() => RunEngineAction("trading/engine/start", "start the engine");

        [RelayCommand]
        private Task PauseEngine() => RunEngineAction("trading/engine/pause", "pause the engine");

        [RelayCommand]
        private Task StopEngine() => RunEngineAction("trading/engine/stop", "stop the engine");

        [RelayCommand]
        private Task ResumeEngine() => RunEngineAction("trading/engine/resume", "resume the engine");

        [RelayCommand]
        private async Task CloseAllPositions()
        {
            if (IsControlActionRunning)
                return;

            var envLabel = Environment == "testnet" ? "TESTNET (no real funds)" : "MAINNET (REAL FUNDS)";
            var confirm = MessageBox.Show(
                $"Close EVERY open position on {envLabel}?\n\nThis places a real reduceOnly MARKET order for each open position right now.",
                "Confirm Close All Positions", MessageBoxButton.YesNo,
                Environment == "testnet" ? MessageBoxImage.Question : MessageBoxImage.Warning);
            if (confirm != MessageBoxResult.Yes)
                return;

            IsControlActionRunning = true;
            ControlErrorMessage = null;
            try
            {
                var result = await _apiService.PostAsync<EmptyRequestDto, BulkActionResponseDto>(
                    "trading/close-all-positions", new EmptyRequestDto());
                ControlStatusMessage = result != null
                    ? $"Close All Positions: {result.Succeeded}/{result.Attempted} closed.{(result.Message != null ? " " + result.Message : "")}"
                    : null;
                await Refresh();
                await RefreshStatus();
            }
            catch (Exception ex)
            {
                ControlErrorMessage = $"Close All Positions failed: {ex.Message}";
            }
            finally
            {
                IsControlActionRunning = false;
            }
        }

        [RelayCommand]
        private async Task CancelAllOrders()
        {
            if (IsControlActionRunning)
                return;

            var confirm = MessageBox.Show(
                "Cancel EVERY pending order account-wide?\n\nThis includes protective stop-loss/take-profit orders on open positions.",
                "Confirm Cancel Pending Orders", MessageBoxButton.YesNo, MessageBoxImage.Warning);
            if (confirm != MessageBoxResult.Yes)
                return;

            IsControlActionRunning = true;
            ControlErrorMessage = null;
            try
            {
                var result = await _apiService.PostAsync<EmptyRequestDto, BulkActionResponseDto>(
                    "trading/cancel-all-orders", new EmptyRequestDto());
                ControlStatusMessage = result != null
                    ? $"Cancel Pending Orders: {result.Succeeded}/{result.Attempted} cancelled.{(result.Message != null ? " " + result.Message : "")}"
                    : null;
                await Refresh();
                await RefreshStatus();
            }
            catch (Exception ex)
            {
                ControlErrorMessage = $"Cancel Pending Orders failed: {ex.Message}";
            }
            finally
            {
                IsControlActionRunning = false;
            }
        }

        [RelayCommand]
        private async Task KillSwitch()
        {
            if (IsControlActionRunning)
                return;

            var confirm = MessageBox.Show(
                "EMERGENCY KILL SWITCH\n\nThis will immediately:\n" +
                "  - Turn Auto Trading OFF\n" +
                "  - Stop the scanner and signal monitor\n" +
                "  - Close EVERY open position with a real MARKET order\n" +
                "  - Cancel EVERY pending order account-wide\n\n" +
                "This cannot be undone. Continue?",
                "CONFIRM EMERGENCY KILL SWITCH", MessageBoxButton.YesNo, MessageBoxImage.Stop);
            if (confirm != MessageBoxResult.Yes)
                return;

            IsControlActionRunning = true;
            ControlErrorMessage = null;
            try
            {
                var result = await _apiService.PostAsync<EmptyRequestDto, KillSwitchResponseDto>(
                    "trading/kill-switch", new EmptyRequestDto());
                if (result != null)
                {
                    ControlStatusMessage =
                        $"Kill switch complete. Positions closed: {result.ClosePositions.Succeeded}/{result.ClosePositions.Attempted}. " +
                        $"Orders cancelled: {result.CancelOrders.Succeeded}/{result.CancelOrders.Attempted}.";
                }
                await Refresh();
                await RefreshStatus();
            }
            catch (Exception ex)
            {
                ControlErrorMessage = $"Kill switch failed: {ex.Message}";
            }
            finally
            {
                IsControlActionRunning = false;
            }
        }
    }
}
