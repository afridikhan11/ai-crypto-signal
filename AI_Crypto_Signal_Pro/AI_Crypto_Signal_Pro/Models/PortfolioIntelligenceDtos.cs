using System;
using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace AI_Crypto_Signal_Pro.Models
{
    /// <summary>Matches FastAPI PortfolioRiskItem schema (app/schemas/signal.py) - existing
    /// Phase 1 model, reused unchanged by Portfolio Intelligence.</summary>
    public sealed class PortfolioRiskItemDto
    {
        [JsonPropertyName("coin_symbol")]
        public string CoinSymbol { get; set; } = string.Empty;

        [JsonPropertyName("direction")]
        public string Direction { get; set; } = string.Empty;

        [JsonPropertyName("risk_usd")]
        public double RiskUsd { get; set; }
    }

    /// <summary>Matches FastAPI PortfolioRiskResponse schema - existing Phase 1 model,
    /// reused unchanged by Portfolio Intelligence. Advisory only.</summary>
    public sealed class PortfolioRiskDto
    {
        [JsonPropertyName("active_signal_count")]
        public int ActiveSignalCount { get; set; }

        [JsonPropertyName("account_balance")]
        public double? AccountBalance { get; set; }

        [JsonPropertyName("total_risk_usd")]
        public double? TotalRiskUsd { get; set; }

        [JsonPropertyName("total_risk_percent_of_balance")]
        public double? TotalRiskPercentOfBalance { get; set; }

        [JsonPropertyName("items")]
        public List<PortfolioRiskItemDto> Items { get; set; } = new();

        [JsonPropertyName("message")]
        public string? Message { get; set; }
    }

    /// <summary>Matches FastAPI DailyLossResponse schema - existing Phase 1 model,
    /// reused unchanged by Portfolio Intelligence. Advisory only, never auto-pauses anything.</summary>
    public sealed class DailyLossDto
    {
        [JsonPropertyName("period_start")]
        public DateTime PeriodStart { get; set; }

        [JsonPropertyName("account_balance")]
        public double? AccountBalance { get; set; }

        [JsonPropertyName("realized_loss_usd")]
        public double RealizedLossUsd { get; set; }

        [JsonPropertyName("realized_pnl_usd")]
        public double RealizedPnlUsd { get; set; }

        [JsonPropertyName("loss_percent_of_balance")]
        public double? LossPercentOfBalance { get; set; }

        [JsonPropertyName("warning_threshold_percent")]
        public double WarningThresholdPercent { get; set; }

        [JsonPropertyName("breached")]
        public bool Breached { get; set; }

        [JsonPropertyName("closed_trade_count")]
        public int ClosedTradeCount { get; set; }

        [JsonPropertyName("message")]
        public string? Message { get; set; }
    }

    /// <summary>Matches FastAPI ExposureItem schema (app/schemas/portfolio.py). `Source`
    /// on the parent ExposureAnalysisDto tells you which of the Binance-live vs.
    /// active-signal fields below are actually populated - never blended or guessed.</summary>
    public sealed class ExposureItemDto
    {
        [JsonPropertyName("symbol")]
        public string Symbol { get; set; } = string.Empty;

        [JsonPropertyName("asset_class")]
        public string AssetClass { get; set; } = string.Empty;

        [JsonPropertyName("asset_type")]
        public string AssetType { get; set; } = string.Empty;

        [JsonPropertyName("direction")]
        public string Direction { get; set; } = string.Empty;

        [JsonPropertyName("notional_usd")]
        public double? NotionalUsd { get; set; }

        [JsonPropertyName("margin_usd")]
        public double? MarginUsd { get; set; }

        [JsonPropertyName("unrealized_pnl_usd")]
        public double? UnrealizedPnlUsd { get; set; }

        [JsonPropertyName("risk_usd")]
        public double? RiskUsd { get; set; }

        [JsonPropertyName("weight_pct")]
        public double WeightPct { get; set; }

        [JsonPropertyName("current_decision")]
        public string? CurrentDecision { get; set; }

        [JsonPropertyName("current_confidence")]
        public int? CurrentConfidence { get; set; }

        [JsonPropertyName("decision_note")]
        public string? DecisionNote { get; set; }
    }

    /// <summary>Matches FastAPI ExposureAnalysisResponse schema.</summary>
    public sealed class ExposureAnalysisDto
    {
        [JsonPropertyName("source")]
        public string Source { get; set; } = string.Empty;

        [JsonPropertyName("account_balance")]
        public double? AccountBalance { get; set; }

        [JsonPropertyName("total_exposure_usd")]
        public double? TotalExposureUsd { get; set; }

        [JsonPropertyName("items")]
        public List<ExposureItemDto> Items { get; set; } = new();

        [JsonPropertyName("by_asset_class_pct")]
        public Dictionary<string, double> ByAssetClassPct { get; set; } = new();

        [JsonPropertyName("by_direction_pct")]
        public Dictionary<string, double> ByDirectionPct { get; set; } = new();

        [JsonPropertyName("message")]
        public string? Message { get; set; }
    }

    /// <summary>Matches FastAPI CorrelationPair schema.</summary>
    public sealed class CorrelationPairDto
    {
        [JsonPropertyName("symbol_a")]
        public string SymbolA { get; set; } = string.Empty;

        [JsonPropertyName("symbol_b")]
        public string SymbolB { get; set; } = string.Empty;

        [JsonPropertyName("correlation")]
        public double Correlation { get; set; }

        [JsonPropertyName("same_direction")]
        public bool SameDirection { get; set; }

        [JsonPropertyName("stacked_risk")]
        public bool StackedRisk { get; set; }
    }

    /// <summary>Matches FastAPI CorrelationAnalysisResponse schema.</summary>
    public sealed class CorrelationAnalysisDto
    {
        [JsonPropertyName("available")]
        public bool Available { get; set; }

        [JsonPropertyName("reason")]
        public string? Reason { get; set; }

        [JsonPropertyName("threshold")]
        public double? Threshold { get; set; }

        [JsonPropertyName("symbols_checked")]
        public List<string> SymbolsChecked { get; set; } = new();

        [JsonPropertyName("pairs")]
        public List<CorrelationPairDto> Pairs { get; set; } = new();

        [JsonPropertyName("stacked_risk_pairs")]
        public List<CorrelationPairDto> StackedRiskPairs { get; set; } = new();

        [JsonPropertyName("message")]
        public string? Message { get; set; }
    }

    /// <summary>Matches FastAPI DiversificationResponse schema - standard Herfindahl-Hirschman
    /// Index over exposure weights already computed above, nothing new invented here.</summary>
    public sealed class DiversificationDto
    {
        [JsonPropertyName("available")]
        public bool Available { get; set; }

        [JsonPropertyName("reason")]
        public string? Reason { get; set; }

        [JsonPropertyName("hhi_by_symbol")]
        public double? HhiBySymbol { get; set; }

        [JsonPropertyName("hhi_by_asset_class")]
        public double? HhiByAssetClass { get; set; }

        [JsonPropertyName("concentration_label_by_symbol")]
        public string? ConcentrationLabelBySymbol { get; set; }

        [JsonPropertyName("concentration_label_by_asset_class")]
        public string? ConcentrationLabelByAssetClass { get; set; }

        [JsonPropertyName("largest_symbol")]
        public string? LargestSymbol { get; set; }

        [JsonPropertyName("largest_symbol_weight_pct")]
        public double? LargestSymbolWeightPct { get; set; }

        [JsonPropertyName("largest_asset_class")]
        public string? LargestAssetClass { get; set; }

        [JsonPropertyName("largest_asset_class_weight_pct")]
        public double? LargestAssetClassWeightPct { get; set; }

        [JsonPropertyName("message")]
        public string? Message { get; set; }
    }

    /// <summary>
    /// Matches FastAPI PortfolioIntelligenceResponse schema - the payload for
    /// GET /portfolio/intelligence. Risk/DailyLoss are the existing, unmodified Phase 1
    /// responses; Exposure/Correlation/Diversification are Phase 7 additions, all built
    /// only from real account/signal data - see app/services/portfolio_intelligence.py.
    /// </summary>
    public sealed class PortfolioIntelligenceDto
    {
        [JsonPropertyName("generated_at")]
        public DateTime GeneratedAt { get; set; }

        [JsonPropertyName("risk")]
        public PortfolioRiskDto Risk { get; set; } = new();

        [JsonPropertyName("daily_loss")]
        public DailyLossDto DailyLoss { get; set; } = new();

        [JsonPropertyName("exposure")]
        public ExposureAnalysisDto Exposure { get; set; } = new();

        [JsonPropertyName("correlation")]
        public CorrelationAnalysisDto Correlation { get; set; } = new();

        [JsonPropertyName("diversification")]
        public DiversificationDto Diversification { get; set; } = new();
    }
}
