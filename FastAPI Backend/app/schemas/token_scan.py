from typing import List, Optional

from pydantic import BaseModel, Field


class TokenScanRequest(BaseModel):
    chain: Optional[str] = Field(
        None,
        description=(
            "One of: ethereum, bsc, solana, base, arbitrum, polygon, avalanche, optimism. "
            "Only used when token_address is a contract address - ignored/omittable when "
            "token_address is actually a trading pair symbol like BTCUSDT (auto-detected)."
        ),
    )
    token_address: str = Field(
        ...,
        description=(
            "Either a contract address (mint address on Solana) for on-chain analysis, "
            "or a Binance trading pair symbol (e.g. BTCUSDT) for Spot/Futures market "
            "analysis - the input type is detected automatically."
        ),
    )


class TokenSearchResult(BaseModel):
    """
    One candidate match for a free-text symbol/name search (e.g. "PEPE") -
    used to let the user pick the exact token/chain they mean before
    running the full scan, since a symbol alone is ambiguous (many tokens
    share the same ticker across different chains and contracts).
    """
    chain: str
    token_address: str
    symbol: str
    name: str
    dex_id: str
    price_usd: Optional[float] = None
    liquidity_usd: Optional[float] = None
    volume_24h_usd: Optional[float] = None
    pair_url: Optional[str] = None


class TokenSearchResponse(BaseModel):
    query: str
    results: List[TokenSearchResult]


class DataAvailability(BaseModel):
    """
    Every section of the report that could NOT be filled with real data is
    listed here with a plain-language reason, instead of being silently
    omitted or guessed - see token_scorer.py's module docstring for why
    Arkham/Nansen/Bubblemaps/social-sentiment are structurally unavailable
    without a paid API key.
    """
    field: str
    reason: str


class DexScreenerSection(BaseModel):
    available: bool
    error: Optional[str] = None
    dex_id: Optional[str] = None
    pair_address: Optional[str] = None
    url: Optional[str] = None
    base_token_symbol: Optional[str] = None
    base_token_name: Optional[str] = None
    quote_token_symbol: Optional[str] = None
    price_usd: Optional[float] = None
    liquidity_usd: Optional[float] = None
    fdv_usd: Optional[float] = None
    market_cap_usd: Optional[float] = None
    volume_24h_usd: Optional[float] = None
    price_change_1h_pct: Optional[float] = None
    price_change_24h_pct: Optional[float] = None
    txns_24h_buys: Optional[int] = None
    txns_24h_sells: Optional[int] = None
    buy_sell_ratio_24h: Optional[float] = None
    pair_age_hours: Optional[float] = None
    is_new_pair: Optional[bool] = None
    momentum: Optional[str] = None


class TokenSecuritySection(BaseModel):
    """
    LEGACY - BACKWARD COMPATIBILITY ONLY (marked 2026-07-26).

    This was the original, basic GoPlus-only security summary. It has been
    superseded by the full multi-provider Contract Security Dashboard (see
    ContractSecurityAnalysisSection below / app/services/contract_security_service.py),
    which covers all of this section's fields plus 12 more, with
    multi-source corroboration and an AI summary.

    Kept in the API response (`TokenScanReport.token_security`) and NOT
    deprecated, made optional, or removed - the user explicitly decided
    backward compatibility takes priority over cleanup while this project
    is under active development. Do not build new UI against this section;
    all new security-related features must use ContractSecurityAnalysisSection
    instead. Any future proposal to deprecate/remove this field is an
    API-breaking change and requires the user's explicit approval first.
    """
    available: bool
    error: Optional[str] = None
    mint_function_present: Optional[bool] = None
    honeypot: Optional[bool] = None
    has_blacklist_function: Optional[bool] = None
    has_whitelist_function: Optional[bool] = None
    ownership_renounced: Optional[bool] = None
    is_proxy_contract: Optional[bool] = None
    is_open_source: Optional[bool] = None
    lp_locked_pct: Optional[float] = None
    buy_tax_pct: Optional[float] = None
    sell_tax_pct: Optional[float] = None
    holder_count: Optional[int] = None
    top_10_holder_pct: Optional[float] = None


class TechnicalIndicatorSection(BaseModel):
    """
    One row of the institutional-grade Technical Analysis dashboard (see
    app/services/ta_dashboard.py) - e.g. "BOS", "Liquidity Sweep", "Order
    Blocks". Every value is a real, deterministic read of the underlying
    SMC/indicator modules, never a placeholder.
    """
    name: str
    status: str
    strength: int = Field(..., ge=0, le=100)
    confidence: int = Field(..., ge=0, le=100)
    detail: Optional[str] = None


class AITechnicalSummary(BaseModel):
    """
    The dashboard's closing verdict - aggregates every TechnicalIndicatorSection
    into one score/read. best_entry_zone/stop_loss/take_profit/risk_reward
    intentionally REUSE the same structure-anchored levels already shown in
    the report's top-level "Suggested Trade Levels" card (passed in via
    build_ta_dashboard's trade_levels param) so this summary never shows a
    second, conflicting set of numbers.
    """
    technical_score: int = Field(..., ge=0, le=100)
    trend: str
    momentum: str
    structure: str
    best_entry_zone: Optional[str] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    risk_reward: Optional[str] = None
    verdict: str


