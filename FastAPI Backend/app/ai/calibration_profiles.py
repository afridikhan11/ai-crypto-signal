"""
Per-asset-class calibration profiles for AIScorer + SignalGenerator.

--------------------------------------------------------------------------
BACKGROUND (2026-07-26)
--------------------------------------------------------------------------
Before this module existed, AIScorer/SignalGenerator used ONE global set
of thresholds and weights for every symbol - crypto, Gold, Silver, and Oil
all shared the same MIN_CONFIDENCE=85, MIN_RISK_REWARD=2.0, ATR multiples,
volatility thresholds, volume-spike multiplier, and calibrated-weights
file. Investigation showed the Gold Signal tab was empty not because of a
bug, but because Gold/Silver/Oil (only 4 symbols total, vs ~50 crypto
pairs) had never once cleared the same strict confidence bar tuned mainly
against crypto's much larger, much more liquid sample - the highest
confidence ever recorded for XAUUSDT across all logs was 73, against an
85 minimum.

The user's explicit instruction was: do NOT simply lower the thresholds.
Instead, give each asset class its OWN calibration profile (own weights,
own ATR multipliers, own volatility/volume thresholds, own confidence/
risk-reward gates), auto-detected by symbol, so tuning one asset class
never touches another - and crucially, do not change any actual values
yet. Every profile below is initialized to the EXACT values the single
global config used before this refactor, so today's live behavior for
every symbol (crypto included) is byte-for-byte unchanged. Only after
reviewing real near-miss statistics (see the diagnostics report this
shipped alongside) should any profile's numbers actually be adjusted -
and only for the specific asset class the data supports changing.
--------------------------------------------------------------------------
WHAT EACH PROFILE CONTROLS
--------------------------------------------------------------------------
  - default_weights / weights_file: AIScorer's per-category score weights
    (see app/ai/scorer.py), and where this profile's own auto-calibrated
    weights (once enough of ITS OWN closed trades exist - see
    app/ai/calibration_profiles.py's calibrate_weights_for_profile) get
    written/read from. Never mixed across asset types.
  - min_confidence / min_risk_reward: SignalGenerator's two scored hard
    gates - a signal is rejected below either bar regardless of how good
    everything else looks.
  - stop_loss_atr_multiple / ob_invalidation_atr_buffer /
    min_target_distance_atr / take_profit_fallback_atr_multiple: the ATR-
    scaled distances SignalGenerator uses to build the stop-loss and
    take-profit when there's no cleaner structural level to anchor to.
  - volatility_lookback / volatility_high_ratio / volatility_low_ratio:
    SignalGenerator._classify_volatility()'s regime thresholds (current
    ATR vs its own recent baseline).
  - volume_spike_multiplier: the multiple of the 20-period average volume
    that counts as a "spike" (see app/indicators/confirmation.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict

from app.ai.calibration import DEFAULT_WEIGHTS
from app.core.constants import (
    ASSET_TYPE_CRYPTO,
    ASSET_TYPE_GOLD,
    ASSET_TYPE_SILVER,
    ASSET_TYPE_OIL,
    ASSET_TYPE_FOREX,
    asset_type_for_symbol,
)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


@dataclass(frozen=True)
class CalibrationProfile:
    asset_type: str
    default_weights: Dict[str, float]
    weights_file: Path

    # Confidence / risk-reward gates (SignalGenerator hard gates)
    min_confidence: int
    min_risk_reward: float

    # ATR-scaled structural distances (SignalGenerator SL/TP construction)
    stop_loss_atr_multiple: float
    ob_invalidation_atr_buffer: float
    min_target_distance_atr: float
    take_profit_fallback_atr_multiple: float

    # Volatility regime thresholds (SignalGenerator._classify_volatility)
    volatility_lookback: int
    volatility_high_ratio: float
    volatility_low_ratio: float

    # Volume threshold (ConfirmationIndicators volume_spike)
    volume_spike_multiplier: float

    # Data-driven latest-ICT confluence bonus (see matrix backtest,
    # scripts/backtest_matrix.py + scripts/evaluate_confluence_lift.py). Maps a
    # confluence-engine name (exactly as emitted by
    # app/smc/latest_ict_confluence.py) to the confidence points it ADDS when
    # its directional signal agrees with the trade direction. Only engines whose
    # edge was proven ON THIS ASSET CLASS get a non-zero weight; every other
    # engine (and every asset with an empty map) contributes nothing, so live
    # behavior there is byte-for-byte unchanged. The total bonus across all
    # aligned engines is capped at confluence_bonus_cap.
    confluence_weights: Dict[str, float] = field(default_factory=dict)
    confluence_bonus_cap: float = 8.0


def _profile(
    asset_type: str,
    weights_filename: str,
    confluence_weights: Dict[str, float] | None = None,
) -> CalibrationProfile:
    """
    Every numeric value here is this project's EXISTING, already-tuned
    default (previously hardcoded as global constants in
    app/strategy/signal_generator.py, app/ai/scorer.py, and
    app/indicators/confirmation.py). Creating a separate profile per asset
    class does NOT change any live behavior today - see this module's
    docstring. Each profile gets its own calibrated-weights filename so a
    future auto-calibration run for one asset class can never be
    influenced by another's trade outcomes (see calibrate_weights_for_profile
    below).
    """
    return CalibrationProfile(
        asset_type=asset_type,
        default_weights=dict(DEFAULT_WEIGHTS),
        weights_file=DATA_DIR / weights_filename,
        min_confidence=85,
        min_risk_reward=2.0,
        stop_loss_atr_multiple=1.5,
        ob_invalidation_atr_buffer=0.1,
        min_target_distance_atr=1.5,
        take_profit_fallback_atr_multiple=3.0,
        volatility_lookback=50,
        volatility_high_ratio=1.5,
        volatility_low_ratio=0.7,
        volume_spike_multiplier=1.5,
        confluence_weights=dict(confluence_weights or {}),
    )


# Data-driven per-asset confluence weights.
#
# Sized from the CONFLUENCE-LIFT test (scripts/evaluate_confluence_lift.py, 1h,
# BTC/ETH/SOL and XAUUSDT), which measures each engine's forward-return edge
# *when it agrees with the core Group A read* (market-structure bias) - i.e. the
# exact condition under which this bonus fires live (engine bias == trade
# direction). This is the right basis: an engine that looks flat ALONE (in the
# matrix --mode new test) can still be a strong confirmation in context.
#
# Findings (in-context / +structure expectancy, larger = better):
#   Crypto: IPDA Range +0.50 was the biggest; Turtle Soup +0.35; Judas Swing
#     (NY) already had standalone edge and improved further; Rejection Block,
#     Mitigation Block and Power of 3 all FLIPPED from ~0/negative standalone to
#     clearly positive in context. Judas Swing (London) stayed negative and
#     Liquidity Void had too few samples -> both excluded.
#   Gold: Power of 3 dominated (+0.24); IPDA Range, Rejection Block and
#     Mitigation Block were positive in context; Judas Swing and Turtle Soup do
#     NOT work on gold (negative) -> excluded. (Note this is the mirror image of
#     crypto, which is exactly why the maps are per-asset.)
#
# Weights are edge-proportional and intentionally easy to retune here as more
# data arrives. Engine names must match app/smc/latest_ict_confluence.py exactly.
_CRYPTO_CONFLUENCE_WEIGHTS = {
    "IPDA Range": 5.0,
    "Judas Swing (new_york)": 3.0,
    "Turtle Soup": 3.0,
    "Mitigation Block": 2.0,
    "Rejection Block": 2.0,
    "Power of 3": 2.0,
}
_GOLD_CONFLUENCE_WEIGHTS = {
    "Power of 3": 5.0,
    "IPDA Range": 2.0,
    "Rejection Block": 2.0,
    "Mitigation Block": 1.0,
}

CRYPTO_PROFILE = _profile(ASSET_TYPE_CRYPTO, "ai_scorer_weights_crypto.json",
                          _CRYPTO_CONFLUENCE_WEIGHTS)
GOLD_PROFILE = _profile(ASSET_TYPE_GOLD, "ai_scorer_weights_gold.json",
                        _GOLD_CONFLUENCE_WEIGHTS)
SILVER_PROFILE = _profile(ASSET_TYPE_SILVER, "ai_scorer_weights_silver.json")
OIL_PROFILE = _profile(ASSET_TYPE_OIL, "ai_scorer_weights_oil.json")
# No forex symbols exist yet (see app.core.constants.SYMBOL_TO_ASSET_TYPE) -
# this profile is unreachable today, ready for when one is added.
FOREX_PROFILE = _profile(ASSET_TYPE_FOREX, "ai_scorer_weights_forex.json")

_PROFILES_BY_ASSET_TYPE: Dict[str, CalibrationProfile] = {
    ASSET_TYPE_CRYPTO: CRYPTO_PROFILE,
    ASSET_TYPE_GOLD: GOLD_PROFILE,
    ASSET_TYPE_SILVER: SILVER_PROFILE,
    ASSET_TYPE_OIL: OIL_PROFILE,
    ASSET_TYPE_FOREX: FOREX_PROFILE,
}


# --------------------------------------------------------------------------
# TEMPORARY Binance Futures TESTNET validation override (2026-07-30)
# --------------------------------------------------------------------------
# The single source of truth for "what confidence threshold does Testnet
# validation use". Every asset class's CalibrationProfile.min_confidence
# stays 85 (unchanged) - this constant is applied as a runtime override
# ONLY by UniversalScanner, ONLY when saved Binance credentials are
# pointed at Testnet (see UniversalScanner._apply_testnet_confidence_override
# in app/scheduler/universal_scanner.py). MAINNET reads each profile's own
# min_confidence=85 untouched. BACKTEST never calls that override method at
# all (BacktestEngine builds its own SignalGenerator directly - see
# app/backtest/engine.py) and always uses each profile's real 85, or an
# explicit min_confidence request param unrelated to this constant.
#
# This exists so 60 is hardcoded in exactly ONE place. Remove this
# constant and its callers (UniversalScanner._apply_testnet_confidence_
# override, and effective_min_confidence() below) once Testnet validation
# is complete.
TESTNET_MIN_CONFIDENCE = 60


def effective_min_confidence(base_min_confidence: int) -> int:
    """
    The SAME environment-aware resolution UniversalScanner's live scanner
    already applies (see UniversalScanner._apply_testnet_confidence_override
    in app/scheduler/universal_scanner.py), exposed here so any OTHER
    ADVISORY-ONLY caller can report a threshold that always matches what
    the live scanner is actually gating on right now, without each caller
    duplicating its own copy of the environment-detection logic.

    Returns TESTNET_MIN_CONFIDENCE when saved Binance credentials
    (app.services.binance_credentials - the same source UniversalScanner,
    account.py, dashboard.py, and trading_control.py already use) are
    pointed at Testnet. Returns `base_min_confidence` unchanged for
    Mainnet, for no saved credentials at all, or for any caller (like
    BacktestEngine) that never calls this function in the first place.

    Local import of binance_credentials, not a module-level one: this is
    a low-level, early-imported config module and should not carry a
    load-time dependency on the credentials/security stack - only paid
    when a caller actually resolves a threshold.
    """
    from app.services import binance_credentials

    creds = binance_credentials.load_credentials()
    if creds and creds.get("testnet"):
        return TESTNET_MIN_CONFIDENCE
    return base_min_confidence


def get_profile_for_symbol(symbol: str) -> CalibrationProfile:
    """The AI auto-detects the asset type from the symbol (see
    app.core.constants.asset_type_for_symbol) and loads the matching
    profile - no caller ever has to know or pass the asset type itself."""
    return _PROFILES_BY_ASSET_TYPE[asset_type_for_symbol(symbol)]


def all_profiles() -> Dict[str, CalibrationProfile]:
    """Every registered profile, keyed by asset_type - used by the
    diagnostics report and by calibrate_all_profiles()."""
    return dict(_PROFILES_BY_ASSET_TYPE)
