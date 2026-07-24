using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using System.Collections.ObjectModel;

namespace AI_Crypto_Signal_Pro.ViewModels
{
    public partial class SettingsViewModel : ObservableObject
    {
        [ObservableProperty]
        private string apiKey = "";

        [ObservableProperty]
        private string secretKey = "";

        [ObservableProperty]
        private string telegramBotToken = "";

        [ObservableProperty]
        private string telegramChatId = "";

        [ObservableProperty]
        private string selectedTheme = "Dark";

        [ObservableProperty]
        private string selectedLanguage = "English";

        [ObservableProperty]
        private bool enableNotifications = true;

        [ObservableProperty]
        private bool autoTrading = false;

        [ObservableProperty]
        private string riskPercent = "2";

        [ObservableProperty]
        private string leverage = "10";

        public ObservableCollection<string> Themes { get; } =
            new() { "Dark", "Light" };

        public ObservableCollection<string> Languages { get; } =
            new() { "English", "Urdu" };

        [RelayCommand]
        private void Save()
        {
            // TODO: Save settings
        }

        [RelayCommand]
        private void Reset()
        {
            ApiKey = "";
            SecretKey = "";
            TelegramBotToken = "";
            TelegramChatId = "";
            SelectedTheme = "Dark";
            SelectedLanguage = "English";
            EnableNotifications = true;
            AutoTrading = false;
            RiskPercent = "2";
            Leverage = "10";
        }
    }
}
