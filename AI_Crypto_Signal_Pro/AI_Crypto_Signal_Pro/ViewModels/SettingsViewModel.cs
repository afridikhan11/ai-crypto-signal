using System;
using System.Collections.ObjectModel;
using System.IO;
using System.Text.Json;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using AI_Crypto_Signal_Pro.Models;
using AI_Crypto_Signal_Pro.Services;

namespace AI_Crypto_Signal_Pro.ViewModels
{
    public partial class SettingsViewModel : ObservableObject
    {
        private readonly ApiService _apiService = new();

        private static readonly string SettingsFilePath = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
            "AI_Crypto_Signal_Pro",
            "settings.json");

        // Binance API Key/Secret are NEVER written to the local plaintext settings
        // file - Save sends them straight to the backend, which encrypts them at
        // rest (app.security.api_key_cipher.ApiKeyCipher) via POST /account/credentials.
        [ObservableProperty]
        private string apiKey = "";

        [ObservableProperty]
        private string secretKey = "";

        [ObservableProperty]
        private bool testnet = false;

        [ObservableProperty]
        private bool hasSavedCredentials;

        [ObservableProperty]
        private string? savedCredentialsEnvironment;

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

        [ObservableProperty]
        private bool isSaving;

        [ObservableProperty]
        private string? statusMessage;

        public ObservableCollection<string> Themes { get; } =
            new() { "Dark", "Light" };

        public ObservableCollection<string> Languages { get; } =
            new() { "English", "Urdu" };

        public SettingsViewModel()
        {
            LoadFromDisk();
            _ = LoadCredentialsStatusAsync();
        }

        private void LoadFromDisk()
        {
            try
            {
                if (!File.Exists(SettingsFilePath))
                    return;

                var json = File.ReadAllText(SettingsFilePath);
                var data = JsonSerializer.Deserialize<SettingsData>(json);
                if (data is null)
                    return;

                TelegramBotToken = data.TelegramBotToken ?? "";
                TelegramChatId = data.TelegramChatId ?? "";
                SelectedTheme = data.SelectedTheme ?? "Dark";
                SelectedLanguage = data.SelectedLanguage ?? "English";
                EnableNotifications = data.EnableNotifications;
                AutoTrading = data.AutoTrading;
                RiskPercent = data.RiskPercent ?? "2";
                Leverage = data.Leverage ?? "10";
            }
            catch (Exception ex)
            {
                StatusMessage = $"Could not load saved settings: {ex.Message}";
            }
        }

        private async Task LoadCredentialsStatusAsync()
        {
            try
            {
                var status = await _apiService.GetAsync<CredentialsStatusDto>("account/credentials/status");
                HasSavedCredentials = status?.HasCredentials ?? false;
                SavedCredentialsEnvironment = status?.Environment;
            }
            catch
            {
                // Backend may not be running yet - not fatal, just leave status unknown.
                HasSavedCredentials = false;
                SavedCredentialsEnvironment = null;
            }
        }

        [RelayCommand]
        private async Task Save()
        {
            if (IsSaving)
                return;

            IsSaving = true;
            StatusMessage = null;

            try
            {
                // 1. Non-sensitive preferences -> local file, as before.
                var directory = Path.GetDirectoryName(SettingsFilePath);
                if (!string.IsNullOrEmpty(directory))
                    Directory.CreateDirectory(directory);

                var data = new SettingsData
                {
                    TelegramBotToken = TelegramBotToken,
                    TelegramChatId = TelegramChatId,
                    SelectedTheme = SelectedTheme,
                    SelectedLanguage = SelectedLanguage,
                    EnableNotifications = EnableNotifications,
                    AutoTrading = AutoTrading,
                    RiskPercent = RiskPercent,
                    Leverage = Leverage,
                };

                var json = JsonSerializer.Serialize(data, new JsonSerializerOptions { WriteIndented = true });
                File.WriteAllText(SettingsFilePath, json);

                // 2. Binance API Key/Secret -> backend, encrypted at rest.
                //    Only sent if the user actually typed something (fields are
                //    always blank on load, since secrets never round-trip to the UI).
                if (!string.IsNullOrWhiteSpace(ApiKey) && !string.IsNullOrWhiteSpace(SecretKey))
                {
                    var result = await _apiService.PostAsync<SaveCredentialsRequestDto, CredentialsStatusDto>(
                        "account/credentials",
                        new SaveCredentialsRequestDto { ApiKey = ApiKey, ApiSecret = SecretKey, Testnet = Testnet });

                    HasSavedCredentials = result?.HasCredentials ?? false;
                    SavedCredentialsEnvironment = result?.Environment;

                    // Clear the fields immediately after a successful save - the
                    // backend now holds them encrypted, there's no reason to keep
                    // the plaintext secret sitting in a UI field.
                    ApiKey = "";
                    SecretKey = "";

                    StatusMessage = "Settings saved. Binance API credentials saved and encrypted.";
                }
                else
                {
                    StatusMessage = "Settings saved.";
                }
            }
            catch (Exception ex)
            {
                StatusMessage = $"Failed to save settings: {ex.Message}";
            }
            finally
            {
                IsSaving = false;
            }
        }

        [RelayCommand]
        private void Reset()
        {
            ApiKey = "";
            SecretKey = "";
            Testnet = false;
            TelegramBotToken = "";
            TelegramChatId = "";
            SelectedTheme = "Dark";
            SelectedLanguage = "English";
            EnableNotifications = true;
            AutoTrading = false;
            RiskPercent = "2";
            Leverage = "10";
            StatusMessage = "Reset to defaults (not yet saved). Saved Binance credentials on the server are unaffected.";
        }

        private sealed class SettingsData
        {
            public string? TelegramBotToken { get; set; }
            public string? TelegramChatId { get; set; }
            public string? SelectedTheme { get; set; }
            public string? SelectedLanguage { get; set; }
            public bool EnableNotifications { get; set; }
            public bool AutoTrading { get; set; }
            public string? RiskPercent { get; set; }
            public string? Leverage { get; set; }
        }
    }
}
