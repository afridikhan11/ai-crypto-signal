using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace AI_Crypto_Signal_Pro.Models
{
    /// <summary>Request body for POST /api/v1/token-scan (matches app/schemas/token_scan.py TokenScanRequest).</summary>
    public sealed class TokenScanRequestDto
    {
        [JsonPropertyName("chain")]
        public string Chain { get; set; } = string.Empty;

        [JsonPropertyName("token_address")]
        public string TokenAddress { get; set; } = string.Empty;
    }

    /// <summary>One entry in the report's "unavailable_data" list - a section that could NOT be filled with real data, and why.</summary>
    public sealed class DataAvailabilityDto
    {
        [JsonPropertyName("field")]
        public string Field { get; set; } = string.Empty;

        [JsonPropertyName("reason")]
        public string Reason { get; set; } = string.Empty;
    }

    /// <summary>Matches FastAPI DexScreenerSection schema - liquidity/volume/price action from DexScreener (free).</summary>
    public sealed class DexScreenerSectionDto
    {
        [JsonPropertyName("available")]
        public bool Available { get; set; }

        [JsonPropertyName("error")]
        public string? Error { get; set; }

        [JsonPropertyName("dex_id")]
        public string? DexId { get; set; }

        [JsonPropertyName("pair_address")]
        public string? PairAddress { get; set; }

        [JsonPropertyName("url")]
        public string? Url { get; set; }

        [JsonPropertyName("base_token_symbol")]
        public string? BaseTokenSymbol { get; set; }

        [JsonPropertyName("base_token_name")]
        public string? BaseTokenName { get; set; }

        [JsonPropertyName("quote_token_symbol")]
        public string? QuoteTokenSymbol { get; set; }

        [JsonPropertyName("price_usd")]
        public double? PriceUsd { get; set; }

        [JsonPropertyName("liquidity_usd")]
        public double? LiquidityUsd { get; set; }

        [JsonPropertyName("fdv_usd")]
        public double? FdvUsd { get; set; }

        [JsonPropertyName("market_cap_usd")]
        public double? MarketCapUsd { get; set; }

        [JsonPropertyName("volume_24h_usd")]
        public double? Volume24hUsd { get; set; }

        [JsonPropertyName("price_change_1h_pct")]
        public double? PriceChange1hPct { get; set; }

        [JsonPropertyName("price_change_24h_pct")]
        public double? PriceChange24hPct { get; set; }

        [JsonPropertyName("txns_24h_buys")]
        public int? Txns24hBuys { get; set; }

        [JsonPropertyName("txns_24h_sells")]
        public int? Txns24hSells { get; set; }

        [JsonPropertyName("buy_sell_ratio_24h")]
        public double? BuySellRatio24h { get; set; }

        [JsonPropertyName("pair_age_hours")]
        public double? PairAgeHours { get; set; }

        [JsonPropertyName("is_new_pair")]
        public bool? IsNewPair { get; set; }

        [JsonPropertyName("momentum")]
        public string? Momentum { get; set; }
    }

    /// <summary>
    /// Matches FastAPI TokenSecuritySection schema - mint/honeypot/blacklist/ownership/proxy/LP-lock checks from GoPlus (free).
    ///
    /// LEGACY - BACKWARD COMPATIBILITY ONLY (marked 2026-07-26). Superseded
    /// in the UI by ContractSecurityAnalysisSectionDto (the tabbed Security
    /// dashboard); no View/ViewModel binds to this DTO anymore. Kept because
    /// the backend still returns it on every scan for API backward
    /// compatibility (user's explicit decision - see project memory).
    /// Do not bind new UI to this DTO; use ContractSecurityAnalysisSectionDto
    /// for all new security features. Removing this is an API-breaking
    /// change and needs explicit approval.
    /// </summary>
    public sealed class TokenSecuritySectionDto
    {
        [JsonPropertyName("available")]
        public bool Available { get; set; }

        [JsonPropertyName("error")]
        public string? Error { get; set; }

        [JsonPropertyName("mint_function_present")]
        public bool? MintFunctionPresent { get; set; }

        [JsonPropertyName("honeypot")]
        public bool? Honeypot { get; set; }

        [JsonPropertyName("has_blacklist_function")]
        public bool? HasBlacklistFunction { get; set; }

        [JsonPropertyName("has_whitelist_function")]
        public bool? HasWhitelistFunction { get; set; }

        [JsonPropertyName("ownership_renounced")]
        public bool? OwnershipRenounced { get; set; }

        [JsonPropertyName("is_proxy_contract")]
        public bool? IsProxyContract { get; set; }

        [JsonPropertyName("is_open_source")]
        public bool? IsOpenSource { get; set; }

        [JsonPropertyName("lp_locked_pct")]
        public double? LpLockedPct { get; set; }

        [JsonPropertyName("buy_tax_pct")]
        public double? BuyTaxPct { get; set; }

        [JsonPropertyName("sell_tax_pct")]
        public double? SellTaxPct { get; set; }

        [JsonPropertyName("holder_count")]
        public int? HolderCount { get; set; }

        [JsonPropertyName("top_10_holder_pct")]
        public double? Top10HolderPct { get; set; }
    }

    /// <summary>Matches FastAPI TechnicalAnalysisSection schema - the SAME SMC/indicator engine used for Binance futures, run on GeckoTerminal candles (free).</summary>
    public sealed class TechnicalAnalysisSectionDto
    {
        [JsonPropertyName("available")]
        public bool Available { get; set; }

        [JsonPropertyName("error")]
        public string? Error { get; set; }

        [JsonPropertyName("candles_used")]
        public int? CandlesUsed { get; set; }

        [JsonPropertyName("timeframe")]
        public string? Timeframe { get; set; }

        [JsonPropertyName("trend")]
        public string? Trend { get; set; }

        [JsonPropertyName("latest_structure_break")]
        public string? LatestStructureBreak { get; set; }

        [JsonPropertyName("order_block_present")]
        public bool? OrderBlockPresent { get; set; }

        [JsonPropertyName("unfilled_fvg_count")]
        public int? UnfilledFvgCount { get; set; }

        [JsonPropertyName("liquidity_swept")]
        public bool? LiquiditySwept { get; set; }

        [JsonPropertyName("rsi")]
        public double? Rsi { get; set; }

        [JsonPropertyName("adx")]
        public double? Adx { get; set; }

        [JsonPropertyName("macd_bullish")]
        public bool? MacdBullish { get; set; }

        [JsonPropertyName("supertrend_bullish")]
        public bool? SupertrendBullish { get; set; }

        [JsonPropertyName("price_vs_vwap")]
        public string? PriceVsVwap { get; set; }

        [JsonPropertyName("price_vs_ema200")]
        public string? PriceVsEma200 { get; set; }

        // Institutional-grade dashboard - added on top of the fields above;
        // every original field is untouched. See app/services/ta_dashboard.py.
        [JsonPropertyName("dashboard")]
        public List<TechnicalIndicatorSectionDto> Dashboard { get; set; } = new();

        [JsonPropertyName("ai_summary")]
        public AITechnicalSummaryDto? AiSummary { get; set; }
    }

    /// <summary>One row of the institutional-grade Technical Analysis dashboard - matches FastAPI TechnicalIndicatorSection schema.</summary>
    public sealed class TechnicalIndicatorSectionDto
    {
        [JsonPropertyName("name")]
        public string Name { get; set; } = string.Empty;

        [JsonPropertyName("status")]
        public string Status { get; set; } = string.Empty;

        [JsonPropertyName("strength")]
        public int Strength { get; set; }

        [JsonPropertyName("confidence")]
        public int Confidence { get; set; }

        [JsonPropertyName("detail")]
        public string? Detail { get; set; }
    }

    /// <summary>The dashboard's closing verdict - matches FastAPI AITechnicalSummary schema. Stop Loss/Take Profit/Risk-Reward reuse the SAME values as the report's top-level "Suggested Trade Levels" card.</summary>
    public sealed class AITechnicalSummaryDto
    {
        [JsonPropertyName("technical_score")]
        public int TechnicalScore { get; set; }

        [JsonPropertyName("trend")]
        public string Trend { get; set; } = string.Empty;

        [JsonPropertyName("momentum")]
        public string Momentum { get; set; } = string.Empty;

        [JsonPropertyName("structure")]
        public string Structure { get; set; } = string.Empty;

        [JsonPropertyName("best_entry_zone")]
        public string? BestEntryZone { get; set; }

        [JsonPropertyName("stop_loss")]
        public double? StopLoss { get; set; }

        [JsonPropertyName("take_profit")]
        public double? TakeProfit { get; set; }

        [JsonPropertyName("risk_reward")]
        public string? RiskReward { get; set; }

        [JsonPropertyName("verdict")]
        public string Verdict { get; set; } = string.Empty;
    }

    /// <summary>
    /// Matches FastAPI SmartMoneyAnalysisSection schema - institutional-grade Smart Money
    /// dashboard (see app/services/smart_money_service.py). "Dashboard" reuses the exact
    /// same TechnicalIndicatorSectionDto row shape as the Technical Analysis dashboard -
    /// every row is either backed by a real trade-flow data source (GeckoTerminal on-chain
    /// trades or Binance aggregated trades) or explicitly marked "Not Available" with a
    /// specific reason (e.g. Exchange Inflows/Outflows and every wallet-identity category
    /// require a paid Arkham/Nansen subscription with no free API tier).
    /// </summary>
    public sealed class SmartMoneyAnalysisSectionDto
    {
        [JsonPropertyName("available")]
        public bool Available { get; set; }

        [JsonPropertyName("error")]
        public string? Error { get; set; }

        [JsonPropertyName("source")]
        public string? Source { get; set; }

        [JsonPropertyName("window_description")]
        public string? WindowDescription { get; set; }

        [JsonPropertyName("dashboard")]
        public List<TechnicalIndicatorSectionDto> Dashboard { get; set; } = new();

        [JsonPropertyName("ai_summary")]
        public AISmartMoneySummaryDto? AiSummary { get; set; }
    }

    /// <summary>
    /// The Smart Money dashboard's closing verdict - matches FastAPI AISmartMoneySummary
    /// schema. "Caveat" always explicitly discloses that this is an order-flow proxy
    /// score, not wallet-labeled smart money (Arkham/Nansen data is not available).
    /// </summary>
    public sealed class AISmartMoneySummaryDto
    {
        [JsonPropertyName("smart_money_score")]
        public int SmartMoneyScore { get; set; }

        [JsonPropertyName("activity_level")]
        public string ActivityLevel { get; set; } = string.Empty;

        [JsonPropertyName("net_flow_bias")]
        public string NetFlowBias { get; set; } = string.Empty;

        [JsonPropertyName("data_coverage_pct")]
        public int DataCoveragePct { get; set; }

        [JsonPropertyName("verdict")]
        public string Verdict { get; set; } = string.Empty;

        [JsonPropertyName("caveat")]
        public string Caveat { get; set; } = string.Empty;
    }

    /// <summary>
    /// The Contract Security dashboard's closing verdict - matches FastAPI AISecuritySummary
    /// schema. A "security_score" of 0 with verdict "INSUFFICIENT DATA" specifically means
    /// nothing could be verified - NOT that the token is safe. See "Caveat" for exactly
    /// which providers were checked.
    /// </summary>
    public sealed class AISecuritySummaryDto
    {
        [JsonPropertyName("security_score")]
        public int SecurityScore { get; set; }

        [JsonPropertyName("risk_level")]
        public string RiskLevel { get; set; } = string.Empty;

        [JsonPropertyName("verdict")]
        public string Verdict { get; set; } = string.Empty;

        [JsonPropertyName("key_concerns")]
        public List<string> KeyConcerns { get; set; } = new();

        [JsonPropertyName("caveat")]
        public string Caveat { get; set; } = string.Empty;
    }

    /// <summary>
    /// Matches FastAPI ContractSecurityAnalysisSection schema - institutional-grade Contract
    /// Security dashboard (see app/services/contract_security_service.py). On-chain tokens
    /// only - not applicable to Binance trading-pair symbols. "Dashboard" reuses the exact
    /// same TechnicalIndicatorSectionDto row shape as the other two dashboards - every row is
    /// either backed by a real provider (GoPlus Security, Honeypot.is, RugCheck) or
    /// explicitly marked "Not Available" with a specific reason, never fabricated.
    /// </summary>
    public sealed class ContractSecurityAnalysisSectionDto
    {
        [JsonPropertyName("available")]
        public bool Available { get; set; }

        [JsonPropertyName("error")]
        public string? Error { get; set; }

        [JsonPropertyName("sources_used")]
        public List<string> SourcesUsed { get; set; } = new();

        [JsonPropertyName("dashboard")]
        public List<TechnicalIndicatorSectionDto> Dashboard { get; set; } = new();

        [JsonPropertyName("ai_summary")]
        public AISecuritySummaryDto? AiSummary { get; set; }
    }

    /// <summary>Matches FastAPI SentimentSection schema - only the free, market-wide Crypto Fear & Greed Index (not token-specific social sentiment, which requires a paid provider).</summary>
    public sealed class SentimentSectionDto
    {
        [JsonPropertyName("available")]
        public bool Available { get; set; }

        [JsonPropertyName("error")]
        public string? Error { get; set; }

        [JsonPropertyName("fear_greed_value")]
        public int? FearGreedValue { get; set; }

        [JsonPropertyName("fear_greed_classification")]
        public string? FearGreedClassification { get; set; }
    }

    /// <summary>Matches FastAPI TokenScanReport schema - the full combined report returned by POST /api/v1/token-scan.</summary>
    public sealed class TokenScanReportDto
    {
        [JsonPropertyName("chain")]
        public string Chain { get; set; } = string.Empty;

        [JsonPropertyName("token_address")]
        public string TokenAddress { get; set; } = string.Empty;

        [JsonPropertyName("generated_at")]
        public string GeneratedAt { get; set; } = string.Empty;

        [JsonPropertyName("safety_score")]
        public int SafetyScore { get; set; }

        [JsonPropertyName("confidence_pct")]
        public int ConfidencePct { get; set; }

        [JsonPropertyName("trend")]
        public string Trend { get; set; } = string.Empty;

        [JsonPropertyName("whales")]
        public string Whales { get; set; } = string.Empty;

        [JsonPropertyName("smart_money")]
        public string SmartMoney { get; set; } = string.Empty;

        [JsonPropertyName("liquidity")]
        public string Liquidity { get; set; } = string.Empty;

        [JsonPropertyName("holder_distribution")]
        public string HolderDistribution { get; set; } = string.Empty;

        [JsonPropertyName("contract_risk")]
        public string ContractRisk { get; set; } = string.Empty;

        [JsonPropertyName("manipulation_risk")]
        public string ManipulationRisk { get; set; } = string.Empty;

        [JsonPropertyName("best_entry")]
        public double? BestEntry { get; set; }

        [JsonPropertyName("stop_loss")]
        public double? StopLoss { get; set; }

        [JsonPropertyName("take_profit_1")]
        public double? TakeProfit1 { get; set; }

        [JsonPropertyName("take_profit_2")]
        public double? TakeProfit2 { get; set; }

        [JsonPropertyName("take_profit_3")]
        public double? TakeProfit3 { get; set; }

        [JsonPropertyName("risk_reward")]
        public string? RiskReward { get; set; }

        [JsonPropertyName("final_decision")]
        public string FinalDecision { get; set; } = string.Empty;

        [JsonPropertyName("reasons")]
        public List<string> Reasons { get; set; } = new();

        [JsonPropertyName("dexscreener")]
        public DexScreenerSectionDto DexScreener { get; set; } = new();

        [JsonPropertyName("token_security")]
        public TokenSecuritySectionDto TokenSecurity { get; set; } = new();

        [JsonPropertyName("technical_analysis")]
        public TechnicalAnalysisSectionDto TechnicalAnalysis { get; set; } = new();

        [JsonPropertyName("smart_money_analysis")]
        public SmartMoneyAnalysisSectionDto SmartMoneyAnalysis { get; set; } = new();

        [JsonPropertyName("contract_security_analysis")]
        public ContractSecurityAnalysisSectionDto ContractSecurityAnalysis { get; set; } = new();

        [JsonPropertyName("sentiment")]
        public SentimentSectionDto Sentiment { get; set; } = new();

        [JsonPropertyName("unavailable_data")]
        public List<DataAvailabilityDto> UnavailableData { get; set; } = new();
    }

    /// <summary>Matches GET /api/v1/token-scan/supported-chains response.</summary>
    public sealed class SupportedChainsDto
    {
        [JsonPropertyName("chains")]
        public List<string> Chains { get; set; } = new();
    }

    /// <summary>One candidate match from GET /api/v1/token-scan/search?query= - matches FastAPI TokenSearchResult schema.</summary>
    public sealed class TokenSearchResultDto
    {
        [JsonPropertyName("chain")]
        public string Chain { get; set; } = string.Empty;

        [JsonPropertyName("token_address")]
        public string TokenAddress { get; set; } = string.Empty;

        [JsonPropertyName("symbol")]
        public string Symbol { get; set; } = string.Empty;

        [JsonPropertyName("name")]
        public string Name { get; set; } = string.Empty;

        [JsonPropertyName("dex_id")]
        public string DexId { get; set; } = string.Empty;

        [JsonPropertyName("price_usd")]
        public double? PriceUsd { get; set; }

        [JsonPropertyName("liquidity_usd")]
        public double? LiquidityUsd { get; set; }

        [JsonPropertyName("volume_24h_usd")]
        public double? Volume24hUsd { get; set; }

        [JsonPropertyName("pair_url")]
        public string? PairUrl { get; set; }
    }

    /// <summary>Matches FastAPI TokenSearchResponse schema - the full response of GET /api/v1/token-scan/search.</summary>
    public sealed class TokenSearchResponseDto
    {
        [JsonPropertyName("query")]
        public string Query { get; set; } = string.Empty;

        [JsonPropertyName("results")]
        public List<TokenSearchResultDto> Results { get; set; } = new();
    }
}
