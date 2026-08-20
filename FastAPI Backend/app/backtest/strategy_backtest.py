"""
Backtest adapter for Smart AI BaseStrategy implementations.

Walks a historical OHLCV frame bar by bar, asks the strategy to `evaluate` the
data available up to each bar, and when a signal fires simulates the trade
forward with realistic frictions - taker fees on every fill, entry/exit
slippage, and per-bar funding - resolving at the first stop/target touch (stop
takes priority on a bar that trades through both, the conservative choice).

Partial fills are modelled when a signal carries a laddered `targets` list: a
fraction is taken at the equilibrium partial, the stop moves to breakeven, and
the runner resolves at the final target or breakeven. Output is the same
`trades` dict shape the rest of app/backtest consumes (`realized_rr`,
`outcome`, ...), so app/backtest/strategy_metrics.py turns it into a report.

Funding note: like BacktestEngine, there is no free historical "funding as of
T" series, so `funding_pct_per_bar` is a flat modelling assumption (default 0),
disclosed rather than fabricated.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pandas as pd

from app.models.signal import Direction
from app.strategy.base_strategy import BaseStrategy, MarketData

_PARTIAL_FRACTION = 0.5  # fraction taken at the equilibrium partial target


@dataclass
class BacktestCosts:
    fee_pct: float = 0.04        # taker fee per fill, % of notional
    slippage_pct: float = 0.02   # adverse price move per fill, %
    funding_pct_per_bar: float = 0.0  # funding cost per bar held, % (disclosed assumption)


def _first_touch(df: pd.DataFrame, start: int, direction: Direction, stop: float, target: float):
    """First bar from `start` that touches stop or target. Stop wins on a bar
    that trades through both (conservative). Returns (outcome, price, idx) or
    (None, None, None) if neither is reached by the end of the frame."""
    for i in range(start, len(df)):
        high = float(df["high"].iloc[i])
        low = float(df["low"].iloc[i])
        if direction is Direction.SHORT:
            if high >= stop:
                return "STOPPED", stop, i
            if low <= target:
                return "TP_HIT", target, i
        else:
            if low <= stop:
                return "STOPPED", stop, i
            if high >= target:
                return "TP_HIT", target, i
    return None, None, None


def _price_pnl(direction: Direction, entry: float, exit_price: float) -> float:
    return (entry - exit_price) if direction is Direction.SHORT else (exit_price - entry)


def _costs_price(entry: float, exit_price: float, bars_held: int, costs: BacktestCosts, fills: int) -> float:
    """Total friction in price units per unit position: `fills` taker fees +
    slippage on entry and each exit + funding over the hold."""
    fee = costs.fee_pct / 100.0
    slip = costs.slippage_pct / 100.0
    funding = costs.funding_pct_per_bar / 100.0
    fee_cost = fee * entry + fee * exit_price * (fills - 1)  # entry fill + (fills-1) exit fills
    slippage_cost = slip * entry + slip * exit_price * (fills - 1)
    funding_cost = funding * entry * max(0, bars_held)
    return fee_cost + slippage_cost + funding_cost


def _simulate(df: pd.DataFrame, entry_idx: int, signal) -> Optional[Dict[str, Any]]:
    direction = signal.direction
    entry = signal.entry_price
    stop = signal.stop_loss
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    start = entry_idx + 1
    if start >= len(df):
        return None

    partial = len(signal.targets) >= 2
    if not partial:
        outcome, exit_price, exit_i = _first_touch(df, start, direction, stop, signal.take_profit)
        if outcome is None:
            return None
        bars = exit_i - entry_idx
        gross = _price_pnl(direction, entry, exit_price)
        net = gross - _costs_price(entry, exit_price, bars, _dummy_costs(), fills=2)
        return _trade(signal, outcome, net / risk, entry_idx, exit_i)

    # Laddered: try the equilibrium partial first; if hit, bank a fraction and
    # move the stop to breakeven for the runner.
    partial_target, final_target = signal.targets[0], signal.targets[-1]
    out1, px1, i1 = _first_touch(df, start, direction, stop, partial_target)
    if out1 is None:
        return None
    if out1 == "STOPPED":
        bars = i1 - entry_idx
        gross = _price_pnl(direction, entry, px1)
        net = gross - _costs_price(entry, px1, bars, _dummy_costs(), fills=2)
        return _trade(signal, "STOPPED", net / risk, entry_idx, i1)

    # Partial banked at px1; runner from i1+1 with stop at breakeven (entry).
    out2, px2, i2 = _first_touch(df, i1 + 1, direction, entry, final_target)
    if out2 is None:
        out2, px2, i2 = "STOPPED", entry, len(df) - 1  # unresolved runner -> flat at breakeven
    bars = (i2 if i2 else i1) - entry_idx
    gross = _PARTIAL_FRACTION * _price_pnl(direction, entry, px1) + (1 - _PARTIAL_FRACTION) * _price_pnl(direction, entry, px2)
    net = gross - _costs_price(entry, px2, bars, _dummy_costs(), fills=3)  # entry + 2 exits
    # A trade whose runner closed at breakeven but banked a partial is still a win.
    outcome = "TP_HIT" if net > 0 else "STOPPED"
    return _trade(signal, outcome, net / risk, entry_idx, i2)


# Module-level default costs, swapped per-run via a context dict on the frame.
_CURRENT_COSTS = {"costs": BacktestCosts()}


def _dummy_costs() -> BacktestCosts:
    return _CURRENT_COSTS["costs"]


def _trade(signal, outcome, realized_rr, entry_idx, exit_idx) -> Dict[str, Any]:
    return {
        "symbol": signal.symbol,
        "direction": signal.direction.value,
        "entry": signal.entry_price,
        "stop_loss": signal.stop_loss,
        "take_profit": signal.take_profit,
        "confidence": signal.confidence,
        "outcome": outcome,
        "realized_rr": round(realized_rr, 4),
        "entry_index": entry_idx,
        "exit_index": exit_idx,
    }


async def backtest_strategy(
    strategy: BaseStrategy,
    df: pd.DataFrame,
    *,
    symbol: str = "BTCUSDT",
    htf_dataframes: Optional[Dict[str, pd.DataFrame]] = None,
    costs: BacktestCosts = BacktestCosts(),
    warmup: int = 50,
    funding_rate: Optional[float] = None,
    cooldown_bars: int = 3,
) -> List[Dict[str, Any]]:
    """Run `strategy` over `df` and return the simulated trades. `cooldown_bars`
    suppresses re-entry for a few bars after a signal so one setup is not
    counted on every subsequent bar it remains valid."""
    _CURRENT_COSTS["costs"] = costs
    trades: List[Dict[str, Any]] = []
    last_entry = -10_000
    for i in range(max(warmup, 1), len(df) - 1):
        if i - last_entry < cooldown_bars:
            continue
        window = df.iloc[: i + 1]
        md = MarketData(
            symbol=symbol,
            dataframe=window,
            htf_dataframes={k: v[v.index <= window.index[-1]] for k, v in (htf_dataframes or {}).items()},
            funding_rate=funding_rate,
            current_price=float(window["close"].iloc[-1]),
        )
        signal = await strategy.evaluate(md)
        if signal is None:
            continue
        trade = _simulate(df, i, signal)
        if trade is not None:
            trades.append(trade)
            last_entry = i
    return trades


async def walk_forward_backtest(
    strategy: BaseStrategy,
    df: pd.DataFrame,
    *,
    windows: int = 4,
    **kwargs: Any,
) -> List[List[Dict[str, Any]]]:
    """Split `df` into `windows` sequential, non-overlapping slices and backtest
    each independently - a real out-of-sample walk, never a single in-sample
    fit. Returns the per-window trade lists (report each on its own)."""
    n = len(df)
    if windows < 1 or n < windows:
        return [await backtest_strategy(strategy, df, **kwargs)]
    size = n // windows
    out = []
    for w in range(windows):
        start = w * size
        end = n if w == windows - 1 else (w + 1) * size
        out.append(await backtest_strategy(strategy, df.iloc[start:end].reset_index(drop=False), **kwargs))
    return out
