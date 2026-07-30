"""
Asset Profile - the ONE place where per-market characteristics live.

STATUS (2026-07-30): PRODUCTION. Created for the Universal Institutional
ICT Platform phase.

THE CONTRACT: PROFILES DESCRIBE MARKETS, THEY NEVER IMPLEMENT STRATEGY
--------------------------------------------------------------------------
This platform has exactly ONE strategy - the Universal ICT Pipeline in
`app/strategy/signal_generator.py`, built from the ICT engines in
`app/smc/`. An Asset Profile contributes only the market's own
characteristics to that single pipeline:

    Trading Sessions          - which global sessions this market respects
    Kill Zones                - the high-probability windows within them
    Liquidity Characteristics - how and when liquidity actually shows up
    Volatility Characteristics- the market's structural volatility shape
    Tick Size / Contract Size - instrument mechanics
    Trading Hours             - when the market is open at all
    Economic Event Priority   - which macro releases actually move it
    Optional ICT Filters      - profile-level tightening of shared gates

There is deliberately NO field on this dataclass that could encode a
different way of finding a trade. Adding Forex, Indices or Stocks support
means adding a profile here - never a scanner, never an AI, never a
strategy, never a signal generator.

RELATIONSHIP TO `CalibrationProfile`
--------------------------------------------------------------------------
`app/ai/calibration_profiles.py` already owns the per-asset-type SCORING
configuration (category weights, confidence/risk-reward gates, ATR
multiples, volatility regime thresholds) and its own auto-calibration
machinery. That is not duplicated here. `AssetProfile.calibration` holds a
reference to the existing `CalibrationProfile` for the same asset type, so
a caller resolves ONE object per symbol and reads both concerns from it:

    profile = get_asset_profile("XAUUSDT")
    profile.session_windows          # market characteristics (this module)
    profile.calibration.min_confidence   # scoring config (calibration_profiles)

NO FABRICATED INSTRUMENT DATA
--------------------------------------------------------------------------
`tick_size` and `contract_size` are `Optional[float]` and are left `None`
wherever this project has no authoritative value. Binance publishes real
tick sizes per symbol via `exchangeInfo`, and every instrument this
platform trades is a USDT-margined perpetual - NOT a COMEX/CME contract -
so quoting "100 troy ounces" for XAUUSDT would be plainly wrong. A `None`
here means "not known from an authoritative source", never a guess, in
line with this project's no-fabrication rule.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from app.ai.calibration_profiles import CalibrationProfile, get_profile_for_symbol
from app.core.constants import (
    ASSET_CLASS_COMMODITY,
    ASSET_CLASS_CRYPTO,
    ASSET_TYPE_CRYPTO,
    ASSET_TYPE_FOREX,
    ASSET_TYPE_GOLD,
    ASSET_TYPE_OIL,
    ASSET_TYPE_SILVER,
    asset_class_for_symbol,
    asset_type_for_symbol,
)
from app.smc.session_engine import (
    DEFAULT_KILL_ZONE_WINDOWS,
    DEFAULT_LONDON_NY_OVERLAP,
    DEFAULT_SESSION_WINDOWS,
    KillZone,
    Session,
    SessionWindow,
)


@dataclass(frozen=True)
class TradingHours:
    """When the market is open at all (UTC), independent of session quality."""

    is_24_7: bool
    trades_weekends: bool
    # Daily maintenance/settlement break, if the venue has one. `None` for
    # continuously-traded markets.
    daily_break: Optional[SessionWindow] = None

    def is_open(self, hour: float, is_weekend: bool = False) -> bool:
        if not self.trades_weekends and is_weekend:
            return False
        if self.daily_break is not None and self.daily_break.contains(hour):
            return False
        return True


@dataclass(frozen=True)
class LiquidityCharacteristics:
    """How liquidity behaves in this market - context the Evidence Engine
    and the AI's session scoring interpret, not a rule that trades."""

    # Sessions during which this market carries genuinely institutional
    # participation. Evidence formed outside these is real but thinner.
    primary_liquidity_sessions: Tuple[Session, ...]
    # Whether resting liquidity tends to build as clean equal highs/lows.
    # Both current profiles are venues where it does; kept explicit so a
    # future market where it does not can say so.
    forms_clean_equal_highs_lows: bool
    # Plain-language summary surfaced in evidence/explanations.
    description: str


