using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace AI_Crypto_Signal_Pro.Models
{
    /// <summary>
    /// Matches FastAPI SignalListResponse schema.
    /// </summary>
    public sealed class SignalListResponseDto
    {
        [JsonPropertyName("total")]
        public int Total { get; set; }

        [JsonPropertyName("page")]
        public int Page { get; set; }

        [JsonPropertyName("page_size")]
        public int PageSize { get; set; }

        [JsonPropertyName("items")]
        public List<SignalDto> Items { get; set; } = new();
    }
}
