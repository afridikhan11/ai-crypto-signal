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

    // ---- ICT Pending Limit Entry (2026-07-30) ----
    public string? EntryType { get; set; }

    public decimal? EntryZoneTop { get; set; }

    public decimal? EntryZoneBottom { get; set; }

    public DateTime? EntryExpiresAt { get; set; }

    public DateTime? FilledAt { get; set; }

    public decimal? ActualFillPrice { get; set; }

    public string? EntryOrderId { get; set; }

    /// <summary>True while the ICT entry zone has been identified but price has not traded into it yet - no position exists.</summary>
    public bool IsPendingEntry => string.Equals(Status, "PENDING_ENTRY", StringComparison.OrdinalIgnoreCase);

    /// <summary>True when the setup was abandoned without ever being entered (window elapsed, or invalidated before fill).</summary>
    public bool IsExpired => string.Equals(Status, "EXPIRED", StringComparison.OrdinalIgnoreCase);

    /// <summary>
    /// What the Execute button in Auto Trading should show/do for this row.
    /// An ARMED pending entry (executed, but not yet filled) is deliberately
    /// distinguished from a filled position: "Armed" means a LIMIT order is
    /// resting at the entry price and no position exists yet, which is a
    /// materially different state for the user than "Executed".
    /// </summary>
    public string ExecuteButtonLabel =>
        Executed
            ? (IsPendingEntry ? $"Armed ({ExecutedEnvironment})" : $"Executed ({ExecutedEnvironment})")
            : (IsPendingEntry ? "Arm Entry" : "Execute");

    /// <summary>Human-readable state of the ICT entry, for the Entry column / status chip.</summary>
    public string EntryStateDisplay
    {
        get
        {
            if (IsExpired)
                return "Never entered";
            if (IsPendingEntry)
                return Executed
                    ? $"Armed @ {Entry:0.########} ({EntryType})"
                    : $"Waiting @ {Entry:0.########} ({EntryType})";
            if (ActualFillPrice.HasValue)
                return $"Filled @ {ActualFillPrice.Value:0.########}";
            return $"{Entry:0.########}";
        }
    }

    private const string ColorPending = "#FFB74D";
    private const string ColorActive = "#00E676";
    private const string ColorExpired = "#9E9E9E";
    private const string ColorClosed = "#64B5F6";

    /// <summary>
    /// Status chip colour. Hex strings bound directly to a Brush property,
    /// matching the existing MainWindowViewModel.BackendStatusColor /
    /// TradingStatusDto convention already used across this app.
    /// </summary>
    public string StatusColor => Status?.ToUpperInvariant() switch
    {
        "PENDING_ENTRY" => ColorPending,
        "ACTIVE" => ColorActive,
        "EXPIRED" => ColorExpired,
        _ => ColorClosed,
    };

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