"""
Strategy Profile Manager (Phase 2, 2026-07-27).

Two INDEPENDENT profile dimensions the Trading Agent needs, bundled behind
one manager:

  1. ASSET-TYPE profile (Crypto / Gold / Silver / Oil / Forex) - this
     already exists in full as `app.ai.calibration_profiles.CalibrationProfile`
     (indicator weights, ATR multiples, min confidence/RR, volatility/volume
     thresholds - see that module). This manager does NOT reimplement any of
     it - `get_asset_profile()` below just calls the real
     `get_profile_for_symbol()` and returns it untouched.

  2. TRADING-STYLE profile (Scalping / Intraday / Swing / Position) - this
     is genuinely NEW. Nothing in the codebase today distinguishes a
     scalping setup from a swing setup: `SignalGenerator`/`AIScorer` always
     operate on the same 15m primary timeframe with the same ATR multiples
     for every signal, asset-type aside. Building real per-style timeframe
     switching into the live scanner is a much bigger change than Phase 2's
     scope ("orchestrate existing modules", not "recalculate indicators")
     - so `TradingStyleProfile` below is honest about what it does: it
     supplies a stop/target ATR-multiple RATIO and a minimum RR relative to
     the existing intraday-equivalent defaults, which the Decision Engine
     uses to RESCALE the SL/TP distances a reused report already computed
     (same entry price, same underlying market read) rather than deriving a
     fresh ATR figure itself. `primary_timeframe_hint` is descriptive
     metadata for a future phase that might wire real per-timeframe
     scanning - nothing consumes it yet, and that's disclosed below rather
     than silently implying it already works.

No existing file is imported for write access here - this module is purely
additive on top of `app.ai.calibration_profiles`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.ai.calibration_profiles import CalibrationProfile, get_profile_for_symbol


@dataclass(frozen=True)
class TradingStyleProfile:
    style: str
    label: str
    # Multiplier RATIOS relative to this project's existing single ATR-based
    # SL/TP construction (see app/strategy/signal_generator.py /
    # scripts/analyze_smc_frequency.py's _build_signal_data - both anchor
    # stop-loss at stop_loss_atr_multiple*ATR and take-profit at either the
    # nearest real liquidity level or take_profit_fallback_atr_multiple*ATR).
    # A ratio of 1.0 means "same as the asset's own CalibrationProfile
    # already produces" (that's INTRADAY, the style already implicit in
    # today's live signals).
    stop_distance_ratio: float
    target_distance_ratio: float
    min_risk_reward: float
    expected_holding_period: str
    description: str
    # Not consumed anywhere yet (see module docstring) - forward-looking
    # metadata only.
    primary_timeframe_hint: str


STYLE_SCALPING = "scalping"
STYLE_INTRADAY = "intraday"
STYLE_SWING = "swing"
STYLE_POSITION = "position"

# INTRADAY is deliberately ratio 1.0/1.0 - it IS what the live pipeline
# already produces today (15m structure, the asset's own CalibrationProfile
# ATR multiples, unmodified). The other three are real, distinct,
# defensible multiples - tighter for scalping, wider for swing/position -
# not placeholders.
_STYLE_PROFILES: dict[str, TradingStyleProfile] = {
    STYLE_SCALPING: TradingStyleProfile(
        style=STYLE_SCALPING,
        label="Scalping",
        stop_distance_ratio=0.5,
        target_distance_ratio=0.6,
        min_risk_reward=1.3,
        expected_holding_period="minutes to ~1 hour",
        description=(
            "Tight stop/target around the same structure-anchored entry a reused "
            "scan already found - for a trader looking to be in and out fast, not "
            "for a fresh lower-timeframe re-scan (this project's primary structure "
            "timeframe stays 15m; see module docstring)."
        ),
        primary_timeframe_hint="1m-5m",
    ),
    STYLE_INTRADAY: TradingStyleProfile(
        style=STYLE_INTRADAY,
        label="Intraday",
        stop_distance_ratio=1.0,
        target_distance_ratio=1.0,
        min_risk_reward=2.0,
        expected_holding_period="hours to ~1 day",
        description="The default: exactly what the live 15m signal pipeline already produces, unscaled.",
        primary_timeframe_hint="15m",
    ),
    STYLE_SWING: TradingStyleProfile(
        style=STYLE_SWING,
        label="Swing",
        stop_distance_ratio=1.8,
        target_distance_ratio=2.5,
        min_risk_reward=2.5,
        expected_holding_period="days to ~2 weeks",
        description="Wider stop and target scaled off the same reused entry/ATR-implied distance, for a multi-day hold.",
        primary_timeframe_hint="1h-4h",
    ),
    STYLE_POSITION: TradingStyleProfile(
        style=STYLE_POSITION,
        label="Position",
        stop_distance_ratio=3.0,
        target_distance_ratio=5.0,
        min_risk_reward=3.0,
        expected_holding_period="weeks to months",
        description="Widest stop/target scaling, for a trend-following hold well beyond the live signal's own horizon.",
        primary_timeframe_hint="1d",
    ),
}

def get_default_style_profile() -> TradingStyleProfile:
    """Public accessor for the INTRADAY profile (today's live-pipeline
    default, ratio 1.0/1.0). Added in Phase 3 so the Decision Engine can
    surface an honest "Expected Holding Time" even for the common case
    where a caller never requested an explicit style - without
    instantiating a full StrategyProfileManager or duplicating the
    catalog above."""
    return _STYLE_PROFILES[STYLE_INTRADAY]


STYLE_ALIASES: dict[str, str] = {
    "scalp": STYLE_SCALPING, "scalping": STYLE_SCALPING, "scalper": STYLE_SCALPING,
    "intraday": STYLE_INTRADAY, "day trade": STYLE_INTRADAY, "day-trade": STYLE_INTRADAY, "daytrading": STYLE_INTRADAY,
    "swing": STYLE_SWING, "swing trade": STYLE_SWING,
    "position": STYLE_POSITION, "long term": STYLE_POSITION, "long-term": STYLE_POSITION, "positional": STYLE_POSITION,
}


@dataclass(frozen=True)
class ResolvedStrategyProfile:
    asset_profile: CalibrationProfile
    style_profile: TradingStyleProfile

    def rescale_levels(
        self, entry_price: float, direction: str, base_stop_loss: float, base_take_profit: float
    ) -> tuple[float, float]:
        """
        Rescales an ALREADY-COMPUTED stop-loss/take-profit pair (from a
        reused report - e.g. build_market_scan_report's own SL/TP) to this
        style's distance ratio, using the reused levels' own distance as the
        unit (no fresh ATR/indicator calculation here - see module
        docstring). No-op (returns the inputs unchanged) when style is
        INTRADAY, since ratio 1.0/1.0 means "already correct".
        """
        stop_distance = abs(entry_price - base_stop_loss) * self.style_profile.stop_distance_ratio
        target_distance = abs(base_take_profit - entry_price) * self.style_profile.target_distance_ratio

        if direction.upper() == "LONG":
            return entry_price - stop_distance, entry_price + target_distance
        return entry_price + stop_distance, entry_price - target_distance


class StrategyProfileManager:
    """
    Single entry point the Trading Agent uses for BOTH profile dimensions.
    Stateless - safe to construct once per request or reuse as a singleton.
    """

    def get_asset_profile(self, symbol: str) -> CalibrationProfile:
        """Delegates to the existing, unmodified per-asset-type calibration profile."""
        return get_profile_for_symbol(symbol)

    def get_style_profile(self, style: Optional[str]) -> TradingStyleProfile:
        """Unknown/None style falls back to INTRADAY - today's live pipeline behavior."""
        if not style:
            return _STYLE_PROFILES[STYLE_INTRADAY]
        key = STYLE_ALIASES.get(style.strip().lower(), style.strip().lower())
        return _STYLE_PROFILES.get(key, _STYLE_PROFILES[STYLE_INTRADAY])

    def resolve(self, symbol: str, style: Optional[str] = None) -> ResolvedStrategyProfile:
        return ResolvedStrategyProfile(
            asset_profile=self.get_asset_profile(symbol),
            style_profile=self.get_style_profile(style),
        )

    @staticmethod
    def detect_style_in_text(text: str) -> Optional[str]:
        """Best-effort keyword match used by the Intent Parser - returns a
        canonical style key (see STYLE_* constants) or None if no style
        keyword appears in the text at all."""
        lowered = text.lower()
        for alias, canonical in STYLE_ALIASES.items():
            if alias in lowered:
                return canonical
        return None
