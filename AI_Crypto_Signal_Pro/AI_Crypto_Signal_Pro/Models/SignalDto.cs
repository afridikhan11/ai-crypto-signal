using System;
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

        [JsonPropertyName("take_profit_1")]
        public decimal TakeProfit1 { get; set; }

        [JsonPropertyName("take_profit_2")]
        public decimal TakeProfit2 { get; set; }

        [JsonPropertyName("take_profit_3")]
        public decimal TakeProfit3 { get; set; }

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

        [JsonPropertyName("session")]
        public string? Session { get; set; }

        [JsonPropertyName("htf_bias")]
        public string? HtfBias { get; set; }

        [JsonPropertyName("bias_strength")]
        public double? BiasStrength { get; set; }

        [JsonPropertyName("max_tp_hit")]
        public int MaxTpHit { get; set; }

        [JsonPropertyName("created_at")]
        public DateTime CreatedAt { get; set; }

        [JsonPropertyName("updated_at")]
        public DateTime UpdatedAt { get; set; }

        [JsonPropertyName("closed_at")]
        public DateTime? ClosedAt { get; set; }
    }
}
