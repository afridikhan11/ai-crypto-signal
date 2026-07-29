using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace AI_Crypto_Signal_Pro.Models
{
    /// <summary>Matches FastAPI AccountPanel schema (app/schemas/dashboard.py).</summary>
    public sealed class AccountPanelDto
    {
        [JsonPropertyName("has_credentials")]
        public bool HasCredentials { get; set; }

        [JsonPropertyName("environment")]
        public string? Environment { get; set; }

        [JsonPropertyName("wallet_balance")]
        public decimal? WalletBalance { get; set; }

        [JsonPropertyName("available_balance")]
        public decimal? AvailableBalance { get; set; }

        [JsonPropertyName("margin_ratio_pct")]
        public decimal? MarginRatioPct { get; set; }

        [JsonPropertyName("unrealized_pnl")]
        public decimal? UnrealizedPnl { get; set; }

        [JsonPropertyName("realized_pnl")]
        public decimal? RealizedPnl { get; set; }

        [JsonPropertyName("open_positions")]
        public int OpenPositions { get; set; }

        [JsonPropertyName("open_orders")]
        public int OpenOrders { get; set; }

        [JsonPropertyName("win_rate")]
        public double? WinRate { get; set; }

        [JsonPropertyName("warnings")]
        public List<string> Warnings { get; set; } = new();
    }

    /// <summary>Matches FastAPI AiPanel schema - real AIScorer internals, not RL training metrics.</summary>
    public sealed class AiPanelDto
    {
        [JsonPropertyName("weights")]
        public Dictionary<string, double> Weights { get; set; } = new();

        [JsonPropertyName("is_calibrated")]
        public bool IsCalibrated { get; set; }

        [JsonPropertyName("wins_collected")]
        public int WinsCollected { get; set; }

        [JsonPropertyName("losses_collected")]
        public int LossesCollected { get; set; }

        [JsonPropertyName("min_required_per_group")]
        public int MinRequiredPerGroup { get; set; }

        [JsonPropertyName("last_signal_symbol")]
        public string? LastSignalSymbol { get; set; }

        [JsonPropertyName("last_signal_direction")]
        public string? LastSignalDirection { get; set; }

        [JsonPropertyName("last_signal_confidence")]
        public int? LastSignalConfidence { get; set; }

        [JsonPropertyName("last_signal_score_breakdown")]
        public Dictionary<string, double>? LastSignalScoreBreakdown { get; set; }

        [JsonPropertyName("last_signal_reason")]
        public string? LastSignalReason { get; set; }
    }

    /// <summary>Matches FastAPI MarketPanel schema - BTC-wide market context.</summary>
    public sealed class MarketPanelDto
    {
        [JsonPropertyName("btc_trend_1h")]
        public string BtcTrend1h { get; set; } = "neutral";

        [JsonPropertyName("btc_trend_4h")]
        public string BtcTrend4h { get; set; } = "neutral";

        [JsonPropertyName("market_structure")]
        public string MarketStructure { get; set; } = string.Empty;

        [JsonPropertyName("liquidity_sweep")]
        public string LiquiditySweep { get; set; } = string.Empty;

        [JsonPropertyName("funding_rate")]
        public double? FundingRate { get; set; }

        [JsonPropertyName("open_interest_trend")]
        public string OpenInterestTrend { get; set; } = "flat";

        [JsonPropertyName("fear_greed_value")]
        public int FearGreedValue { get; set; }

        [JsonPropertyName("fear_greed_classification")]
        public string FearGreedClassification { get; set; } = "Neutral";

        [JsonPropertyName("btc_dominance")]
        public double? BtcDominance { get; set; }

        [JsonPropertyName("binance_connected")]
        public bool BinanceConnected { get; set; }
    }

    /// <summary>Matches FastAPI DashboardResponse schema - the payload for GET /dashboard.</summary>
    public sealed class DashboardResponseDto
    {
        [JsonPropertyName("account")]
        public AccountPanelDto Account { get; set; } = new();

        [JsonPropertyName("ai")]
        public AiPanelDto Ai { get; set; } = new();

        [JsonPropertyName("market")]
        public MarketPanelDto Market { get; set; } = new();
    }
}
