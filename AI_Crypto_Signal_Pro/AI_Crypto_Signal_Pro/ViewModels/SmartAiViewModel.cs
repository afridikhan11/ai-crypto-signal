using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Threading;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using AI_Crypto_Signal_Pro.Models;
using AI_Crypto_Signal_Pro.Services;

namespace AI_Crypto_Signal_Pro.ViewModels
{
    /// <summary>
    /// Per-strategy row shown as a tab in the Smart AI page. Merges the plain
    /// /smartai/status strategy fields with the live /smartai/runner/status
    /// counters (running / restarts / last_error / evaluations / signals).
    /// </summary>
    public partial class SmartAiStrategyVm : ObservableObject
    {
        public string StrategyId { get; }

        [ObservableProperty]
        private string displayName = string.Empty;

        [ObservableProperty]
        [NotifyPropertyChangedFor(nameof(EnabledLabel))]
        [NotifyPropertyChangedFor(nameof(EnabledColor))]
        [NotifyPropertyChangedFor(nameof(ToggleButtonLabel))]
        private bool enabled;

        [ObservableProperty]
        [NotifyPropertyChangedFor(nameof(RunStatusLabel))]
        [NotifyPropertyChangedFor(nameof(RunStatusColor))]
        private bool running;

        [ObservableProperty]
        private long evaluations;

        [ObservableProperty]
        private long signalCount;

        [ObservableProperty]
        private int restarts;

        [ObservableProperty]
        [NotifyPropertyChangedFor(nameof(HasLastError))]
        private string? lastError;

        public SmartAiStrategyVm(string strategyId)
        {
            StrategyId = strategyId;
        }

        public string EnabledLabel => Enabled ? "ENABLED" : "DISABLED";
        public string EnabledColor => Enabled ? "#00E676" : "#9E9E9E";
        public string ToggleButtonLabel => Enabled ? "Disable Strategy" : "Enable Strategy";

        public string RunStatusLabel => Running ? "RUNNING" : "STOPPED";
        public string RunStatusColor => Running ? "#00E676" : "#FF5252";

        public bool HasLastError => !string.IsNullOrWhiteSpace(LastError);
    }

    /// <summary>
    /// Smart AI page. Binds to the /api/v1/smartai/* contract through
    /// <see cref="SmartAiApiService"/>. The whole page is gated behind an
    /// in-view LOCKED overlay until a password login (or a restored "remember
    /// me" session) succeeds - matching how this app already does everything as
    /// UserControls (there is no modal-Window pattern anywhere in the codebase).
    ///
    /// State machine (each state has a visible UI representation):
    ///   IsLocked        -> login overlay covers the page
    ///   IsLoading       -> progress ring
    ///   ErrorMessage    -> inline error banner
    ///   IsEmpty         -> "no signals yet" empty-state for the selected strategy
    ///
    /// All I/O is async (no .Result/.Wait). A single CancellationTokenSource is
    /// held for the lifetime of the page and cancelled+disposed on navigation
    /// away (see <see cref="Cleanup"/>), so in-flight requests are abandoned
    /// when the user leaves.
    /// </summary>
    public partial class SmartAiViewModel : ObservableObject
    {
        // Fixed drawing surface for the hand-drawn equity curve. These MUST match
        // the Canvas Width/Height in SmartAiView.xaml - the Polyline points are
        // computed here in those pixel coordinates (there is no charting library).
        private const double ChartWidth = 560;
        private const double ChartHeight = 150;
        private const double ChartPadding = 8;

        private static readonly TimeSpan RunnerPollInterval = TimeSpan.FromSeconds(10);

        private readonly SmartAiApiService _api = new();
        private readonly DispatcherTimer _runnerPollTimer;

        private CancellationTokenSource _cts = new();

        [ObservableProperty]
        private bool isLocked = true;

        [ObservableProperty]
        private bool isLoading;

        [ObservableProperty]
        private bool isEmpty;

        [ObservableProperty]
        private string? errorMessage;

        [ObservableProperty]
        private bool isAuthenticating;

        [ObservableProperty]
        private string? loginError;

        [ObservableProperty]
        private bool isTogglingStrategy;

        // ---- Environment / module ----

        [ObservableProperty]
        private bool moduleEnabled;

        [ObservableProperty]
        [NotifyPropertyChangedFor(nameof(EnvironmentBadge))]
        [NotifyPropertyChangedFor(nameof(EnvironmentBadgeText))]
        private bool testnet = true;