@dataclass(frozen=True)
class VolatilityCharacteristics:
    """Structural volatility shape. The NUMERIC regime thresholds live on
    `CalibrationProfile` (volatility_lookback / high_ratio / low_ratio) and
    are deliberately not duplicated here - these are the qualities that
    calibration does not express."""

    # Does price gap across the session/weekend break? Crypto does not;
    # markets with a real close can.
    gaps_across_breaks: bool
    # Is volatility strongly clustered into specific sessions rather than
    # spread across the day?
    session_clustered: bool
    description: str


@dataclass(frozen=True)
class EconomicEventPriority:
    """Which macro releases genuinely move this market, highest first.

    Consumed as CONTEXT for evidence and risk notes. This does not fetch
    anything - `app/services/macro_fundamentals_service.py` owns fetching -
    and it never blocks a trade on its own.
    """

    high_impact: Tuple[str, ...] = ()
    medium_impact: Tuple[str, ...] = ()

    def priority_of(self, event_name: Optional[str]) -> Optional[str]:
        if not event_name:
            return None
        name = event_name.strip().lower()
        for candidate in self.high_impact:
            if candidate.lower() in name:
                return "high"
        for candidate in self.medium_impact:
            if candidate.lower() in name:
                return "medium"
        return None


@dataclass(frozen=True)
class ICTFilters:
    """OPTIONAL, profile-level tightening of gates the shared pipeline
    already applies to every asset.

    These can only ever make the single shared pipeline STRICTER for a
    given market - they can never introduce a different way of finding a
    trade, and they can never loosen a global gate. Defaults are
    permissive, so a profile that sets nothing behaves exactly as the
    pipeline does today.
    """

    # Require the setup to sit inside a Kill Zone at all.
    require_kill_zone: bool = False
    # Reject setups formed while no primary session is active.
    reject_off_session: bool = False
    # Extra confluence (liquidity / order block / FVG) beyond the pipeline's
    # own "at least one" requirement.
    min_confluence_signals: int = 1


@dataclass(frozen=True)
class AssetProfile:
    """One market's characteristics. See module docstring for the contract."""

    asset_type: str
    asset_class: str
    display_name: str

    session_windows: Dict[Session, SessionWindow]
    kill_zone_windows: Dict[KillZone, SessionWindow]
    london_ny_overlap: Optional[SessionWindow]

    liquidity: LiquidityCharacteristics
    volatility: VolatilityCharacteristics
    trading_hours: TradingHours
    economic_events: EconomicEventPriority
    ict_filters: ICTFilters

    # Instrument mechanics - see the module docstring's no-fabrication note.
    tick_size: Optional[float] = None
    contract_size: Optional[float] = None

    # Per-symbol authoritative overrides, when known. Empty by default.
    tick_size_by_symbol: Dict[str, float] = field(default_factory=dict)

    # The EXISTING scoring configuration for this asset type - referenced,
    # never duplicated. See module docstring.
    calibration: Optional[CalibrationProfile] = None

    def tick_size_for(self, symbol: str) -> Optional[float]:
        return self.tick_size_by_symbol.get(symbol.upper(), self.tick_size)

    @property
    def primary_sessions(self) -> Tuple[Session, ...]:
        return self.liquidity.primary_liquidity_sessions


