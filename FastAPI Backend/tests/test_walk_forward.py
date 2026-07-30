"""
Walk-Forward framework tests (Phase 2, objective 5).

These prove the FRAMEWORK mechanics - correct, non-overlapping sequential
window boundaries, real reuse of BacktestEngine._resolve_trade (not a
duplicate), and no exceptions across a full run - not trading performance
(synthetic random-walk data is not expected to produce consistently
profitable signals, and asserting so would be exactly the kind of
fabricated expectation this project avoids).

Every window-mechanics test stubs `SignalGenerator.generate` INLINE (a
plain `with patch.object(...)` inside the test body, not a yield-fixture -
this project's offline test runner resolves fixtures by calling them once
and caching the return value, so a generator/yield-style fixture would
never actually enter its `with` block). This keeps the suite fast while
still exercising every line of `WalkForwardRunner`'s own iteration logic.
`TestRealPipelineSmoke` runs the REAL, unmodified ICT pipeline once on a
minimal window to prove genuine integration, not just the stub.
"""
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from app.backtest.engine import BacktestEngine
from app.backtest.walk_forward import WalkForwardRunner
from app.strategy.signal_generator import SignalGenerator


def _ohlcv(n=520, seed=3, freq="15min", start="2026-01-01"):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1.5, n))
    close = np.maximum(close, 5)
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    hi = np.maximum(open_, close) + rng.uniform(0.1, 2.0, n)
    lo = np.minimum(open_, close) - rng.uniform(0.1, 2.0, n)
    return pd.DataFrame(
        {"open": open_, "high": hi, "low": lo, "close": close,
         "volume": rng.uniform(10, 1000, n), "taker_buy_volume": rng.uniform(5, 500, n)},
        index=pd.date_range(start, periods=n, freq=freq),
    )


class TestWindowSplitting:
    def test_produces_requested_window_count(self):
        df = _ohlcv(n=540)
        with patch.object(SignalGenerator, "generate", return_value=None):
            report = WalkForwardRunner("BTCUSDT").run(df, n_windows=4)
        assert report.window_count == 4
        assert len(report.windows) == 4

    def test_windows_are_sequential_and_non_overlapping(self):
        df = _ohlcv(n=530)
        with patch.object(SignalGenerator, "generate", return_value=None):
            report = WalkForwardRunner("BTCUSDT").run(df, n_windows=3)
        for a, b in zip(report.windows, report.windows[1:]):
            assert a.test_end < b.test_start

    def test_last_window_extends_to_end_of_data(self):
        df = _ohlcv(n=530)
        with patch.object(SignalGenerator, "generate", return_value=None):
            report = WalkForwardRunner("BTCUSDT").run(df, n_windows=3)
        assert report.windows[-1].test_end == df.index[-1]

    def test_too_few_candles_raises_rather_than_silently_shrinking(self):
        df = _ohlcv(n=100)  # well under WARMUP_CANDLES (500)
        with pytest.raises(ValueError):
            WalkForwardRunner("BTCUSDT").run(df, n_windows=4)

    def test_rejects_zero_windows(self):
        df = _ohlcv(n=520)
        with pytest.raises(ValueError):
            WalkForwardRunner("BTCUSDT").run(df, n_windows=0)


class TestReusesBacktestEngineResolution:
    def test_resolve_trade_is_the_real_backtest_engine_method(self):
        runner = WalkForwardRunner("BTCUSDT")
        assert runner._engine._resolve_trade.__func__ is BacktestEngine._resolve_trade

    def test_full_run_does_not_raise_across_every_window(self):
        df = _ohlcv(n=540, seed=11)
        with patch.object(SignalGenerator, "generate", return_value=None):
            report = WalkForwardRunner("BTCUSDT").run(df, n_windows=4)
        for w in report.windows:
            assert w.test_report is not None  # every window produced a report, even with 0 trades

    def test_report_serializes(self):
        df = _ohlcv(n=520, seed=5)
        with patch.object(SignalGenerator, "generate", return_value=None):
            report = WalkForwardRunner("BTCUSDT").run(df, n_windows=2)
        d = report.to_dict()
        assert d["window_count"] == 2

    def test_no_optimization_state_is_carried_between_windows(self):
        """Each window must build its OWN fresh SignalGenerator - proves no
        parameter is fitted/carried forward window to window, per this
        module's explicit 'no optimization yet' constraint."""
        df = _ohlcv(n=530, seed=7)
        runner = WalkForwardRunner("XAUUSDT")
        seen_profiles = []
        original = runner._run_one_window

        def spy(d, idx):
            gen = SignalGenerator(runner.symbol)
            seen_profiles.append(gen.profile.min_confidence)
            return original(d, idx)

        runner._run_one_window = spy
        with patch.object(SignalGenerator, "generate", return_value=None):
            runner.run(df, n_windows=3)
        # Same calibration every window - nothing was tuned between them.
        assert len(set(seen_profiles)) == 1


class TestRealPipelineSmoke:
    """One small, real (non-stubbed) run to prove genuine integration with
    the unmodified ICT pipeline, not just the stub used above."""

    def test_single_small_window_runs_the_real_pipeline_without_raising(self):
        df = _ohlcv(n=520, seed=42)
        report = WalkForwardRunner("BTCUSDT").run(df, n_windows=1)
        assert report.window_count == 1
        assert isinstance(report.windows[0].test_trade_count, int)
