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

    public decimal RiskReward { get; set; }

    public int Confidence { get; set; }

    public string Reason { get; set; } = string.Empty;

    public string Status { get; set; } = string.Empty;

    public string Timeframe { get; set; } = string.Empty;

    public string? AiModelVersion { get; set; }

    public DateTime CreatedAt { get; set; }

    public DateTime UpdatedAt { get; set; }

    public DateTime? ClosedAt { get; set; }

    public decimal? SuggestedRiskUsd { get; set; }

    public decimal? SuggestedQuantity { get; set; }

    public decimal? SuggestedNotionalUsd { get; set; }

    public decimal? SuggestedProfitUsd { get; set; }

    public decimal? SuggestedLossSlUsd { get; set; }

    public bool Executed { get; set; }

    public string? ExecutedOrderId { get; set; }

    public DateTime? ExecutedAt { get; set; }

    public string? ExecutedEnvironment { get; set; }

    /// <summary>What the Execute button in Auto Trading should show/do for this row.</summary>
    public string ExecuteButtonLabel => Executed ? $"Executed ({ExecutedEnvironment})" : "Execute";

    /// <summary>
    /// Null whenever no Binance account is linked (or the balance fetch
    /// failed) - shown as "No account linked" instead of a blank/zero so
    /// it's clear this isn't "$0 risk", it's "unknown". Risk $ amount is
    /// no longer repeated here since it's shown in its own Potential Loss
    /// column.
    /// </summary>
    public string PositionSizeDisplay =>
        SuggestedQuantity.HasValue
            ? $"{SuggestedQuantity.Value:0.########}"
            : "No account linked";

    /// <summary>Potential profit if the take-profit is hit - the same stop-sized position priced out at the target instead of the stop.</summary>
    public string ProfitDisplay =>
        SuggestedProfitUsd.HasValue
            ? $"+${SuggestedProfitUsd.Value:0.##} @TP"
            : "-";

    /// <summary>Potential loss if the stop loss is hit - equals the risk amount, by construction.</summary>
    public string LossDisplay =>
        SuggestedLossSlUsd.HasValue
            ? $"-${SuggestedLossSlUsd.Value:0.##} @SL"
            : "-";
}