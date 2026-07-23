using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using AI_Crypto_Signal_Pro.Models;

namespace AI_Crypto_Signal_Pro.ViewModels;

public partial class LiveSignalsViewModel : ObservableObject
{
    public ObservableCollection<SignalModel> Signals { get; } = new();

    public LiveSignalsViewModel()
    {
        Signals.Add(new SignalModel
        {
            Coin = "BTCUSDT",
            Direction = "LONG",
            Entry = 118250,
            StopLoss = 117600,
            TakeProfit = 119800,
            Confidence = 96,
            Status = "ACTIVE"
        });

        Signals.Add(new SignalModel
        {
            Coin = "ETHUSDT",
            Direction = "SHORT",
            Entry = 3820,
            StopLoss = 3865,
            TakeProfit = 3740,
            Confidence = 91,
            Status = "WAITING"
        });
    }
}
