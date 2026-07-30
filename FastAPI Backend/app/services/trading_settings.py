"""
Small persisted trading preferences (risk_percent, used for position-size
suggestions - see app/services/position_sizing.py; and trade management,
see app/scheduler/signal_monitor.py). Same lightweight JSON-file pattern as
app/ai/calibration.py's weights file, since this project has no
user-settings table/migration set up yet.

2026-07-29: generalized from a single-key file to a read-modify-write
settings dict so a new key can be added without clobbering an existing one.
`set_risk_percent()` previously wrote `{"risk_percent": value}` wholesale,
which would have silently dropped any sibling key the moment a second
setting existed. Files written by the old single-key version load
unchanged - `risk_percent` is read the same way, and any key absent from
an older file falls back to its documented default.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from app.core.logging import logger

SETTINGS_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "trading_settings.json"

# % of account balance suggested to risk on a single position.
DEFAULT_RISK_PERCENT = 1.0

# Whether SignalMonitor applies ICT Trade Management to open signals
# (breakeven + structure trailing + structure-failure closure - see
# app/scheduler/signal_monitor.py). Defaults ON: every action it can take
# either tightens the stop toward profit or closes a structurally-failed
# trade, so it can only ever reduce the risk on an open position, never
# widen it (enforced by TradeManagementEngine's own forward-only trailing
# invariant). Exposed as a setting so it can be disabled without a code
# change if a user wants purely static stops.
DEFAULT_TRADE_MANAGEMENT_ENABLED = True

# Auto Trading Control Panel (2026-07-30) - two INDEPENDENT switches:
#
#   auto_trading_enabled - master gate on real order PLACEMENT. Checked by
#       POST /trading/execute/{signal_id} (app/api/v1/endpoints/trading.py)
#       before the RiskEngine gate even runs. Defaults True so this is
#       purely additive - every signal that could be executed before this
#       feature existed still can be, until a user explicitly flips it off
#       from the new Auto Trading Control panel.
#
#   engine_run_state - controls whether the SCANNER (new signal generation,
#       UniversalScanner.on_new_candle) and the SIGNAL MONITOR (trade
#       management / TP-SL resolution, SignalMonitor.check_active_signals)
#       do any work on a given tick. "paused"/"stopped" both mean "do
#       nothing this tick" - the distinction is purely how the control
#       panel labels/enables its own buttons, there is no behavioral
#       difference between them today. Neither state touches anything
#       already resting on the exchange: an executed signal's real
#       stop-loss/take-profit orders keep protecting the position exactly
#       as placed regardless of this flag - pausing only freezes the
#       platform's own decision-making (new signals, trailing stops,
#       structure-failure closes), it never removes existing protection.
#       Defaults "running" so this is also purely additive - the scanner
#       and monitor behave exactly as before until a user explicitly
#       pauses/stops from the panel.
DEFAULT_AUTO_TRADING_ENABLED = True
DEFAULT_ENGINE_RUN_STATE = "running"
VALID_ENGINE_RUN_STATES = {"running", "paused", "stopped"}

# ICT Pending Limit Entry migration (2026-07-30) - the SINGLE switch every
# subsystem reads to decide which entry model is in force. There is exactly
# one of these process-wide, so the platform is always wholly one mode or
# the other and can never be half market-entry / half pending-entry:
#
#   "ict_pending" (default, the migrated behaviour) - a signal is created
#       at the ICT entry_plan price (OTE / Order Block / FVG / Supply-Demand
#       zone) with status PENDING_ENTRY. It becomes ACTIVE only when price
#       actually trades into that zone. Auto Trading places a resting LIMIT
#       entry order, and the protective stop-loss/take-profit are placed
#       only AFTER that entry fills (reduceOnly orders cannot exist before
#       a position does). Because a LIMIT order fills AT its limit price,
#       `Signal.entry_price` equals the real fill price, which is what makes
#       position sizing (quantity = risk_usd / |entry - stop|), breakeven at
#       1R, structure trailing and backtest RR correct BY CONSTRUCTION.
#
#   "market" - the pre-migration behaviour, kept as a deliberate operating
#       mode (not a compatibility shim): the signal is created ACTIVE at the
#       live price and Auto Trading places a MARKET entry bracket. Selecting
#       this switches every subsystem back together, atomically.
#
# Signals already in flight keep the semantics they were created with: a
# PENDING_ENTRY row is always driven by the pending state machine and an
# ACTIVE row is always managed as an open trade, whatever this flag says
# later. The flag only decides how the NEXT signal is created and executed.
ENTRY_MODE_MARKET = "market"
ENTRY_MODE_ICT_PENDING = "ict_pending"
VALID_ENTRY_MODES = {ENTRY_MODE_MARKET, ENTRY_MODE_ICT_PENDING}
DEFAULT_ENTRY_MODE = ENTRY_MODE_ICT_PENDING


def _read_settings() -> Dict[str, Any]:
    if not SETTINGS_FILE.exists():
        # Expected on first run before the user has ever saved a setting -
        # not an error, so no warning here.
        return {}
    try:
        data = json.loads(SETTINGS_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(
            f"Could not read trading settings from {SETTINGS_FILE} "
            f"({type(e).__name__}: {e}) - falling back to defaults."
        )
        return {}


def _write_setting(key: str, value: Any) -> None:
    """Read-modify-write so writing one setting never drops another."""
    settings = _read_settings()
    settings[key] = value
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2))


def get_risk_percent() -> float:
    raw_value = _read_settings().get("risk_percent", DEFAULT_RISK_PERCENT)
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        logger.warning(
            f"Saved risk_percent ({raw_value!r}) is not a number - falling back to "
            f"default {DEFAULT_RISK_PERCENT}%. This affects real position sizing "
            f"(see position_sizing.py) - fix the value via the Settings screen."
        )
        return DEFAULT_RISK_PERCENT

    if not (0 < value <= 100):
        logger.warning(
            f"Saved risk_percent ({raw_value}) is out of the valid (0, 100] "
            f"range - falling back to default {DEFAULT_RISK_PERCENT}%. This "
            f"affects real position sizing (see position_sizing.py) - fix "
            f"the value via the Settings screen."
        )
        return DEFAULT_RISK_PERCENT
    return value


def set_risk_percent(value: float) -> float:
    if not (0 < value <= 100):
        raise ValueError("risk_percent must be greater than 0 and at most 100")
    _write_setting("risk_percent", value)
    return value


def get_trade_management_enabled() -> bool:
    raw_value = _read_settings().get("trade_management_enabled", DEFAULT_TRADE_MANAGEMENT_ENABLED)
    if isinstance(raw_value, bool):
        return raw_value
    logger.warning(
        f"Saved trade_management_enabled ({raw_value!r}) is not a boolean - falling "
        f"back to default {DEFAULT_TRADE_MANAGEMENT_ENABLED}."
    )
    return DEFAULT_TRADE_MANAGEMENT_ENABLED


def set_trade_management_enabled(value: bool) -> bool:
    if not isinstance(value, bool):
        raise ValueError("trade_management_enabled must be a boolean")
    _write_setting("trade_management_enabled", value)
    return value


def get_auto_trading_enabled() -> bool:
    raw_value = _read_settings().get("auto_trading_enabled", DEFAULT_AUTO_TRADING_ENABLED)
    if isinstance(raw_value, bool):
        return raw_value
    logger.warning(
        f"Saved auto_trading_enabled ({raw_value!r}) is not a boolean - falling back "
        f"to default {DEFAULT_AUTO_TRADING_ENABLED}."
    )
    return DEFAULT_AUTO_TRADING_ENABLED


def set_auto_trading_enabled(value: bool) -> bool:
    if not isinstance(value, bool):
        raise ValueError("auto_trading_enabled must be a boolean")
    _write_setting("auto_trading_enabled", value)
    return value


def get_engine_run_state() -> str:
    raw_value = _read_settings().get("engine_run_state", DEFAULT_ENGINE_RUN_STATE)
    if isinstance(raw_value, str) and raw_value in VALID_ENGINE_RUN_STATES:
        return raw_value
    logger.warning(
        f"Saved engine_run_state ({raw_value!r}) is not one of {sorted(VALID_ENGINE_RUN_STATES)} - "
        f"falling back to default {DEFAULT_ENGINE_RUN_STATE!r}."
    )
    return DEFAULT_ENGINE_RUN_STATE


def set_engine_run_state(value: str) -> str:
    if value not in VALID_ENGINE_RUN_STATES:
        raise ValueError(f"engine_run_state must be one of {sorted(VALID_ENGINE_RUN_STATES)}")
    _write_setting("engine_run_state", value)
    return value


def get_entry_mode() -> str:
    """The one entry-model switch - see VALID_ENTRY_MODES above."""
    raw_value = _read_settings().get("entry_mode", DEFAULT_ENTRY_MODE)
    if isinstance(raw_value, str) and raw_value in VALID_ENTRY_MODES:
        return raw_value
    logger.warning(
        f"Saved entry_mode ({raw_value!r}) is not one of {sorted(VALID_ENTRY_MODES)} - "
        f"falling back to default {DEFAULT_ENTRY_MODE!r}."
    )
    return DEFAULT_ENTRY_MODE


def set_entry_mode(value: str) -> str:
    if value not in VALID_ENTRY_MODES:
        raise ValueError(f"entry_mode must be one of {sorted(VALID_ENTRY_MODES)}")
    _write_setting("entry_mode", value)
    return value


def is_ict_pending_entry() -> bool:
    """Convenience read used across the scanner/monitor/execution path."""
    return get_entry_mode() == ENTRY_MODE_ICT_PENDING
