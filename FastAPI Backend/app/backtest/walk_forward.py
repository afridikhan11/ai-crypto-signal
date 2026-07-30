"""
Walk-Forward Framework - Phase 2 Validation (2026-07-30), objective 5.

STATUS: PRODUCTION (validation tooling, FRAMEWORK ONLY). Additive-only:
reuses `BacktestEngine._resolve_trade` and `SignalGenerator` directly
rather than re-implementing trade resolution - the one simulation loop
this project has stays the one simulation loop.

EXPLICITLY NOT DONE HERE (per Phase 2 objective 5's own constraint: "no
optimization yet, no parameter fitting")
--------------------------------------------------------------------------
This module only SPLITS a historical dataframe into sequential windows and
runs the SAME, UNCHANGED pipeline over each window's in-sample and
out-of-sample slice. It never touches `generator.profile` (calibration),
never searches over a parameter grid, and never selects a "best" window's
config to carry forward. Each window is independent and reported on its
own; comparing in-sample vs out-of-sample results across windows is left
to whoever reads `WalkForwardReport` - this module draws no conclusion
about overfitting or parameter stability, it only produces the honest,
per-window numbers a human (or a later, explicitly-approved phase) would
need to draw one.

WHY THIS DOESN'T CALL `BacktestEngine.run()` DIRECTLY
--------------------------------------------------------------------------
`BacktestEngine.run()` always fetches its own historical window ending at
"now" (`_load_all_series`'s `end_ms = int(time.time() * 1000)` - see
app/backtest/engine.py). It has no parameter for an arbitrary historical
[start, end) range, which a walk-forward split fundamentally requires (each
window is a different past slice, not "the last N days"). Modifying
`BacktestEngine.run()`'s signature is out of this validation phase's scope
(Backtesting Engine logic was excluded from Phase 1 and Phase 2 is
validation-only), so this module instead takes an ALREADY-LOADED dataframe
(however the caller obtained it - a real historical fetch outside this
sandbox, or a stored dataset) and reuses `BacktestEngine._resolve_trade`
(unchanged, real method) plus a real `SignalGenerator` for the walk itself,
so the walk-forward path and the single-shot backtest path share the exact
same trade-resolution rule instead of a second, parallel implementation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pandas as pd
from loguru import logger

from app.backtest.engine import BacktestEngine
from app.backtest.performance_report import PerformanceReport, build_performance_report
from app.strategy.signal_generator import SignalGenerator


@dataclass
class WalkForwardWindow:
    index: int
    train_start: Any
    train_end: Any
    test_start: Any
    test_end: Any
    test_report: PerformanceReport
    test_trade_count: int


@dataclass
class WalkForwardReport:
    symbol: str
    window_count: int
    windows: List[WalkForwardWindow]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "window_count": self.window_count,
            "windows": [
                {
                    "index": w.index,
                    "train_start": str(w.train_start), "train_end": str(w.train_end),
                    "test_start": str(w.test_start), "test_end": str(w.test_end),
                    "test_trade_count": w.test_trade_count,
                    "test_report": w.test_report.to_dict(),
                }
                for w in self.windows
            ],
        }

    def summary(self) -> str:
        lines = [f"Walk-forward report for {self.symbol}: {self.window_count} window(s)."]
        for w in self.windows:
            lines.append(
                f"  Window {w.index}: train [{w.train_start} -> {w.train_end}], "
                f"test [{w.test_start} -> {w.test_end}] "
                f"-> {w.test_trade_count} signal(s), win rate {w.test_report.win_rate_pct}%, "
                f"avg RR {w.test_report.average_realized_rr}"
            )
        return "\n".join(lines)


class WalkForwardRunner:
    """
    Splits `df` into `n_windows` sequential (train, test) slices - each
    test window immediately follows its own train window, and windows do
    not overlap - and runs the UNCHANGED SignalGenerator/BacktestEngine
    trade-resolution logic over each test slice only (the train slice
    exists to size WARMUP_CANDLES lookback for the first test candle, not
    to fit anything - see module docstring).
    """

    def __init__(self, symbol: str, timeframe: str = "15m"):
        self.symbol = symbol.upper()
        self.timeframe = timeframe
        # Reuses the real BacktestEngine's own resolution rule/constants -
        # never a second, parallel implementation of trade simulation.
        self._engine = BacktestEngine(symbol, timeframe)

    def _run_one_window(self, df: pd.DataFrame, test_start_idx: int) -> List[Dict[str, Any]]:
        generator = SignalGenerator(self.symbol)
        trades: List[Dict[str, Any]] = []
        i = test_start_idx
        n = len(df)
        while i < n:
            window = df.iloc[max(0, i - self._engine.WARMUP_CANDLES + 1): i + 1]
            if len(window) < self._engine.WARMUP_CANDLES:
                i += 1
                continue
            current_ts = window.index[-1]
            current_close = float(window["close"].iloc[-1])
            try:
                signal_data = generator.generate(
                    window, "neutral", 0.0, "neutral", "neutral",
                    entry_price=current_close, liquidation_pressure=None, fundamentals=None,
                    htf_trend_1d="neutral", entry_trend_5m="neutral",
                )
            except Exception as e:
                logger.warning(f"[walk-forward {self.symbol}] step failed at {current_ts}: {e}")
                i += 1
                continue

            if signal_data is None:
                i += 1
                continue

            # Reuses BacktestEngine's own resolution rule unchanged.
            outcome, exit_ts, exit_index, realized_rr = self._engine._resolve_trade(df, i + 1, signal_data)
            trades.append({
                "symbol": self.symbol, "direction": signal_data["direction"],
                "entry": signal_data["entry"], "stop_loss": signal_data["stop_loss"],
                "take_profit": signal_data["take_profit"], "confidence": signal_data["confidence"],
                "score_breakdown": signal_data.get("score_breakdown"),
                "outcome": outcome, "realized_rr": realized_rr,
                "entry_time": current_ts.to_pydatetime(),
                "exit_time": exit_ts.to_pydatetime() if exit_ts is not None else None,
            })
            i = exit_index if exit_index is not None else i + 1
        return trades

    def run(self, df: pd.DataFrame, n_windows: int = 4) -> WalkForwardReport:
        """
        `df` must already have at least `WARMUP_CANDLES` extra rows before
        the first window's test slice begins (same warmup convention
        BacktestEngine.run() uses). Windows are non-overlapping and
        sequential: window k's train slice is immediately followed by its
        own test slice, and window k+1 starts strictly after window k's
        test slice ends.
        """
        if n_windows < 1:
            raise ValueError("n_windows must be at least 1.")

        warmup = self._engine.WARMUP_CANDLES
        usable = len(df) - warmup
        if usable < n_windows:
            raise ValueError(
                f"Not enough candles ({len(df)}) for {n_windows} window(s) with a "
                f"{warmup}-candle warmup - need at least {warmup + n_windows}."
            )

        window_size = usable // n_windows
        windows: List[WalkForwardWindow] = []

        for k in range(n_windows):
            train_start_idx = k * window_size
            test_start_idx = warmup + train_start_idx
            test_end_idx = len(df) if k == n_windows - 1 else warmup + (k + 1) * window_size

            test_df = df.iloc[: test_end_idx]  # trailing history up to this window's end, for warmup
            trades = self._run_one_window(test_df, test_start_idx)
            report = build_performance_report(trades)

            windows.append(WalkForwardWindow(
                index=k,
                train_start=df.index[train_start_idx],
                train_end=df.index[test_start_idx - 1],
                test_start=df.index[test_start_idx],
                test_end=df.index[test_end_idx - 1],
                test_report=report,
                test_trade_count=len(trades),
            ))

        return WalkForwardReport(symbol=self.symbol, window_count=n_windows, windows=windows)
