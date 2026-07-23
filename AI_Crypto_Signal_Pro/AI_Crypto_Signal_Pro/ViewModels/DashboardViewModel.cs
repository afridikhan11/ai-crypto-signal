using CommunityToolkit.Mvvm.ComponentModel;

namespace AI_Crypto_Signal_Pro.ViewModels;

public partial class DashboardViewModel : ObservableObject
{
    [ObservableProperty]
    private string marketTrend = "Bullish";

    [ObservableProperty]
    private int aiConfidence = 96;

    [ObservableProperty]
    private int activeSignals = 12;

    [ObservableProperty]
    private string btcPrice = "$118,250";

    [ObservableProperty]
    private string ethPrice = "$3,820";
}