class TechnicalAnalysisSection(BaseModel):
    available: bool
    error: Optional[str] = None
    candles_used: Optional[int] = None
    timeframe: Optional[str] = None
    trend: Optional[str] = None  # "bullish" | "bearish" | "neutral"
    latest_structure_break: Optional[str] = None  # e.g. "CHoCH (bullish)"
    order_block_present: Optional[bool] = None
    unfilled_fvg_count: Optional[int] = None
    liquidity_swept: Optional[bool] = None
    rsi: Optional[float] = None
    adx: Optional[float] = None
    macd_bullish: Optional[bool] = None
    supertrend_bullish: Optional[bool] = None
    price_vs_vwap: Optional[str] = None  # "above" | "below"
    price_vs_ema200: Optional[str] = None

    # Institutional-grade dashboard (added on top of the fields above -
    # every original field is untouched and still populated exactly as
    # before). Empty list / None when the primary dataframe was too short
    # to run the full dashboard (e.g. technical_analysis.available=False).
    dashboard: List[TechnicalIndicatorSection] = Field(default_factory=list)
    ai_summary: Optional[AITechnicalSummary] = None


class AISecuritySummary(BaseModel):
    """
    The Contract Security dashboard's closing verdict - matches
    app/services/contract_security_service.py's `_build_ai_security_summary()`.
    `security_score` of 0 with `verdict="INSUFFICIENT DATA"` specifically
    means "nothing could be verified", never "this token is safe" - see
    `caveat` for exactly which providers were checked.
    """
    security_score: int = Field(..., ge=0, le=100)
    risk_level: str  # "Low" | "Medium" | "High" | "Critical" | "Unknown"
    verdict: str  # "SAFE" | "CAUTION" | "HIGH RISK" | "AVOID" | "INSUFFICIENT DATA"
    key_concerns: List[str] = Field(default_factory=list)
    caveat: str


class ContractSecurityAnalysisSection(BaseModel):
    """
    Institutional-grade Contract Security dashboard (see
    app/services/contract_security_service.py) - on-chain tokens only, not
    applicable to Binance trading-pair symbols (no smart contract to
    inspect). `dashboard` reuses the exact same TechnicalIndicatorSection
    row shape (name/status/strength/confidence/detail) as the Technical
    Analysis and Smart Money dashboards - every row is either backed by a
    real provider (GoPlus Security, Honeypot.is, RugCheck) or explicitly
    marked "Not Available" with a specific reason, never fabricated.
    """
    available: bool
    error: Optional[str] = None
    sources_used: List[str] = Field(default_factory=list)
    dashboard: List[TechnicalIndicatorSection] = Field(default_factory=list)
    ai_summary: Optional[AISecuritySummary] = None


class AISmartMoneySummary(BaseModel):
    """
    The Smart Money dashboard's closing verdict - aggregates the real
    trade-flow categories (Whale Activity, Accumulation/Distribution, Net
    Flow, Large Transactions) into one score/read. `caveat` always
    explicitly discloses that this is an order-flow proxy, not
    wallet-labeled smart money (see app/services/smart_money_service.py).
    """
    smart_money_score: int = Field(..., ge=0, le=100)
    activity_level: str  # "Low" | "Moderate" | "High"
    net_flow_bias: str  # "Accumulation" | "Distribution" | "Neutral"
    data_coverage_pct: int = Field(..., ge=0, le=100)
    verdict: str
    caveat: str


class SmartMoneyAnalysisSection(BaseModel):
    """
    Institutional-grade Smart Money dashboard (see
    app/services/smart_money_service.py). `dashboard` reuses the exact
    same TechnicalIndicatorSection row shape (name/status/strength/
    confidence/detail) as the Technical Analysis dashboard - every row is
    either backed by a real data source or explicitly marked
    "Not Available" with a specific reason, never fabricated.
    """
    available: bool
    error: Optional[str] = None
    source: Optional[str] = None
    window_description: Optional[str] = None
    dashboard: List[TechnicalIndicatorSection] = Field(default_factory=list)
    ai_summary: Optional[AISmartMoneySummary] = None


class SentimentSection(BaseModel):
    available: bool
    error: Optional[str] = None
    fear_greed_value: Optional[int] = None
    fear_greed_classification: Optional[str] = None


class TokenScanReport(BaseModel):
    chain: str
    token_address: str
    generated_at: str

    safety_score: int = Field(..., ge=0, le=100)
    confidence_pct: int = Field(..., ge=0, le=100)

    trend: str  # "Bullish" | "Bearish" | "Neutral" | "Unknown"
    whales: str
    smart_money: str
    liquidity: str
    holder_distribution: str
    contract_risk: str  # "Low" | "Medium" | "High" | "Critical" | "Unknown"
    manipulation_risk: str

    best_entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit_1: Optional[float] = None
    take_profit_2: Optional[float] = None
    take_profit_3: Optional[float] = None
    risk_reward: Optional[str] = None

    final_decision: str  # "STRONG BUY" | "BUY" | "NEUTRAL" | "SELL" | "AVOID"
    reasons: List[str]

    dexscreener: DexScreenerSection
    token_security: TokenSecuritySection
    technical_analysis: TechnicalAnalysisSection
    smart_money_analysis: SmartMoneyAnalysisSection
    contract_security_analysis: ContractSecurityAnalysisSection
    sentiment: SentimentSection

    unavailable_data: List[DataAvailability]
