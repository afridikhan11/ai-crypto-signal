using System;
using System.Collections.ObjectModel;
using System.Globalization;
using System.Linq;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using AI_Crypto_Signal_Pro.Models;
using AI_Crypto_Signal_Pro.Services;

namespace AI_Crypto_Signal_Pro.ViewModels;

/// <summary>
/// Backs the Dashboard's three dense panels (Account / AI / Market), replacing
/// the old stat cards + "Recent Signals" table (which just duplicated the
/// Live Signals tab with no unique information).
///
/// All three panels are built from real backend data (GET /dashboard):
///   - Account: the read-only Binance account snapshot, same data source as
///     the Account tab. Empty/absent when no API credentials are saved.
///   - AI: the actual AIScorer's category weights and calibration status -
///     NOT reinforcement-learning metrics. This system is a hand-weighted,
///     outcome-calibrated scorer, so there is no episode/epoch/replay-buffer/
///     entropy to show, and none is faked here.
///   - Market: BTC-wide technical + fundamental context (trend, structure,
///     liquidity, funding, open interest, Fear and Greed).
/// </summary>
public partial class DashboardViewModel : ObservableObject
{
    private const string ColorSuccess = "#00E676";
    private const string ColorDanger = "#FF5252";
    private const string ColorWarning = "#FFC107";
    private const string ColorInfo = "#29B6F6";
    private const string ColorMuted = "#8B91A0";
    private const string ColorPrimary = "#E6E8EC";

    private readonly ApiService _apiService = new();

    [ObservableProperty]
    private bool isLoading;

    [ObservableProperty]
    private string? errorMessage;

    [ObservableProperty]
    private ObservableCollection<PanelRow> accountRows = new();

    [ObservableProperty]
    private ObservableCollection<PanelRow> aiRows = new();

    [ObservableProperty]
    private ObservableCollection<PanelRow> aiWeightRows = new();

    [ObservableProperty]
    private ObservableCollection<PanelRow> marketRows = new();

    public DashboardViewModel()
    {
        _ = LoadDataAsync();
    }

    [RelayCommand]
    public async Task LoadDataAsync()
    {
        if (IsLoading)
            return;

        IsLoading = true;
        ErrorMessage = null;

        try
        {
            var dashboard = await _apiService.GetAsync<DashboardResponseDto>("dashboard");
            if (dashboard == null)
                throw new Exception("Empty dashboard response.");

            BuildAccountRows(dashboard.Account);
            BuildAiRows(dashboard.Ai);
            BuildMarketRows(dashboard.Market);
        }
        catch (Exception ex)
        {
            ErrorMessage = ex.Message;
            AccountRows = new ObservableCollection<PanelRow>();
            AiRows = new ObservableCollection<PanelRow>();
            AiWeightRows = new ObservableCollection<PanelRow>();
            MarketRows = new ObservableCollection<PanelRow>();
        }
        finally
        {
            IsLoading = false;
        }
    }

    private void BuildAccountRows(AccountPanelDto acc)
    {
        var rows = new ObservableCollection<PanelRow>();

        if (!acc.HasCredentials)
        {
            rows.Add(new PanelRow { Label = "Status", Value = "No API credentials saved", ValueColor = ColorMuted });
            AccountRows = rows;
            return;
        }

        rows.Add(new PanelRow { Label = "Environment", Value = acc.Environment ?? "-", ValueColor = ColorPrimary });
        rows.Add(new PanelRow { Label = "Wallet balance", Value = FormatUsdt(acc.WalletBalance), ValueColor = ColorPrimary });
        rows.Add(new PanelRow { Label = "Available balance", Value = FormatUsdt(acc.AvailableBalance), ValueColor = ColorPrimary });
        rows.Add(new PanelRow { Label = "Margin ratio", Value = FormatPct(acc.MarginRatioPct), ValueColor = ColorPrimary });
        rows.Add(new PanelRow { Label = "Unrealized PnL", Value = FormatUsdt(acc.UnrealizedPnl), ValueColor = ColorForSign(acc.UnrealizedPnl) });
        rows.Add(new PanelRow { Label = "Realized PnL", Value = FormatUsdt(acc.RealizedPnl), ValueColor = ColorForSign(acc.RealizedPnl) });
        rows.Add(new PanelRow { Label = "Open positions", Value = acc.OpenPositions.ToString(), ValueColor = ColorPrimary });
        rows.Add(new PanelRow { Label = "Open orders", Value = acc.OpenOrders.ToString(), ValueColor = ColorPrimary });
        rows.Add(new PanelRow
        {
            Label = "Win rate",
            Value = acc.WinRate.HasValue ? $"{acc.WinRate.Value:F1}%" : "-",
            ValueColor = ColorSuccess,
        });

        if (acc.Warnings.Count > 0)
        {
            rows.Add(new PanelRow { Label = "Warning", Value = acc.Warnings[0], ValueColor = ColorWarning });
        }

        AccountRows = rows;
    }