# ==========================================================================
# CRYPTO
# ==========================================================================
CRYPTO_PROFILE = AssetProfile(
    asset_type=ASSET_TYPE_CRYPTO,
    asset_class=ASSET_CLASS_CRYPTO,
    display_name="Crypto (USDT-margined perpetuals)",
    # The standard ICT session/kill-zone windows this platform has always
    # used - referenced from SessionEngine rather than re-declared, so
    # there is one definition of "the London session" in the codebase.
    session_windows=dict(DEFAULT_SESSION_WINDOWS),
    kill_zone_windows=dict(DEFAULT_KILL_ZONE_WINDOWS),
    london_ny_overlap=DEFAULT_LONDON_NY_OVERLAP,
    liquidity=LiquidityCharacteristics(
        primary_liquidity_sessions=(Session.ASIAN, Session.LONDON, Session.NEW_YORK),
        forms_clean_equal_highs_lows=True,
        description=(
            "Trades continuously with real participation in all three global sessions. "
            "Liquidity is thinner but genuine during the Asian session, deepest across "
            "the London/New York overlap."
        ),
    ),
    volatility=VolatilityCharacteristics(
        gaps_across_breaks=False,
        session_clustered=False,
        description=(
            "Continuous 24/7 trading with no settlement break, so price does not gap "
            "across sessions or weekends. Volatility is present around the clock rather "
            "than concentrated into a single session."
        ),
    ),
    trading_hours=TradingHours(is_24_7=True, trades_weekends=True, daily_break=None),
    economic_events=EconomicEventPriority(
        high_impact=("FOMC", "CPI", "Interest Rate Decision"),
        medium_impact=("NFP", "Non-Farm Payrolls", "PPI", "Unemployment"),
    ),
    # Crypto runs the shared pipeline with no extra tightening - exactly
    # the behaviour that shipped before this profile existed.
    ict_filters=ICTFilters(),
    calibration=None,  # resolved per symbol - see get_asset_profile()
)


# ==========================================================================
# COMMODITY (Gold / Silver / Oil)
# ==========================================================================
# Sessions: gold, silver and oil are London- and New-York-centred markets.
# The Asian session is deliberately NOT a primary liquidity session for
# them: it trades, but without the institutional participation that makes
# ICT session logic meaningful, so evidence formed there should read as
# lower-conviction rather than equal to a London/NY setup.
COMMODITY_SESSION_WINDOWS: Dict[Session, SessionWindow] = {
    Session.LONDON: SessionWindow(7, 16),
    Session.NEW_YORK: SessionWindow(12, 21),
}

# Kill zones: the standard London/NY/London-close windows, PLUS the COMEX
# metals open (13:30 UTC / 08:30 ET) - a real, recurring liquidity event
# for metals with no crypto equivalent.
COMMODITY_KILL_ZONE_WINDOWS: Dict[KillZone, SessionWindow] = {
    KillZone.LONDON_KILL_ZONE: SessionWindow(7, 10),
    KillZone.COMEX_OPEN_KILL_ZONE: SessionWindow(13, 15),
    KillZone.NEW_YORK_KILL_ZONE: SessionWindow(12, 15),
    KillZone.LONDON_CLOSE_KILL_ZONE: SessionWindow(14, 16),
}

COMMODITY_PROFILE = AssetProfile(
    asset_type=ASSET_CLASS_COMMODITY,
    asset_class=ASSET_CLASS_COMMODITY,
    display_name="Commodity (Gold / Silver / Oil)",
    session_windows=dict(COMMODITY_SESSION_WINDOWS),
    kill_zone_windows=dict(COMMODITY_KILL_ZONE_WINDOWS),
    london_ny_overlap=DEFAULT_LONDON_NY_OVERLAP,
    liquidity=LiquidityCharacteristics(
        primary_liquidity_sessions=(Session.LONDON, Session.NEW_YORK),
        forms_clean_equal_highs_lows=True,
        description=(
            "London- and New-York-centred. Real institutional depth arrives with the "
            "London open and again at the COMEX metals open; the Asian session trades "
            "but without comparable participation, so evidence formed there is thinner."
        ),
    ),
    volatility=VolatilityCharacteristics(
        gaps_across_breaks=True,
        session_clustered=True,
        description=(
            "Volatility is strongly clustered into the London and New York sessions and "
            "around scheduled USD macro releases. The underlying venue observes a daily "
            "settlement break and a weekend close, so price can gap across them."
        ),
    ),
    trading_hours=TradingHours(
        is_24_7=False,
        trades_weekends=False,
        # CME/COMEX daily settlement break, 22:00-23:00 UTC.
        daily_break=SessionWindow(22, 23),
    ),
    economic_events=EconomicEventPriority(
        # Metals and oil are USD-priced, so dollar and real-yield drivers
        # dominate - which is exactly what macro_fundamentals_service.py
        # already fetches for these symbols (DXY, real yields, event risk).
        high_impact=("FOMC", "CPI", "Interest Rate Decision", "NFP", "Non-Farm Payrolls"),
        medium_impact=("PPI", "Unemployment", "Retail Sales", "GDP", "Crude Oil Inventories"),
    ),
    # Commodities honour the shared pipeline with one profile-level
    # tightening: because their volatility is genuinely session-clustered
    # (unlike crypto's), a setup formed with no primary session active is
    # rejected rather than merely scored lower. This makes the SAME
    # pipeline stricter for this market - it does not change how a setup
    # is found.
    ict_filters=ICTFilters(reject_off_session=True),
    calibration=None,  # resolved per symbol - see get_asset_profile()
)


