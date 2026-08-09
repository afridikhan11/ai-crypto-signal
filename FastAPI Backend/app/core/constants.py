"""
Shared cross-module constants.

Kept separate from config.py (runtime settings loaded from env) because
these are fixed classification data, not configuration - the same symbol
list is needed by app.main (to build the scanner), app.scheduler.scanner
(to tag newly-created Coin rows), and anywhere else that needs to tell a
commodity future apart from a crypto pair without hardcoding the list
again and risking drift.
"""

# Binance USD-M Futures commodity contracts (launched Jan-Apr 2026):
# XAUUSDT (Gold, 5 Jan 2026), XAGUSDT (Silver, 7 Jan 2026),
# CLUSDT (WTI Crude, 1 Apr 2026), BZUSDT (Brent Crude, 1 Apr 2026).
# Fetched through the exact same fapi.binance.com REST/WebSocket paths as
# every crypto pair - no separate data provider needed.
COMMODITY_SYMBOLS = {"XAUUSDT", "XAGUSDT", "CLUSDT", "BZUSDT"}

ASSET_CLASS_CRYPTO = "crypto"
ASSET_CLASS_COMMODITY = "commodity"

# --------------------------------------------------------------------------
# ICT Pending Limit Entry (2026-07-30)
# --------------------------------------------------------------------------
# How many PRIMARY-timeframe (15m) candles a pending ICT entry stays valid
# before it EXPIRES unfilled. 12 x 15m = 3 hours: long enough for a genuine
# retracement into the OTE/order-block zone to occur, short enough that the
# setup dies with the structural context that produced it rather than
# lingering into a market that has moved on.
#
# ONE definition, read by the live monitor (app/scheduler/signal_monitor.py)
# and by the backtest fill simulation (app/backtest/engine.py), so live and
# backtested expiry can never disagree - the same "one rule, two callers"
# discipline used for TP/SL resolution.
# 48 (12h on a 15m chart), set from the fill-window sweep
# (scripts/sweep_fill_window.py, BTC/ETH/SOL, 15m, 180d): widening the window
# 12 -> 48 raised the fill rate (~3.9% -> ~5.4%) and average realised R:R
# (0.37 -> 0.43) with no drop in win rate, i.e. more setups actually fill and
# the ones that do have a better reward/risk - a Pareto improvement. (96 gave
# an even better R:R but means a 24h-stale pending entry; 48 is the balanced
# point.) NOTE: this improves fill/RR, it does not by itself make the pipeline
# profitable - signal quality / confidence is the larger, separate issue.
PENDING_ENTRY_EXPIRY_CANDLES = 48
PENDING_ENTRY_EXPIRY_MINUTES = PENDING_ENTRY_EXPIRY_CANDLES * 15


def asset_class_for_symbol(symbol: str) -> str:
    return ASSET_CLASS_COMMODITY if symbol.upper() in COMMODITY_SYMBOLS else ASSET_CLASS_CRYPTO


# --------------------------------------------------------------------------
# Fine-grained ASSET TYPE (2026-07-26 addition) - a finer split than
# ASSET_CLASS_* above. ASSET_CLASS_COMMODITY lumps Gold/Silver/Oil together
# (used where the distinction genuinely doesn't matter, e.g. AIScorer's
# "skip crypto-only market-wide signals" gate, or the Gold Signal tab's
# API filter). ASSET_TYPE_* below distinguishes Gold from Silver from Oil
# specifically, because per the user's explicit instruction, each of these
# now gets its OWN calibration profile (see app/ai/calibration_profiles.py)
# - Gold and Oil are different enough markets that they shouldn't share one
# set of indicator weights/ATR multipliers/thresholds just because they're
# both "not crypto".
#
# ASSET_TYPE_FOREX exists as a placeholder for a future asset class with no
# live symbols yet - nothing routes to it today (SYMBOL_TO_ASSET_TYPE has
# no forex entries), but the calibration profile architecture is ready the
# moment a forex symbol is added, per the user's explicit "Forex (future)"
# requirement.
# --------------------------------------------------------------------------
ASSET_TYPE_CRYPTO = "crypto"
ASSET_TYPE_GOLD = "gold"
ASSET_TYPE_SILVER = "silver"
ASSET_TYPE_OIL = "oil"
ASSET_TYPE_FOREX = "forex"

SYMBOL_TO_ASSET_TYPE: dict[str, str] = {
    "XAUUSDT": ASSET_TYPE_GOLD,
    "XAGUSDT": ASSET_TYPE_SILVER,
    "CLUSDT": ASSET_TYPE_OIL,
    "BZUSDT": ASSET_TYPE_OIL,
}


def asset_type_for_symbol(symbol: str) -> str:
    """Any symbol not in SYMBOL_TO_ASSET_TYPE is treated as crypto - matches
    asset_class_for_symbol()'s same "everything else is crypto" default."""
    return SYMBOL_TO_ASSET_TYPE.get(symbol.upper(), ASSET_TYPE_CRYPTO)


# GoPlus Security's numeric chain IDs for EVM chains (their API uses these,
# not chain names) - same chain-name convention as
# geckoterminal_service.CHAIN_TO_GECKOTERMINAL_NETWORK, so a caller only
# has to pick one chain name and every service maps it internally.
#
# Moved here from app.services.token_security_service on 2026-07-26 so
# app.services.contract_security_service (which also needs this mapping)
# doesn't have to import it from a sibling service module - both now pull
# it from this shared, dependency-free constants module instead. Pure
# relocation: values unchanged, no behavior change.
EVM_CHAIN_TO_GOPLUS_ID: dict[str, str] = {
    "ethereum": "1",
    "bsc": "56",
    "polygon": "137",
    "arbitrum": "42161",
    "optimism": "10",
    "avalanche": "43114",
    "base": "8453",
}