        /// <summary>Maps onto the existing EnvironmentToBrushConverter ("testnet"
        /// -> amber, "mainnet" -> red) so the LIVE/TESTNET badge reuses the exact
        /// same loud colour language as the Auto Trading environment badge.</summary>
        public string EnvironmentBadge => Testnet ? "testnet" : "mainnet";

        public string EnvironmentBadgeText => Testnet ? "TESTNET" : "LIVE";

        // ---- Data ----

        public ObservableCollection<SmartAiStrategyVm> Strategies { get; } = new();

        [ObservableProperty]
        private SmartAiStrategyVm? selectedStrategy;

        public ObservableCollection<SmartAiSignalDto> Signals { get; } = new();

        [ObservableProperty]
        private SmartAiPerformanceDto? performance;

        // ---- Equity curve (hand-drawn Polyline) ----

        [ObservableProperty]
        [NotifyPropertyChangedFor(nameof(HasEquityCurve))]
        private PointCollection equityCurvePoints = new();

        [ObservableProperty]
        private double equityBaselineY = ChartHeight / 2;

        [ObservableProperty]
        private string equityCurveCaption = "Cumulative outcome of closed signals";

        public bool HasEquityCurve => EquityCurvePoints.Count >= 2;

        public SmartAiViewModel()
        {
            _runnerPollTimer = new DispatcherTimer { Interval = RunnerPollInterval };
            _runnerPollTimer.Tick += (_, _) => _ = PollRunnerAsync();

            _ = InitializeAsync();
        }

        /// <summary>Call from the view's Unloaded handler: cancels in-flight
        /// requests and stops polling so nothing runs after navigation away.</summary>
        public void Cleanup()
        {
            _runnerPollTimer.Stop();
            try { _cts.Cancel(); } catch { /* already disposed */ }
            _cts.Dispose();
        }

        private async Task InitializeAsync()
        {
            try
            {
                // Silently reuse a "remember me" session if one is stored.
                var restored = await _api.TryRestoreSessionAsync(_cts.Token);
                if (restored)
                    await EnterAuthenticatedAsync();
            }
            catch (OperationCanceledException)
            {
                // Navigated away during restore - nothing to do.
            }
            catch
            {
                // Restore is best-effort; fall through to the locked login state.
            }
        }

        // ============================ Auth ============================

        [RelayCommand]
        private async Task Login(object? passwordBox)
        {
            if (IsAuthenticating)
                return;

            // The password is read straight off the PasswordBox for this one
            // call and never stored on the ViewModel (PasswordBox.Password is
            // deliberately not bindable for exactly this reason).
            var password = (passwordBox as PasswordBox)?.Password ?? string.Empty;
            if (string.IsNullOrEmpty(password))
            {
                LoginError = "Enter the Smart AI password.";
                return;
            }

            IsAuthenticating = true;
            LoginError = null;
            try
            {
                await _api.LoginAsync(password, false, _cts.Token);
                (passwordBox as PasswordBox)?.Clear();
                await EnterAuthenticatedAsync();
            }
            catch (OperationCanceledException)
            {
                // Navigated away mid-login.
            }
            catch (Exception ex)
            {
                LoginError = $"Login failed: {ex.Message}";
                IsLocked = true;
            }
            finally
            {
                IsAuthenticating = false;
            }
        }

        [RelayCommand]
        private void Logout()
        {
            _runnerPollTimer.Stop();
            _api.ClearSession();
            Strategies.Clear();
            Signals.Clear();
            Performance = null;
            SelectedStrategy = null;
            EquityCurvePoints = new PointCollection();
            IsEmpty = false;
            ErrorMessage = null;
            IsLocked = true;
        }

        private async Task EnterAuthenticatedAsync()
        {
            IsLocked = false;
            LoginError = null;
            await LoadStatusAndStrategiesAsync();
            _runnerPollTimer.Start();
        }

        /// <summary>Single funnel for the "any 401 clears the token and returns
        /// to the locked/login state" rule. Returns true if the failure was an
        /// auth failure (already handled by locking).</summary>
        private bool HandleIfUnauthorized(Exception ex)
        {
            if (ex is SmartAiUnauthorizedException)
            {
                _runnerPollTimer.Stop();
                Strategies.Clear();
                Signals.Clear();
                Performance = null;
                SelectedStrategy = null;
                EquityCurvePoints = new PointCollection();
                IsEmpty = false;
                LoginError = "Your Smart AI session expired. Please sign in again.";
                IsLocked = true;
                return true;
            }
            return false;
        }