# ==========================================================================
# FUTURE-READY: Forex / Indices / Stocks
# ==========================================================================
# These are intentionally minimal, unreachable-today declarations. No
# symbol routes to them (see app.core.constants.SYMBOL_TO_ASSET_TYPE), and
# they exist to prove the architectural claim that adding an asset class
# requires ONLY a profile - no new scanner, AI, risk engine or strategy.
ASSET_TYPE_INDICES = "indices"
ASSET_TYPE_STOCKS = "stocks"

FOREX_PROFILE = AssetProfile(
    asset_type=ASSET_TYPE_FOREX,
    asset_class=ASSET_TYPE_FOREX,
    display_name="Forex (spot FX pairs)",
    session_windows=dict(DEFAULT_SESSION_WINDOWS),
    kill_zone_windows=dict(DEFAULT_KILL_ZONE_WINDOWS),
    london_ny_overlap=DEFAULT_LONDON_NY_OVERLAP,
    liquidity=LiquidityCharacteristics(
        primary_liquidity_sessions=(Session.ASIAN, Session.LONDON, Session.NEW_YORK),
        forms_clean_equal_highs_lows=True,
        description="Continuous Sunday-to-Friday interbank market across all three sessions.",
    ),
    volatility=VolatilityCharacteristics(
        gaps_across_breaks=True,
        session_clustered=True,
        description="Session-clustered volatility; gaps over the weekend close.",
    ),
    trading_hours=TradingHours(is_24_7=False, trades_weekends=False, daily_break=SessionWindow(21, 22)),
    economic_events=EconomicEventPriority(
        high_impact=("FOMC", "CPI", "Interest Rate Decision", "NFP", "Non-Farm Payrolls"),
        medium_impact=("PPI", "GDP", "Retail Sales", "Unemployment"),
    ),
    ict_filters=ICTFilters(reject_off_session=True),
    calibration=None,
)

INDICES_PROFILE = AssetProfile(
    asset_type=ASSET_TYPE_INDICES,
    asset_class=ASSET_TYPE_INDICES,
    display_name="Indices (equity index futures)",
    session_windows={Session.LONDON: SessionWindow(7, 16), Session.NEW_YORK: SessionWindow(13, 21)},
    kill_zone_windows={
        KillZone.LONDON_KILL_ZONE: SessionWindow(7, 10),
        KillZone.NEW_YORK_KILL_ZONE: SessionWindow(13, 16),
        KillZone.LONDON_CLOSE_KILL_ZONE: SessionWindow(14, 16),
    },
    london_ny_overlap=SessionWindow(13, 16),
    liquidity=LiquidityCharacteristics(
        primary_liquidity_sessions=(Session.LONDON, Session.NEW_YORK),
        forms_clean_equal_highs_lows=True,
        description="Depth concentrated in the cash-equity session and the index futures open.",
    ),
    volatility=VolatilityCharacteristics(
        gaps_across_breaks=True,
        session_clustered=True,
        description="Sharply session-clustered around the cash open; gaps across the overnight break.",
    ),
    trading_hours=TradingHours(is_24_7=False, trades_weekends=False, daily_break=SessionWindow(21, 22)),
    economic_events=EconomicEventPriority(
        high_impact=("FOMC", "CPI", "Interest Rate Decision"),
        medium_impact=("NFP", "Non-Farm Payrolls", "GDP", "Earnings"),
    ),
    ict_filters=ICTFilters(reject_off_session=True),
    calibration=None,
)

