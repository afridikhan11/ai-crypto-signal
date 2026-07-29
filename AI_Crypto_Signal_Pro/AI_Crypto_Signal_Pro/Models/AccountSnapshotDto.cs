using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace AI_Crypto_Signal_Pro.Models
{
    /// <summary>Matches FastAPI AccountInfo schema (app/services/binance_account_service.py).</summary>
    public sealed class AccountInfoDto
    {
        [JsonPropertyName("account_type")]
        public string AccountType { get; set; } = string.Empty;

        [JsonPropertyName("account_status")]
        public string AccountStatus { get; set; } = string.Empty;

        [JsonPropertyName("can_trade")]
        public bool CanTrade { get; set; }

        [JsonPropertyName("can_withdraw")]
        public bool CanWithdraw { get; set; }

        [JsonPropertyName("can_deposit")]
        public bool CanDeposit { get; set; }

        [JsonPropertyName("buyer_commission")]
        public double? BuyerCommission { get; set; }

        [JsonPropertyName("seller_commission")]
        public double? SellerCommission { get; set; }

        [JsonPropertyName("update_time")]
        public long? UpdateTime { get; set; }
    }

    /// <summary>Matches FastAPI AssetBalance schema.</summary>
    public sealed class AssetBalanceDto
    {
        [JsonPropertyName("asset")]
        public string Asset { get; set; } = string.Empty;

        [JsonPropertyName("free")]
        public decimal Free { get; set; }

        [JsonPropertyName("locked")]
        public decimal Locked { get; set; }

        [JsonPropertyName("total")]
        public decimal Total { get; set; }

        [JsonPropertyName("value_usdt")]
        public decimal? ValueUsdt { get; set; }
    }

    /// <summary>Matches FastAPI FuturesPosition schema.</summary>
    public sealed class FuturesPositionDto
    {
        [JsonPropertyName("symbol")]
        public string Symbol { get; set; } = string.Empty;

        [JsonPropertyName("position_amt")]
        public decimal PositionAmt { get; set; }

        [JsonPropertyName("entry_price")]
        public decimal EntryPrice { get; set; }

        [JsonPropertyName("unrealized_pnl")]
        public decimal UnrealizedPnl { get; set; }

        [JsonPropertyName("leverage")]
        public int Leverage { get; set; }

        [JsonPropertyName("margin_type")]
        public string? MarginType { get; set; }

        [JsonPropertyName("position_side")]
        public string? PositionSide { get; set; }

        [JsonPropertyName("mark_price")]
        public decimal? MarkPrice { get; set; }

        [JsonPropertyName("liquidation_price")]
        public decimal? LiquidationPrice { get; set; }

        [JsonPropertyName("break_even_price")]
        public decimal? BreakEvenPrice { get; set; }

        [JsonPropertyName("margin")]
        public decimal? Margin { get; set; }

        [JsonPropertyName("roi_percent")]
        public decimal? RoiPercent { get; set; }
    }

    /// <summary>Matches FastAPI FuturesAccountInfo schema.</summary>
    public sealed class FuturesAccountInfoDto
    {
        [JsonPropertyName("wallet_balance")]
        public decimal WalletBalance { get; set; }

        [JsonPropertyName("available_balance")]
        public decimal AvailableBalance { get; set; }

        [JsonPropertyName("unrealized_pnl")]
        public decimal UnrealizedPnl { get; set; }

        [JsonPropertyName("margin_balance")]
        public decimal MarginBalance { get; set; }

        [JsonPropertyName("total_maintenance_margin")]
        public decimal? TotalMaintenanceMargin { get; set; }

        [JsonPropertyName("open_positions")]
        public List<FuturesPositionDto> OpenPositions { get; set; } = new();
    }

    /// <summary>Matches FastAPI TradeRecord schema.</summary>
    public sealed class TradeRecordDto
    {
        [JsonPropertyName("market")]
        public string Market { get; set; } = string.Empty;

        [JsonPropertyName("symbol")]
        public string Symbol { get; set; } = string.Empty;

        [JsonPropertyName("side")]
        public string Side { get; set; } = string.Empty;

        [JsonPropertyName("quantity")]
        public decimal Quantity { get; set; }

        [JsonPropertyName("entry_price")]
        public decimal EntryPrice { get; set; }

        [JsonPropertyName("exit_price")]
        public decimal? ExitPrice { get; set; }

        [JsonPropertyName("realized_pnl")]
        public decimal? RealizedPnl { get; set; }

        [JsonPropertyName("commission")]
        public decimal Commission { get; set; }

        [JsonPropertyName("commission_asset")]
        public string? CommissionAsset { get; set; }

        [JsonPropertyName("time")]
        public long Time { get; set; }
    }

    /// <summary>Matches FastAPI OpenOrderInfo schema.</summary>
    public sealed class OpenOrderInfoDto
    {
        [JsonPropertyName("market")]
        public string Market { get; set; } = string.Empty;

        [JsonPropertyName("symbol")]
        public string Symbol { get; set; } = string.Empty;

        [JsonPropertyName("side")]
        public string Side { get; set; } = string.Empty;

        [JsonPropertyName("price")]
        public decimal Price { get; set; }

        [JsonPropertyName("quantity")]
        public decimal Quantity { get; set; }

        [JsonPropertyName("status")]
        public string Status { get; set; } = string.Empty;

        [JsonPropertyName("order_id")]
        public long OrderId { get; set; }

        [JsonPropertyName("time")]
        public long Time { get; set; }
    }

    /// <summary>Matches FastAPI AccountSnapshot schema - the payload for GET /account/snapshot.</summary>
    public sealed class AccountSnapshotDto
    {
        [JsonPropertyName("environment")]
        public string Environment { get; set; } = string.Empty;

        [JsonPropertyName("account")]
        public AccountInfoDto? Account { get; set; }

        [JsonPropertyName("balances")]
        public List<AssetBalanceDto> Balances { get; set; } = new();

        [JsonPropertyName("total_wallet_value_usdt")]
        public decimal? TotalWalletValueUsdt { get; set; }

        [JsonPropertyName("futures")]
        public FuturesAccountInfoDto? Futures { get; set; }

        [JsonPropertyName("trade_history")]
        public List<TradeRecordDto> TradeHistory { get; set; } = new();

        [JsonPropertyName("open_orders")]
        public List<OpenOrderInfoDto> OpenOrders { get; set; } = new();

        [JsonPropertyName("warnings")]
        public List<string> Warnings { get; set; } = new();
    }
}
