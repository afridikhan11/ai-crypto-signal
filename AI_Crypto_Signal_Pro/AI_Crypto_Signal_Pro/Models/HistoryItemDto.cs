using System;
using System.Text.Json.Serialization;

namespace AI_Crypto_Signal_Pro.Models
{
    /// <summary>
    /// Matches FastAPI HistoryItemResponse schema (app/schemas/history.py).
    /// </summary>
    public sealed class HistoryItemDto
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

        [JsonPropertyName("result")]
        public string Result { get; set; } = string.Empty;

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
    }
}
