using System;
using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace AI_Crypto_Signal_Pro.Models
{
    /// <summary>Matches FastAPI RiskLimitsResponse schema (app/schemas/trading_control.py) -
    /// mirrors app.risk.risk_engine.RiskLimits field-for-field, the actual configured
    /// thresholds RiskEngine enforces at execution. Not hand-copied constants.</summary>
    public sealed class RiskLimitsDto
    {
        [JsonPropertyName("max_risk_percent_per_trade")]
        public double MaxRiskPercentPerTrade { get; set; }

        [JsonPropertyName("max_open_risk_percent")]
        public double MaxOpenRiskPercent { get; set; }

        [JsonPropertyName("max_daily_loss_percent")]
        public double MaxDailyLossPercent { get; set; }

        [JsonPropertyName("max_exposure_percent")]
        public double MaxExposurePercent { get; set; }

        [JsonPropertyName("max_drawdown_percent")]
        public double MaxDrawdownPercent { get; set; }
    }

    /// <summary>Matches FastAPI SessionStatusResponse schema. Read-only status (not a
    /// selectable filter) - there is no persisted "session" dimension on a Signal to
    /// select by; this reflects the live ICT session/kill-zone classification for the
    /// reference symbol.</summary>
    public sealed class SessionStatusDto
    {
        [JsonPropertyName("reference_symbol")]
        public string ReferenceSymbol { get; set; } = string.Empty;

        [JsonPropertyName("active_sessions")]
        public List<string> ActiveSessions { get; set; } = new();

        [JsonPropertyName("in_kill_zone")]
        public bool InKillZone { get; set; }

        [JsonPropertyName("kill_zone")]
        public string? KillZone { get; set; }

        [JsonPropertyName("is_london_ny_overlap")]
        public bool IsLondonNyOverlap { get; set; }

        [JsonPropertyName("message")]
        public string? Message { get; set; }

        /// <summary>Human-readable summary for a single status chip.</summary>
        public string Display
        {
            get
            {
                if (!string.IsNullOrEmpty(Message))
                    return Message;

                var sessions = ActiveSessions.Count > 0 ? string.Join(" + ", ActiveSessions) : "Off-session";
                var kz = InKillZone ? $" ({KillZone} kill zone)" : "";
                var overlap = IsLondonNyOverlap ? " [London/NY overlap]" : "";
                return $"{sessions}{kz}{overlap}";
            }
        }
    }

    /// <summary>Matches FastAPI TradingStatusResponse schema - the single aggregated
    /// read the Auto Trading Control Panel polls (GET /trading/status). Every field is
    /// a real backend read - see that endpoint's docstring.</summary>
    public sealed class TradingStatusDto
    {
        [JsonPropertyName("has_credentials")]
        public bool HasCredentials { get; set; }

        [JsonPropertyName("environment")]
        public string? Environment { get; set; }

        [JsonPropertyName("auto_trading_enabled")]
        public bool AutoTradingEnabled { get; set; }

        [JsonPropertyName("engine_run_state")]
        public string EngineRunState { get; set; } = "running";

        [JsonPropertyName("binance_connected")]
        public bool BinanceConnected { get; set; }

        [JsonPropertyName("scanner_running")]
        public bool ScannerRunning { get; set; }

        [JsonPropertyName("signal_monitor_running")]
        public bool SignalMonitorRunning { get; set; }

        [JsonPropertyName("risk_engine_available")]
        public bool RiskEngineAvailable { get; set; }

        [JsonPropertyName("ai_calibrated")]
        public bool AiCalibrated { get; set; }

        [JsonPropertyName("ai_status_message")]
        public string? AiStatusMessage { get; set; }

        [JsonPropertyName("risk_limits")]
        public RiskLimitsDto RiskLimits { get; set; } = new();

        [JsonPropertyName("portfolio_open_risk_percent")]
        public double? PortfolioOpenRiskPercent { get; set; }

        [JsonPropertyName("daily_pnl_usd")]
        public double? DailyPnlUsd { get; set; }

        [JsonPropertyName("daily_pnl_percent")]
        public double? DailyPnlPercent { get; set; }

        [JsonPropertyName("daily_loss_breached")]
        public bool DailyLossBreached { get; set; }

        [JsonPropertyName("win_rate")]
        public double? WinRate { get; set; }

        [JsonPropertyName("total_signals")]
        public int TotalSignals { get; set; }

        [JsonPropertyName("active_trades")]
        public int ActiveTrades { get; set; }

        [JsonPropertyName("pending_signals")]
        public int PendingSignals { get; set; }

        /// <summary>ICT Pending Limit Entry (2026-07-30): entries ARMED with a real
        /// LIMIT order resting on the exchange, waiting for price to reach the ICT
        /// zone. Distinct from PendingSignals (awaiting an Execute click, no order
        /// exists) and ActiveTrades (a position is actually open).</summary>
        [JsonPropertyName("pending_entries")]
        public int PendingEntries { get; set; }

        [JsonPropertyName("executed_signals")]
        public int ExecutedSignals { get; set; }

        /// <summary>Which entry model is in force platform-wide: "ict_pending" | "market".</summary>
        [JsonPropertyName("entry_mode")]
        public string EntryMode { get; set; } = "ict_pending";

        [JsonPropertyName("session")]
        public SessionStatusDto Session { get; set; } = new();

        // ---- Computed display helpers (same pattern as SignalModel's
        // ExecuteButtonLabel/PositionSizeDisplay) - hex color strings bound
        // directly to Foreground, matching MainWindowViewModel's existing
        // BackendStatusColor/DbStatusColor/BinanceStatusColor convention. ----
        private const string ColorOnline = "#00E676";
        private const string ColorOffline = "#FF5252";
        private const string ColorWarning = "#FFC107";
        private const string ColorUnknown = "#9E9E9E";

        public string TradingStatusLabel => EngineRunState switch
        {
            "running" => "Running",
            "paused" => "Paused",
            "stopped" => "Stopped",
            _ => "Unknown",
        };

        public string TradingStatusColor => EngineRunState switch
        {
            "running" => ColorOnline,
            "paused" => ColorWarning,
            "stopped" => ColorOffline,
            _ => ColorUnknown,
        };

        public string BinanceStatusLabel => !HasCredentials ? "No credentials" : BinanceConnected ? "Connected" : "Disconnected";
        public string BinanceStatusColor => !HasCredentials ? ColorUnknown : BinanceConnected ? ColorOnline : ColorOffline;

        public string AiStatusLabel => AiCalibrated ? "Calibrated" : "Learning";
        public string AiStatusColor => AiCalibrated ? ColorOnline : ColorWarning;

        public string ScannerStatusLabel => ScannerRunning ? "Scanning" : "Stopped";
        public string ScannerStatusColor => ScannerRunning ? ColorOnline : ColorOffline;

        public string RiskEngineStatusLabel => RiskEngineAvailable ? "Available" : "No account linked";
        public string RiskEngineStatusColor => RiskEngineAvailable ? ColorOnline : ColorUnknown;

        public string AutoTradingLabel => AutoTradingEnabled ? "ON" : "OFF";
        public string AutoTradingColor => AutoTradingEnabled ? ColorOnline : ColorOffline;

        public string WinRateDisplay => WinRate.HasValue ? $"{WinRate.Value:0.#}%" : "No closed trades yet";

        /// <summary>Entry-model badge for the control panel - makes it unambiguous
        /// which execution model is currently live.</summary>
        public string EntryModeLabel => EntryMode == "ict_pending" ? "ICT Pending Limit" : "Market";

        public string EntryModeColor => EntryMode == "ict_pending" ? ColorOnline : ColorWarning;

        public string PendingEntriesDisplay =>
            PendingEntries == 0 ? "None armed" : $"{PendingEntries} armed, awaiting fill";

        public string DailyPnlDisplay => DailyPnlUsd.HasValue ? $"{DailyPnlUsd.Value:+0.##;-0.##;0} USD" : "No account linked";
        public string DailyPnlColor => !DailyPnlUsd.HasValue ? ColorUnknown : DailyLossBreached ? ColorOffline : DailyPnlUsd.Value >= 0 ? ColorOnline : ColorWarning;

        public string PortfolioOpenRiskDisplay => PortfolioOpenRiskPercent.HasValue ? $"{PortfolioOpenRiskPercent.Value:0.#}%" : "No account linked";
    }

    /// <summary>Matches FastAPI EngineActionResponse schema - response of
    /// POST /trading/engine/start|pause|stop|resume.</summary>
    public sealed class EngineActionResponseDto
    {
        [JsonPropertyName("engine_run_state")]
        public string EngineRunState { get; set; } = string.Empty;

        [JsonPropertyName("message")]
        public string Message { get; set; } = string.Empty;
    }

    /// <summary>Matches FastAPI AutoTradingToggleResponse schema - response of
    /// POST /trading/auto-trading/enable|disable.</summary>
    public sealed class AutoTradingToggleResponseDto
    {
        [JsonPropertyName("auto_trading_enabled")]
        public bool AutoTradingEnabled { get; set; }

        [JsonPropertyName("message")]
        public string Message { get; set; } = string.Empty;
    }

    /// <summary>Matches FastAPI BulkActionItemResponse schema - one Close All Positions /
    /// Cancel Pending Orders line item.</summary>
    public sealed class BulkActionItemDto
    {
        [JsonPropertyName("symbol")]
        public string Symbol { get; set; } = string.Empty;

        [JsonPropertyName("success")]
        public bool Success { get; set; }

        [JsonPropertyName("detail")]
        public string Detail { get; set; } = string.Empty;

        [JsonPropertyName("order_id")]
        public long? OrderId { get; set; }
    }

    /// <summary>Matches FastAPI BulkActionResponse schema - response of
    /// POST /trading/close-all-positions and POST /trading/cancel-all-orders.</summary>
    public sealed class BulkActionResponseDto
    {
        [JsonPropertyName("action")]
        public string Action { get; set; } = string.Empty;

        [JsonPropertyName("environment")]
        public string? Environment { get; set; }

        [JsonPropertyName("attempted")]
        public int Attempted { get; set; }

        [JsonPropertyName("succeeded")]
        public int Succeeded { get; set; }

        [JsonPropertyName("items")]
        public List<BulkActionItemDto> Items { get; set; } = new();

        [JsonPropertyName("message")]
        public string? Message { get; set; }

        public string Summary => Message ?? $"{Succeeded}/{Attempted} succeeded.";
    }

    /// <summary>Matches FastAPI KillSwitchResponse schema - response of
    /// POST /trading/kill-switch.</summary>
    public sealed class KillSwitchResponseDto
    {
        [JsonPropertyName("auto_trading_disabled")]
        public bool AutoTradingDisabled { get; set; }

        [JsonPropertyName("engine_stopped")]
        public bool EngineStopped { get; set; }

        [JsonPropertyName("close_positions")]
        public BulkActionResponseDto ClosePositions { get; set; } = new();

        [JsonPropertyName("cancel_orders")]
        public BulkActionResponseDto CancelOrders { get; set; } = new();
    }

    /// <summary>Matches FastAPI LogEntryResponse schema - one Trading Logs row.</summary>
    public sealed class LogEntryDto
    {
        [JsonPropertyName("time")]
        public string Time { get; set; } = string.Empty;

        [JsonPropertyName("level")]
        public string Level { get; set; } = string.Empty;

        [JsonPropertyName("logger")]
        public string Logger { get; set; } = string.Empty;

        [JsonPropertyName("message")]
        public string Message { get; set; } = string.Empty;
    }

    /// <summary>Matches FastAPI LogsResponse schema - response of GET /trading/logs.</summary>
    public sealed class LogsResponseDto
    {
        [JsonPropertyName("items")]
        public List<LogEntryDto> Items { get; set; } = new();

        [JsonPropertyName("count")]
        public int Count { get; set; }
    }
}
