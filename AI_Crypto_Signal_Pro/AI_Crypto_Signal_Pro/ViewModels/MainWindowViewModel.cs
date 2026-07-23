using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;

namespace AI_Crypto_Signal_Pro.ViewModels;

public partial class MainWindowViewModel : ObservableObject
{
    [ObservableProperty]
    private string currentPageTitle = "Dashboard";

    [RelayCommand]
    private void ShowDashboard()
    {
        CurrentPageTitle = "Dashboard";
    }

    [RelayCommand]
    private void ShowLiveSignals()
    {
        CurrentPageTitle = "Live Signals";
    }

    [RelayCommand]
    private void ShowMarket()
    {
        CurrentPageTitle = "Market";
    }

    [RelayCommand]
    private void ShowHistory()
    {
        CurrentPageTitle = "History";
    }

    [RelayCommand]
    private void ShowSettings()
    {
        CurrentPageTitle = "Settings";
    }
}
