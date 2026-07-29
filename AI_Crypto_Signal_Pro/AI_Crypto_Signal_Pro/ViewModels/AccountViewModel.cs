using System;
using System.Collections.ObjectModel;
using System.Threading.Tasks;
using System.Windows.Threading;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using AI_Crypto_Signal_Pro.Models;
using AI_Crypto_Signal_Pro.Services;

namespace AI_Crypto_Signal_Pro.ViewModels
{
    /// <summary>
    /// Read-only Binance account viewer. Talks only to the backend's
    /// /api/v1/account/* endpoints (BinanceAccountService) - never places,
    /// cancels, or modifies orders. Credentials themselves are entered and
    /// saved from the Settings screen; this screen only displays data and
    /// offers Test Connection / Refresh.
    ///
    /// Supports auto-refresh (for live position/balance tracking): call
    /// StopAutoRefresh() when this screen is navigated away from, or the
    /// timer keeps polling the backend/Binance in the background forever.
    /// </summary>
    public partial class AccountViewModel : ObservableObject
    {
        private static readonly TimeSpan AutoRefreshInterval = TimeSpan.FromSeconds(10);

        private readonly ApiService _apiService = new();
        private readonly DispatcherTimer _autoRefreshTimer;

        [ObservableProperty]
        private bool isAutoRefreshEnabled = true;

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

        public ObservableCollection<AssetBalanceDto> Balances { get; } = new();
        public ObservableCollection<FuturesPositionDto> OpenPositions { get; } = new();
        public ObservableCollection<TradeRecordDto> TradeHistory { get; } = new();
        public ObservableCollection<OpenOrderInfoDto> OpenOrders { get; } = new();
        public ObservableCollection<string> Warnings { get; } = new();

        public AccountViewModel()
        {
            _autoRefreshTimer = new DispatcherTimer { Interval = AutoRefreshInterval };
            _autoRefreshTimer.Tick += (_, _) => _ = Refresh();

            // The [ObservableProperty] field initializer above doesn't route through the
            // generated setter, so OnIsAutoRefreshEnabledChanged won't fire for the default
            // value - start explicitly here if auto-refresh is on by default.
            if (IsAutoRefreshEnabled)
                StartAutoRefresh();

            _ = InitializeAsync();
        }

        partial void OnIsAutoRefreshEnabledChanged(bool value)
        {
            if (value)
                StartAutoRefresh();
            else
                StopAutoRefresh();
        }

        private void StartAutoRefresh()
        {
            if (!_autoRefreshTimer.IsEnabled)
                _autoRefreshTimer.Start();
        }

        /// <summary>Call this when navigating away from the Account screen so background polling stops.</summary>
        public void StopAutoRefresh()
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
                    StatusMessage = "No Binance API credentials saved yet. Add them in Settings, then come back here.";
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
                var snapshot = await _apiService.GetAsync<AccountSnapshotDto>("account/snapshot");
                if (snapshot is null)
                    return;

                Environment = snapshot.Environment;
                Account = snapshot.Account;
                TotalWalletValueUsdt = snapshot.TotalWalletValueUsdt;
                Futures = snapshot.Futures;

                Balances.Clear();
                foreach (var b in snapshot.Balances)
                    Balances.Add(b);

                OpenPositions.Clear();
                foreach (var p in snapshot.Futures?.OpenPositions ?? new())
                    OpenPositions.Add(p);

                TradeHistory.Clear();
                foreach (var t in snapshot.TradeHistory)
                    TradeHistory.Add(t);

                OpenOrders.Clear();
                foreach (var o in snapshot.OpenOrders)
                    OpenOrders.Add(o);

                Warnings.Clear();
                foreach (var w in snapshot.Warnings)
                    Warnings.Add(w);

                StatusMessage = $"Refreshed ({DateTime.Now:HH:mm:ss}).";
            }
            catch (Exception ex)
            {
                ErrorMessage = $"Could not load account snapshot: {ex.Message}";
            }
            finally
            {
                IsLoading = false;
            }
        }

        [RelayCommand]
        private async Task TestConnection()
        {
            if (IsLoading)
                return;

            IsLoading = true;
            ErrorMessage = null;
            StatusMessage = null;

            try
            {
                var result = await _apiService.PostAsync<TestConnectionRequestDto, ConnectionTestResultDto>(
                    "account/test-connection", new TestConnectionRequestDto());

                if (result is null)
                    return;

                StatusMessage = result.Success
                    ? $"Connected ({result.Environment}): {result.Message}"
                    : null;

                if (!result.Success)
                    ErrorMessage = $"Connection failed ({result.Environment}): {result.Message}";
            }
            catch (Exception ex)
            {
                ErrorMessage = $"Test connection failed: {ex.Message}";
            }
            finally
            {
                IsLoading = false;
            }
        }
    }
}
