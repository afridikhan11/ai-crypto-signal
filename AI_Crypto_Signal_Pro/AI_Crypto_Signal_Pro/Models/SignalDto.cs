using System;
using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace AI_Crypto_Signal_Pro.Models
{
    /// <summary>
    /// Matches FastAPI SignalResponse schema.
    /// </summary>
    public sealed class SignalDto
    {
        [JsonPropertyName("id")]
        public Guid Id { get; set; }

        [JsonPropertyName("coin_id")]
        public Guid CoinId { get; set; }

        [JsonPropertyName("coin_symbol")]
        public string CoinSymbol { get; set; } = string.Empty;

        [JsonPropertyName("direction")]
        public string Direction { get; set; } = string.Empty;

        [JsonPropertyName("entry_price")]
        public decimal EntryPrice { get; set; }

        [JsonPropertyName("stop_loss")]
        public decimal StopLoss { get; set; }

        [JsonPropertyName("take_profit")]
        public decimal TakeProfit { get; set; }

        [JsonPropertyName("risk_reward")]
        public decimal RiskReward { get; set; }

        [JsonPropertyName("confidence")]
        public int Confidence { get; set; }

        [JsonPropertyName("reason")]
        public string Reason { get; set; } = string.Empty;

        [JsonPropertyName("status")]
        public string Status { get; set; } = string.Empty;

        [JsonPropertyName("timeframe")]
        public string Timeframe { get; set; } = string.Empty;

        [JsonPropertyName("ai_model_version")]
        public string? AiModelVersion { get; set; }

        [JsonPropertyName("created_at")]
        public DateTime CreatedAt { get; set; }

        [JsonPropertyName("updated_at")]
        public DateTime UpdatedAt { get; set; }

        [JsonPropertyName("closed_at")]
        public DateTime? ClosedAt { get; set; }

        [JsonPropertyName("suggested_risk_usd")]
        public decimal? SuggestedRiskUsd { get; set; }

        [JsonPropertyName("suggested_quantity")]
        public decimal? SuggestedQuantity { get; set; }

        [JsonPropertyName("suggested_notional_usd")]
        public decimal? SuggestedNotionalUsd { get; set; }

        [JsonPropertyName("suggested_profit_usd")]
        public decimal? SuggestedProfitUsd { get; set; }

        [JsonPropertyName("suggested_loss_sl_usd")]
        public decimal? SuggestedLossSlUsd { get; set; }

        [JsonPropertyName("correlation_warning")]
        public string? CorrelationWarning { get; set; }

        [JsonPropertyName("risk_approved")]
        public bool? RiskApproved { get; set; }

        [JsonPropertyName("risk_reasons")]
        public List<string> RiskReasons { get; set; } = new();

        [JsonPropertyName("portfolio_open_risk_percent")]
        public double? PortfolioOpenRiskPercent { get; set; }

        [JsonPropertyName("portfolio_exposure_percent")]
        public double? PortfolioExposurePercent { get; set; }

        [JsonPropertyName("executed")]
        public bool Executed { get; set; }

        [JsonPropertyName("executed_order_id")]
        public string? ExecutedOrderId { get; set; }

        [JsonPropertyName("executed_at")]
        public DateTime? ExecutedAt { get; set; }

        [JsonPropertyName("executed_environment")]
        public string? ExecutedEnvironment { get; set; }

        // ---- ICT Pending Limit Entry (2026-07-30) ----
        // Under entry_mode "ict_pending" a signal is created PENDING_ENTRY at
        // the ICT anchor price and becomes ACTIVE only once price actually
        // trades into the zone. These mirror the backend SignalResponse
        // fields exactly - see app/schemas/signal.py.

        /// <summary>Which ICT anchor priced this entry: ote | order_block | fvg | supply_demand | limit | market.</summary>
        [JsonPropertyName("entry_type")]
        public string? EntryType { get; set; }

        /// <summary>Top of the ICT entry zone. Price trading anywhere inside the zone fills the entry.</summary>
        [JsonPropertyName("entry_zone_top")]
        public decimal? EntryZoneTop { get; set; }

        [JsonPropertyName("entry_zone_bottom")]
        public decimal? EntryZoneBottom { get; set; }

        /// <summary>When an unfilled pending entry is abandoned (never entered).</summary>
        [JsonPropertyName("entry_expires_at")]
        public DateTime? EntryExpiresAt { get; set; }

        /// <summary>When the entry actually filled. Null while still PENDING_ENTRY.</summary>
        [JsonPropertyName("filled_at")]
        public DateTime? FilledAt { get; set; }

        /// <summary>The real fill price - equals EntryPrice for a LIMIT entry by definition.</summary>
        [JsonPropertyName("actual_fill_price")]
        public decimal? ActualFillPrice { get; set; }

        /// <summary>The resting LIMIT entry order on Binance, when Auto Trading has armed this entry.</summary>
        [JsonPropertyName("entry_order_id")]
        public string? EntryOrderId { get; set; }
    }
}
