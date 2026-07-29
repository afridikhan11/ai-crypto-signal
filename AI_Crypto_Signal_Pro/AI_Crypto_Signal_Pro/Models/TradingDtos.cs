using System;
using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace AI_Crypto_Signal_Pro.Models
{
    /// <summary>Empty body for the trading POST endpoints (execute/close-position) - everything they need is in the URL.</summary>
    public sealed class EmptyRequestDto
    {
    }

    /// <summary>Matches FastAPI OrderResultResponse schema (app/schemas/trading.py).</summary>
    public sealed class OrderResultDto
    {
        [JsonPropertyName("order_id")]
        public long OrderId { get; set; }

        [JsonPropertyName("symbol")]
        public string Symbol { get; set; } = string.Empty;

        [JsonPropertyName("side")]
        public string Side { get; set; } = string.Empty;

        [JsonPropertyName("order_type")]
        public string OrderType { get; set; } = string.Empty;

        [JsonPropertyName("status")]
        public string Status { get; set; } = string.Empty;

        [JsonPropertyName("quantity")]
        public decimal Quantity { get; set; }

        [JsonPropertyName("price")]
        public decimal? Price { get; set; }
    }

    /// <summary>Matches FastAPI ExecuteSignalResponse schema - response of POST /trading/execute/{signal_id}.</summary>
    public sealed class ExecuteSignalResponseDto
    {
        [JsonPropertyName("signal_id")]
        public Guid SignalId { get; set; }

        [JsonPropertyName("environment")]
        public string Environment { get; set; } = string.Empty;

        [JsonPropertyName("symbol")]
        public string Symbol { get; set; } = string.Empty;

        [JsonPropertyName("quantity")]
        public decimal Quantity { get; set; }

        [JsonPropertyName("entry_order")]
        public OrderResultDto EntryOrder { get; set; } = new();

        [JsonPropertyName("stop_loss_order")]
        public OrderResultDto? StopLossOrder { get; set; }

        [JsonPropertyName("take_profit_order")]
        public OrderResultDto? TakeProfitOrder { get; set; }

        [JsonPropertyName("warnings")]
        public List<string> Warnings { get; set; } = new();
    }

    /// <summary>Matches FastAPI ClosePositionResponse schema - response of POST /trading/close-position/{symbol}.</summary>
    public sealed class ClosePositionResponseDto
    {
        [JsonPropertyName("environment")]
        public string Environment { get; set; } = string.Empty;

        [JsonPropertyName("order")]
        public OrderResultDto Order { get; set; } = new();
    }
}
