using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace AI_Crypto_Signal_Pro.Models
{
    /// <summary>Matches FastAPI OrderHistoryItem schema.</summary>
    public sealed class OrderHistoryItemDto
    {
        [JsonPropertyName("symbol")]
        public string Symbol { get; set; } = string.Empty;

        [JsonPropertyName("side")]
        public string Side { get; set; } = string.Empty;

        [JsonPropertyName("order_type")]
        public string OrderType { get; set; } = string.Empty;

        [JsonPropertyName("price")]
        public decimal Price { get; set; }

        [JsonPropertyName("avg_price")]
        public decimal AvgPrice { get; set; }

        [JsonPropertyName("orig_qty")]
        public decimal OrigQty { get; set; }

        [JsonPropertyName("executed_qty")]
        public decimal ExecutedQty { get; set; }

        [JsonPropertyName("status")]
        public string Status { get; set; } = string.Empty;

        [JsonPropertyName("order_id")]
        public long OrderId { get; set; }

        [JsonPropertyName("time")]
        public long Time { get; set; }
    }

    /// <summary>Matches FastAPI OrderHistoryResponse schema - GET /account/order-history.</summary>
    public sealed class OrderHistoryResponseDto
    {
        [JsonPropertyName("items")]
        public List<OrderHistoryItemDto> Items { get; set; } = new();
    }

    /// <summary>Matches FastAPI IncomeHistoryItem schema.</summary>
    public sealed class IncomeHistoryItemDto
    {
        [JsonPropertyName("symbol")]
        public string? Symbol { get; set; }

        [JsonPropertyName("income_type")]
        public string IncomeType { get; set; } = string.Empty;

        [JsonPropertyName("income")]
        public decimal Income { get; set; }

        [JsonPropertyName("asset")]
        public string Asset { get; set; } = string.Empty;

        [JsonPropertyName("time")]
        public long Time { get; set; }

        [JsonPropertyName("info")]
        public string? Info { get; set; }
    }

    /// <summary>Matches FastAPI IncomeHistoryResponse schema - GET /account/income-history.</summary>
    public sealed class IncomeHistoryResponseDto
    {
        [JsonPropertyName("items")]
        public List<IncomeHistoryItemDto> Items { get; set; } = new();
    }
}
