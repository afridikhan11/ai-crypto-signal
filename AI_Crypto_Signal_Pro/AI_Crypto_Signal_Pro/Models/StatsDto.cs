using System.Text.Json.Serialization;

namespace AI_Crypto_Signal_Pro.Models
{
    /// <summary>
    /// Matches FastAPI StatsResponse schema.
    /// </summary>
    public sealed class StatsDto
    {
        [JsonPropertyName("total_signals")]
        public int TotalSignals { get; set; }

        [JsonPropertyName("active_signals")]
        public int ActiveSignals { get; set; }

        [JsonPropertyName("closed_signals")]
        public int ClosedSignals { get; set; }

        [JsonPropertyName("win_rate")]
        public double WinRate { get; set; }

        [JsonPropertyName("avg_confidence")]
        public double AvgConfidence { get; set; }

        [JsonPropertyName("avg_risk_reward")]
        public double AvgRiskReward { get; set; }

        [JsonPropertyName("best_performing_symbol")]
        public string BestPerformingSymbol { get; set; } = string.Empty;
    }
}
