using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace AI_Crypto_Signal_Pro.Models
{
    /// <summary>
    /// Matches FastAPI HistoryListResponse schema (app/schemas/history.py).
    /// </summary>
    public sealed class HistoryListResponseDto
    {
        [JsonPropertyName("total")]
        public int Total { get; set; }

        [JsonPropertyName("page")]
        public int Page { get; set; }

        [JsonPropertyName("page_size")]
        public int PageSize { get; set; }

        [JsonPropertyName("items")]
        public List<HistoryItemDto> Items { get; set; } = new();
    }
}