STOCKS_PROFILE = AssetProfile(
    asset_type=ASSET_TYPE_STOCKS,
    asset_class=ASSET_TYPE_STOCKS,
    display_name="Stocks (cash equities)",
    session_windows={Session.NEW_YORK: SessionWindow(13, 21)},
    kill_zone_windows={
        KillZone.NEW_YORK_KILL_ZONE: SessionWindow(13, 16),
    },
    # Explicitly zero-width rather than None: a single-session market has
    # no London/New York overlap at all, and SessionEngine treats a
    # zero-width window as never-active. Passing None would silently fall
    # back to the shared DEFAULT_LONDON_NY_OVERLAP, which would be wrong.
    london_ny_overlap=SessionWindow(0, 0),
    liquidity=LiquidityCharacteristics(
        primary_liquidity_sessions=(Session.NEW_YORK,),
        forms_clean_equal_highs_lows=True,
        description="Single regular-hours session; pre/post-market depth is materially thinner.",
    ),
    # Explicitly zero-width rather than None: a single-session market has
    # no London/NY overlap at all, and SessionEngine treats a zero-width
    # window as never-active (passing None would silently fall back to the
    # shared DEFAULT_LONDON_NY_OVERLAP, which would be wrong here).
    volatility=VolatilityCharacteristics(
        gaps_across_breaks=True,
        session_clustered=True,
        description="Volatility concentrated at the open; routinely gaps overnight on news and earnings.",
    ),
    trading_hours=TradingHours(is_24_7=False, trades_weekends=False, daily_break=SessionWindow(21, 13)),
    economic_events=EconomicEventPriority(
        high_impact=("FOMC", "CPI", "Earnings"),
        medium_impact=("NFP", "Non-Farm Payrolls", "GDP", "Retail Sales"),
    ),
    ict_filters=ICTFilters(reject_off_session=True),
    calibration=None,
)


# Registry keyed by the FINE-GRAINED asset type (see app.core.constants).
# Gold, Silver and Oil all resolve to the single COMMODITY_PROFILE: their
# market characteristics are the same London/NY/COMEX shape. What differs
# between them - scoring weights and thresholds - already lives in their
# separate CalibrationProfiles, which is why that concern is referenced
# rather than duplicated here.
_PROFILES_BY_ASSET_TYPE: Dict[str, AssetProfile] = {
    ASSET_TYPE_CRYPTO: CRYPTO_PROFILE,
    ASSET_TYPE_GOLD: COMMODITY_PROFILE,
    ASSET_TYPE_SILVER: COMMODITY_PROFILE,
    ASSET_TYPE_OIL: COMMODITY_PROFILE,
    ASSET_TYPE_FOREX: FOREX_PROFILE,
    ASSET_TYPE_INDICES: INDICES_PROFILE,
    ASSET_TYPE_STOCKS: STOCKS_PROFILE,
}


def get_asset_profile(symbol: str) -> AssetProfile:
    """
    The one resolver every caller uses. Determines the asset type from the
    symbol (`app.core.constants.asset_type_for_symbol`), returns the
    matching market profile, and attaches that symbol's existing
    `CalibrationProfile` so a caller holds a single object describing both
    the market and its scoring configuration.

    Unknown symbols resolve to crypto, matching
    `asset_type_for_symbol`'s own documented default.
    """
    asset_type = asset_type_for_symbol(symbol)
    base = _PROFILES_BY_ASSET_TYPE.get(asset_type, CRYPTO_PROFILE)
    calibration = get_profile_for_symbol(symbol)

    # dataclasses.replace would be the idiomatic form, but AssetProfile is
    # frozen and holds mutable dict fields - constructing explicitly keeps
    # each profile's dicts from being shared across symbols by reference.
    return AssetProfile(
        asset_type=base.asset_type,
        asset_class=asset_class_for_symbol(symbol),
        display_name=base.display_name,
        session_windows=dict(base.session_windows),
        kill_zone_windows=dict(base.kill_zone_windows),
        london_ny_overlap=base.london_ny_overlap,
        liquidity=base.liquidity,
        volatility=base.volatility,
        trading_hours=base.trading_hours,
        economic_events=base.economic_events,
        ict_filters=base.ict_filters,
        tick_size=base.tick_size,
        contract_size=base.contract_size,
        tick_size_by_symbol=dict(base.tick_size_by_symbol),
        calibration=calibration,
    )


def all_asset_profiles() -> Dict[str, AssetProfile]:
    """Every registered market profile, keyed by asset type."""
    return dict(_PROFILES_BY_ASSET_TYPE)