        // ============================ Loading ============================

        [RelayCommand]
        private async Task Refresh()
        {
            if (IsLocked)
                return;
            await LoadStatusAndStrategiesAsync();
        }

        private async Task LoadStatusAndStrategiesAsync()
        {
            if (IsLoading)
                return;

            IsLoading = true;
            ErrorMessage = null;
            try
            {
                var status = await _api.GetStatusAsync(_cts.Token);
                if (status != null)
                {
                    ModuleEnabled = status.ModuleEnabled;
                    Testnet = status.Testnet;
                }

                var runner = await _api.GetRunnerStatusAsync(_cts.Token);

                MergeStrategies(status?.Strategies, runner?.Strategies);

                // Keep the current tab selected if it still exists; otherwise pick the first.
                var keepId = SelectedStrategy?.StrategyId;
                var next = Strategies.FirstOrDefault(s => s.StrategyId == keepId) ?? Strategies.FirstOrDefault();
                if (!ReferenceEquals(next, SelectedStrategy))
                    SelectedStrategy = next;
                else if (next != null)
                    await LoadSelectedStrategyDataAsync();
            }
            catch (OperationCanceledException)
            {
                // Navigated away.
            }
            catch (Exception ex)
            {
                if (!HandleIfUnauthorized(ex))
                    ErrorMessage = $"Could not load Smart AI status: {ex.Message}";
            }
            finally
            {
                IsLoading = false;
            }
        }

        private void MergeStrategies(
            List<SmartAiStrategyDto>? statusStrategies,
            List<SmartAiRunnerStrategyDto>? runnerStrategies)
        {
            var runnerById = (runnerStrategies ?? new())
                .GroupBy(r => r.StrategyId)
                .ToDictionary(g => g.Key, g => g.First());

            // Source of the strategy set is /status; the runner adds live counters.
            var source = statusStrategies ?? new();

            // Drop rows that vanished.
            for (var i = Strategies.Count - 1; i >= 0; i--)
            {
                if (!source.Any(s => s.StrategyId == Strategies[i].StrategyId))
                    Strategies.RemoveAt(i);
            }

            foreach (var s in source)
            {
                var vm = Strategies.FirstOrDefault(x => x.StrategyId == s.StrategyId);
                if (vm == null)
                {
                    vm = new SmartAiStrategyVm(s.StrategyId);
                    Strategies.Add(vm);
                }

                vm.DisplayName = s.DisplayName;
                vm.Enabled = s.Enabled;

                if (runnerById.TryGetValue(s.StrategyId, out var r))
                {
                    vm.Enabled = r.Enabled;
                    vm.Running = r.Running;
                    vm.Evaluations = r.Evaluations;
                    vm.SignalCount = r.Signals;
                    vm.Restarts = r.Restarts;
                    vm.LastError = r.LastError;
                }
            }
        }

        partial void OnSelectedStrategyChanged(SmartAiStrategyVm? value)
        {
            if (value == null)
                return;
            _ = LoadSelectedStrategyDataAsync();
        }

        private async Task LoadSelectedStrategyDataAsync()
        {
            var strategy = SelectedStrategy;
            if (strategy == null || IsLocked)
                return;

            IsLoading = true;
            ErrorMessage = null;
            try
            {
                var signals = await _api.GetSignalsAsync(strategy.StrategyId, limit: 50, _cts.Token);
                Signals.Clear();
                foreach (var sig in signals ?? Array.Empty<SmartAiSignalDto>())
                    Signals.Add(sig);

                IsEmpty = Signals.Count == 0;

                Performance = await _api.GetPerformanceAsync(strategy.StrategyId, _cts.Token);

                BuildEquityCurve(Signals);
            }
            catch (OperationCanceledException)
            {
                // Navigated away.
            }
            catch (Exception ex)
            {
                if (!HandleIfUnauthorized(ex))
                    ErrorMessage = $"Could not load strategy data: {ex.Message}";
            }
            finally
            {
                IsLoading = false;
            }
        }

