using System;
using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace AI_Crypto_Signal_Pro.Models
{
    /// <summary>Matches FastAPI ConfidenceBucketStat schema (app/schemas/performance.py).
    /// WinRatePct is null (not 0%) for a bucket with zero trades - never fabricated.</summary>
    public sealed class ConfidenceBucketStatDto
    {
        [JsonPropertyName("bucket_label")]
        public string BucketLabel { get; set; } = string.Empty;

        [JsonPropertyName("trade_count")]
        public int TradeCount { get; set; }

        [JsonPropertyName("wins")]
        public int Wins { get; set; }

        [JsonPropertyName("losses")]
        public int Losses { get; set; }

        [JsonPropertyName("win_rate_pct")]
        public double? WinRatePct { get; set; }
    }

    /// <summary>Matches FastAPI SymbolPerformanceStat schema.</summary>
    public sealed class SymbolPerformanceStatDto
    {
        [JsonPropertyName("symbol")]
        public string Symbol { get; set; } = string.Empty;

        [JsonPropertyName("trade_count")]
        public int TradeCount { get; set; }

        [JsonPropertyName("wins")]
        public int Wins { get; set; }

        [JsonPropertyName("losses")]
        public int Losses { get; set; }

        [JsonPropertyName("win_rate_pct")]
        public double? WinRatePct { get; set; }

        [JsonPropertyName("avg_confidence")]
        public double? AvgConfidence { get; set; }

        [JsonPropertyName("avg_risk_reward")]
        public double? AvgRiskReward { get; set; }
    }

    /// <summary>Matches FastAPI AssetClassPerformanceStat schema.</summary>
    public sealed class AssetClassPerformanceStatDto
    {
        [JsonPropertyName("asset_class")]
        public string AssetClass { get; set; } = string.Empty;

        [JsonPropertyName("trade_count")]
        public int TradeCount { get; set; }

        [JsonPropertyName("wins")]
        public int Wins { get; set; }

        [JsonPropertyName("losses")]
        public int Losses { get; set; }

        [JsonPropertyName("win_rate_pct")]
        public double? WinRatePct { get; set; }
    }

    /// <summary>Matches FastAPI CalibrationHealthItem schema - one row per registered
    /// asset-type profile (crypto/gold/silver/oil/forex), a read-only status report that
    /// never triggers a recalibration itself.</summary>
    public sealed class CalibrationHealthItemDto
    {
        [JsonPropertyName("asset_type")]
        public string AssetType { get; set; } = string.Empty;

        [JsonPropertyName("wins_collected")]
        public int WinsCollected { get; set; }

        [JsonPropertyName("losses_collected")]
        public int LossesCollected { get; set; }

        [JsonPropertyName("min_required_per_group")]
        public int MinRequiredPerGroup { get; set; }

        [JsonPropertyName("is_calibrated")]
        public bool IsCalibrated { get; set; }

        [JsonPropertyName("ready_to_calibrate")]
        public bool ReadyToCalibrate { get; set; }
    }

    /// <summary>
    /// Matches FastAPI AIPerformanceOverviewResponse schema - the payload for
    /// GET /performance/overview. DateFrom/DateTo (Phase 8) echo back whichever
    /// optional start_date/end_date query params were sent - null means full history,
    /// unchanged from pre-Phase-8 behavior. CalibrationHealth always reflects full
    /// history regardless of the date filter (see the backend's own docstring -
    /// calibration readiness is a lifetime concept, not a recent-window one).
    /// </summary>
    public sealed class AiPerformanceOverviewDto
    {
        [JsonPropertyName("generated_at")]
        public DateTime GeneratedAt { get; set; }

        [JsonPropertyName("stats")]
        public StatsDto Stats { get; set; } = new();

        [JsonPropertyName("date_from")]
        public DateTime? DateFrom { get; set; }

        [JsonPropertyName("date_to")]
        public DateTime? DateTo { get; set; }

        [JsonPropertyName("confidence_buckets")]
        public List<ConfidenceBucketStatDto> ConfidenceBuckets { get; set; } = new();

        [JsonPropertyName("by_symbol")]
        public List<SymbolPerformanceStatDto> BySymbol { get; set; } = new();

        [JsonPropertyName("by_asset_class")]
        public List<AssetClassPerformanceStatDto> ByAssetClass { get; set; } = new();

        [JsonPropertyName("calibration_health")]
        public List<CalibrationHealthItemDto> CalibrationHealth { get; set; } = new();
    }

    /// <summary>Matches FastAPI TradeJournalEntry schema. Reason is copied VERBATIM from
    /// the stored signal - never rephrased or summarized by this screen.</summary>
    public sealed class TradeJournalEntryDto
    {
        [JsonPropertyName("id")]
        public Guid Id { get; set; }

        [JsonPropertyName("symbol")]
        public string Symbol { get; set; } = string.Empty;

        [JsonPropertyName("direction")]
        public string Direction { get; set; } = string.Empty;

        [JsonPropertyName("entry_price")]
        public double EntryPrice { get; set; }

        [JsonPropertyName("stop_loss")]
        public double StopLoss { get; set; }

        [JsonPropertyName("take_profit")]
        public double TakeProfit { get; set; }

        [JsonPropertyName("exit_price")]
        public double? ExitPrice { get; set; }

        [JsonPropertyName("confidence")]
        public int Confidence { get; set; }

        [JsonPropertyName("risk_reward")]
        public double RiskReward { get; set; }

        [JsonPropertyName("outcome")]
        public string Outcome { get; set; } = string.Empty;

        [JsonPropertyName("reason")]
        public string Reason { get; set; } = string.Empty;

        [JsonPropertyName("timeframe")]
        public string Timeframe { get; set; } = string.Empty;

        [JsonPropertyName("opened_at")]
        public DateTime OpenedAt { get; set; }

        [JsonPropertyName("closed_at")]
        public DateTime? ClosedAt { get; set; }

        [JsonPropertyName("held_duration_hours")]
        public double? HeldDurationHours { get; set; }
    }

    /// <summary>Matches FastAPI TradeJournalResponse schema - the payload for
    /// GET /performance/journal. Reuses the existing History module's pagination shape.</summary>
    public sealed class TradeJournalResponseDto
    {
        [JsonPropertyName("total")]
        public int Total { get; set; }

        [JsonPropertyName("page")]
        public int Page { get; set; }

        [JsonPropertyName("page_size")]
        public int PageSize { get; set; }

        [JsonPropertyName("entries")]
        public List<TradeJournalEntryDto> Entries { get; set; } = new();
    }
}
