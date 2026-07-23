namespace AI_Crypto_Signal_Pro.Models;

public class SignalModel
{
    public string Coin { get; set; } = string.Empty;

    public string Direction { get; set; } = string.Empty;

    public decimal Entry { get; set; }

    public decimal StopLoss { get; set; }

    public decimal TakeProfit { get; set; }

    public int Confidence { get; set; }

    public string Status { get; set; } = "Waiting";
}
