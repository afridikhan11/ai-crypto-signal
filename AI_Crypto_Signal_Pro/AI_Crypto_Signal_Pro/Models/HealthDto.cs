using System.Text.Json.Serialization;

namespace AI_Crypto_Signal_Pro.Models
{
    /// <summary>Matches FastAPI GET /api/v1/health response.</summary>
    public sealed class HealthDto
    {
        [JsonPropertyName("status")]
        public string Status { get; set; } = string.Empty;

        [JsonPropertyName("database")]
        public string Database { get; set; } = string.Empty;

        [JsonPropertyName("redis")]
        public string Redis { get; set; } = string.Empty;

        [JsonPropertyName("binance")]
        public string Binance { get; set; } = string.Empty;
    }
}