        private async Task PollRunnerAsync()
        {
            if (IsLocked)
                return;
            try
            {
                var runner = await _api.GetRunnerStatusAsync(_cts.Token);
                if (runner?.Strategies == null)
                    return;

                foreach (var r in runner.Strategies)
                {
                    var vm = Strategies.FirstOrDefault(x => x.StrategyId == r.StrategyId);
                    if (vm == null)
                        continue;
                    vm.Enabled = r.Enabled;
                    vm.Running = r.Running;
                    vm.Evaluations = r.Evaluations;
                    vm.SignalCount = r.Signals;
                    vm.Restarts = r.Restarts;
                    vm.LastError = r.LastError;
                }
            }
            catch (OperationCanceledException)
            {
            }
            catch (Exception ex)
            {
                // A background poll that 401s must still lock the page.
                HandleIfUnauthorized(ex);
            }
        }

        // ============================ Enable / disable ============================

        [RelayCommand]
        private async Task ToggleStrategy(SmartAiStrategyVm? strategy)
        {
            strategy ??= SelectedStrategy;
            if (strategy == null || IsTogglingStrategy)
                return;

            IsTogglingStrategy = true;
            ErrorMessage = null;
            try
            {
                var turningOn = !strategy.Enabled;
                var result = turningOn
                    ? await _api.EnableStrategyAsync(strategy.StrategyId, _cts.Token)
                    : await _api.DisableStrategyAsync(strategy.StrategyId, _cts.Token);

                if (result != null)
                    strategy.Enabled = result.Enabled;

                // Reflect the change against live runner state immediately.
                await PollRunnerAsync();
            }
            catch (OperationCanceledException)
            {
            }
            catch (Exception ex)
            {
                if (!HandleIfUnauthorized(ex))
                    ErrorMessage = $"Could not change strategy: {ex.Message}";
            }
            finally
            {
                IsTogglingStrategy = false;
            }
        }

        // ============================ Equity curve ============================

        /// <summary>
        /// Builds a cumulative outcome line from the signal list (newest-first
        /// from the API, walked oldest-first here). Each closed signal steps the
        /// running total +1 for a win-like outcome and -1 for a loss-like one;
        /// still-open signals are flat. The resulting integer series is scaled to
        /// the fixed <see cref="ChartWidth"/> x <see cref="ChartHeight"/> canvas.
        /// No charting library is involved - this produces a raw PointCollection
        /// the XAML Polyline renders directly.
        /// </summary>
        private void BuildEquityCurve(IReadOnlyList<SmartAiSignalDto> signals)
        {
            var ordered = signals.OrderBy(s => s.CreatedAt).ToList();

            var cumulative = new List<int>();
            var running = 0;
            foreach (var sig in ordered)
            {
                running += ClassifyOutcome(sig.Status);
                cumulative.Add(running);
            }

            var closedCount = cumulative.Count;
            if (closedCount < 2)
            {
                EquityCurvePoints = new PointCollection();
                EquityBaselineY = ChartHeight / 2;
                EquityCurveCaption = closedCount == 0
                    ? "No signal outcomes yet"
                    : "Not enough outcomes to plot a curve yet";
                return;
            }

            var min = Math.Min(0, cumulative.Min());
            var max = Math.Max(0, cumulative.Max());
            var range = Math.Max(1, max - min);

            var usableWidth = ChartWidth - 2 * ChartPadding;
            var usableHeight = ChartHeight - 2 * ChartPadding;

            double MapY(double value)
                => ChartPadding + (max - value) / range * usableHeight;

            var points = new PointCollection();
            for (var i = 0; i < closedCount; i++)
            {
                var x = ChartPadding + (double)i / (closedCount - 1) * usableWidth;
                points.Add(new Point(x, MapY(cumulative[i])));
            }

            EquityBaselineY = MapY(0);
            EquityCurvePoints = points;
            EquityCurveCaption = $"Cumulative outcome over {closedCount} closed signals (ends {running:+0;-0;0})";
        }

        /// <summary>
        /// Maps a signal status onto a +1 win / -1 loss / 0 open step. The exact
        /// status vocabulary isn't pinned down by the contract, so this matches
        /// defensively on the common win/target/tp and loss/stop/sl spellings and
        /// treats anything else (active, pending, expired, ...) as flat.
        /// </summary>
        private static int ClassifyOutcome(string? status)
        {
            if (string.IsNullOrWhiteSpace(status))
                return 0;

            var s = status.Trim().ToLowerInvariant();

            if (s.Contains("win") || s.Contains("target") || s.Contains("tp") || s.Contains("profit"))
                return 1;
            if (s.Contains("loss") || s.Contains("stop") || s == "sl" || s.Contains("stopped") || s.Contains("liquidat"))
                return -1;

            return 0;
        }
    }
}