    private void BuildAiRows(AiPanelDto ai)
    {
        var rows = new ObservableCollection<PanelRow>
        {
            new PanelRow { Label = "Scorer type", Value = "Hand-weighted, outcome-calibrated", ValueColor = ColorPrimary },
            new PanelRow
            {
                Label = "Calibration",
                Value = ai.IsCalibrated
                    ? "Active (re-derived from real outcomes)"
                    : $"Defaults ({ai.WinsCollected}/{ai.MinRequiredPerGroup} wins, {ai.LossesCollected}/{ai.MinRequiredPerGroup} losses)",
                ValueColor = ai.IsCalibrated ? ColorSuccess : ColorMuted,
            },
        };

        if (!string.IsNullOrEmpty(ai.LastSignalSymbol))
        {
            rows.Add(new PanelRow { Label = "Last signal", Value = $"{ai.LastSignalSymbol} {ai.LastSignalDirection}", ValueColor = ColorPrimary });
            rows.Add(new PanelRow
            {
                Label = "Last confidence",
                Value = ai.LastSignalConfidence.HasValue ? $"{ai.LastSignalConfidence.Value}" : "-",
                ValueColor = ColorForConfidence(ai.LastSignalConfidence),
            });
        }
        else
        {
            rows.Add(new PanelRow { Label = "Last signal", Value = "None yet", ValueColor = ColorMuted });
        }

        AiRows = rows;

        var weightRows = new ObservableCollection<PanelRow>();
        foreach (var kvp in ai.Weights.OrderByDescending(w => w.Value))
        {
            weightRows.Add(new PanelRow
            {
                Label = FormatCategoryName(kvp.Key),
                Value = $"{kvp.Value * 100:F0}%",
                ValueColor = ColorInfo,
            });
        }
        AiWeightRows = weightRows;
    }

    private void BuildMarketRows(MarketPanelDto market)
    {
        var rows = new ObservableCollection<PanelRow>
        {
            new PanelRow { Label = "BTC trend 1h", Value = FormatTrend(market.BtcTrend1h), ValueColor = ColorForTrend(market.BtcTrend1h) },
            new PanelRow { Label = "BTC trend 4h", Value = FormatTrend(market.BtcTrend4h), ValueColor = ColorForTrend(market.BtcTrend4h) },
            new PanelRow { Label = "Market structure", Value = market.MarketStructure, ValueColor = ColorPrimary },
            new PanelRow { Label = "Liquidity sweep", Value = market.LiquiditySweep, ValueColor = ColorPrimary },
            new PanelRow
            {
                Label = "Funding rate",
                Value = market.FundingRate.HasValue ? $"{market.FundingRate.Value * 100:F4}%" : "-",
                ValueColor = ColorForSign(market.FundingRate),
            },
            new PanelRow { Label = "Open interest", Value = FormatTrend(market.OpenInterestTrend), ValueColor = ColorForTrend(market.OpenInterestTrend) },
            new PanelRow
            {
                Label = "Fear and greed",
                Value = $"{market.FearGreedValue} ({market.FearGreedClassification})",
                ValueColor = ColorForFearGreed(market.FearGreedValue),
            },
            new PanelRow
            {
                Label = "BTC dominance",
                Value = market.BtcDominance.HasValue ? $"{market.BtcDominance.Value:F1}%" : "-",
                ValueColor = ColorPrimary,
            },
            new PanelRow
            {
                Label = "Binance feed",
                Value = market.BinanceConnected ? "Connected" : "Disconnected",
                ValueColor = market.BinanceConnected ? ColorSuccess : ColorDanger,
            },
        };

        MarketRows = rows;
    }

    private static string FormatUsdt(decimal? value) => value.HasValue ? $"{value.Value:N2} USDT" : "-";

    private static string FormatPct(decimal? value) => value.HasValue ? $"{value.Value:F2}%" : "-";

    private static string ColorForSign(decimal? value)
    {
        if (!value.HasValue) return ColorMuted;
        return value.Value >= 0 ? ColorSuccess : ColorDanger;
    }

    private static string ColorForSign(double? value)
    {
        if (!value.HasValue) return ColorMuted;
        return value.Value >= 0 ? ColorSuccess : ColorDanger;
    }

    private static string ColorForConfidence(int? confidence)
    {
        if (!confidence.HasValue) return ColorMuted;
        return confidence.Value >= 80 ? ColorSuccess : confidence.Value >= 75 ? ColorWarning : ColorMuted;
    }

    private static string FormatTrend(string trend) => CultureInfo.InvariantCulture.TextInfo.ToTitleCase(trend);

    private static string ColorForTrend(string trend) => trend.ToLowerInvariant() switch
    {
        "up" or "rising" => ColorSuccess,
        "down" or "falling" => ColorDanger,
        _ => ColorMuted,
    };

    private static string ColorForFearGreed(int value)
    {
        if (value <= 25) return ColorDanger;
        if (value >= 75) return ColorWarning;
        return ColorMuted;
    }

    private static string FormatCategoryName(string key)
    {
        var words = key.Split('_').Select(w => char.ToUpperInvariant(w[0]) + w.Substring(1));
        return string.Join(" ", words);
    }
}
