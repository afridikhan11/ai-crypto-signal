namespace AI_Crypto_Signal_Pro.Models;

public class SignalModel
{
    public Guid Id { get; set; }

    public Guid CoinId { get; set; }

    public string Coin { get; set; } = string.Empty;

    public string Direction { get; set; } = string.Empty;

    public decimal Entry { get; set; }

    public decimal StopLoss { get; set; }

    public decimal TakeProfit { get; set; }

    public decimal TakeProfit2 { get; set; }

    public decimal TakeProfit3 { get; set; }

    public decimal RiskReward { get; set; }

    public int Confidence { get; set; }

    public string Reason { get; set; } = string.Empty;

    public string Status { get; set; } = string.Empty;

    public string Timeframe { get; set; } = string.Empty;

    public string? AiModelVersion { get; set; }

    // ICT context (from the multi-timeframe backend)
    public string? Session { get; set; }

    public string? HtfBias { get; set; }

    public double? BiasStrength { get; set; }

    // Highest take-profit reached so far (0-3)
    public int MaxTpHit { get; set; }

    public DateTime CreatedAt { get; set; }

    public DateTime UpdatedAt { get; set; }

    public DateTime? ClosedAt { get; set; }
}