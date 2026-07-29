using System;
using System.Collections.ObjectModel;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using AI_Crypto_Signal_Pro.Models;
using AI_Crypto_Signal_Pro.Services;

namespace AI_Crypto_Signal_Pro.ViewModels;

/// <summary>
/// Backs the Token Scanner tab: user enters a chain + contract address,
/// this calls POST /api/v1/token-scan (app/api/v1/endpoints/token_scan.py)
/// and renders the combined Safety Score / Confidence / Final Decision
/// report. Unlike every other tab in this app, this is NOT a live/polling
/// view - a scan only runs when the user explicitly clicks "Scan Token",
/// since it hits three external free APIs (DexScreener, GeckoTerminal,
/// GoPlus) on demand.
///
/// Whales / Smart Money / holder-cluster / social-sentiment sections are
/// intentionally reported as plain "Not Available" text from the backend
/// (see UnavailableData) rather than hidden - this app does not have paid
/// Arkham/Nansen/Bubblemaps API access, and the report says so honestly
/// instead of guessing.
/// </summary>
public partial class TokenScannerViewModel : ObservableObject
{
    private readonly ApiService _apiService = new();

    public ObservableCollection<string> SupportedChains { get; } = new()
    {
        "ethereum", "bsc", "solana", "base", "arbitrum", "polygon", "avalanche", "optimism"
    };

    [ObservableProperty]
    private string selectedChain = "ethereum";

    [ObservableProperty]
    private string tokenAddress = string.Empty;

    [ObservableProperty]
    private bool isLoading;

    [ObservableProperty]
    private string? errorMessage;

    [ObservableProperty]
    private bool hasReport;

    [ObservableProperty]
    private TokenScanReportDto? report;

    // ---- Symbol/name search (find-then-pick, since a ticker alone is
    // ambiguous - many unrelated tokens across many chains can share the
    // same symbol) - see GET /api/v1/token-scan/search in
    // app/api/v1/endpoints/token_scan.py. ----

    [ObservableProperty]
    private string searchQuery = string.Empty;

    [ObservableProperty]
    private bool isSearching;

    [ObservableProperty]
    private string? searchErrorMessage;

    [ObservableProperty]
    private bool hasSearchResults;

    public ObservableCollection<TokenSearchResultDto> SearchResults { get; } = new();

    public TokenScannerViewModel()
    {
        // Best-effort refresh of the supported-chain list from the backend
        // (app/services/geckoterminal_service.py's CHAIN_TO_GECKOTERMINAL_NETWORK
        // is the source of truth) - falls back silently to the hardcoded
        // list above if the backend isn't reachable yet at startup.
        _ = LoadSupportedChainsAsync();
    }

    private async Task LoadSupportedChainsAsync()
    {
        try
        {
            var result = await _apiService.GetAsync<SupportedChainsDto>("token-scan/supported-chains");
            if (result?.Chains == null || result.Chains.Count == 0)
                return;

            var previousSelection = SelectedChain;
            SupportedChains.Clear();
            foreach (var chain in result.Chains)
                SupportedChains.Add(chain);

            SelectedChain = SupportedChains.Contains(previousSelection)
                ? previousSelection
                : SupportedChains[0];
        }
        catch
        {
            // Keep the hardcoded fallback list - not worth surfacing an
            // error banner for a background convenience refresh.
        }
    }

    [RelayCommand]
    private async Task ScanAsync()
    {
        if (IsLoading)
            return;

        var address = TokenAddress?.Trim() ?? string.Empty;
        if (address.Length < 2)
        {
            ErrorMessage = "Enter a contract address, or a trading pair symbol (e.g. BTCUSDT).";
            HasReport = false;
            return;
        }

        try
        {
            IsLoading = true;
            ErrorMessage = null;
            HasReport = false;

            var request = new TokenScanRequestDto
            {
                Chain = SelectedChain,
                TokenAddress = address
            };

            var result = await _apiService.PostAsync<TokenScanRequestDto, TokenScanReportDto>("token-scan", request);

            if (result == null)
            {
                ErrorMessage = "Scan failed: the backend returned no data.";
                return;
            }

            Report = result;
            HasReport = true;
        }
        catch (Exception ex)
        {
            ErrorMessage = $"Scan failed: {ex.Message}";
            HasReport = false;
        }
        finally
        {
            IsLoading = false;
        }
    }

    [RelayCommand]
    private async Task SearchAsync()
    {
        if (IsSearching)
            return;

        var query = SearchQuery?.Trim() ?? string.Empty;
        if (query.Length < 2)
        {
            SearchErrorMessage = "Type at least 2 characters (e.g. a symbol like PEPE).";
            HasSearchResults = false;
            SearchResults.Clear();
            return;
        }

        try
        {
            IsSearching = true;
            SearchErrorMessage = null;

            // Uri.EscapeDataString, not HttpUtility.UrlEncode - System.Web
            // isn't available to this net9.0-windows WinExe project.
            var endpoint = $"token-scan/search?query={Uri.EscapeDataString(query)}";
            var result = await _apiService.GetAsync<TokenSearchResponseDto>(endpoint);

            SearchResults.Clear();
            if (result?.Results != null)
            {
                foreach (var match in result.Results)
                    SearchResults.Add(match);
            }

            HasSearchResults = SearchResults.Count > 0;
            if (!HasSearchResults)
                SearchErrorMessage = $"No matches found for '{query}' on any supported chain.";
        }
        catch (Exception ex)
        {
            SearchErrorMessage = $"Search failed: {ex.Message}";
            HasSearchResults = false;
            SearchResults.Clear();
        }
        finally
        {
            IsSearching = false;
        }
    }

    /// <summary>
    /// Picking a search result fills the chain + address fields and
    /// immediately runs the full scan on it - the search step's whole
    /// purpose is disambiguating which contract the user means, not an
    /// extra manual copy/paste step.
    /// </summary>
    [RelayCommand]
    private async Task SelectSearchResultAsync(TokenSearchResultDto? selected)
    {
        if (selected == null)
            return;

        SelectedChain = selected.Chain;
        TokenAddress = selected.TokenAddress;
        SearchResults.Clear();
        HasSearchResults = false;
        SearchQuery = string.Empty;

        await ScanAsync();
    }
}
