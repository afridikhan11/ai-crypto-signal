"""
Per-strategy backtest metrics (Sharpe, R-distribution, <100-trade warning) and
the BaseStrategy backtest adapter (fee/slippage/funding-aware resolution,
partial-take modelling, walk-forward split).
"""
import asyncio

import pandas as pd
import pytest

from app.backtest.strategy_backtest import (
    BacktestCosts,
    backtest_strategy,
    walk_forward_backtest,
)
from app.backtest.strategy_metrics import (
    build_strategy_report,
    r_multiple_distribution,
    sharpe_ratio,
)
from app.models.signal import Direction
from app.strategy.base_strategy import BaseStrategy, StrategySignal


def _run(coro):
    return asyncio.run(coro)


def _trades(rr_outcomes):
    # Include the breakdown fields build_performance_report reads (direction /
    # confidence / symbol); real adapter trades carry these too.
    return [
        {"realized_rr": rr, "outcome": oc, "direction": "SHORT", "confidence": 70, "symbol": "BTCUSDT"}
        for rr, oc in rr_outcomes
    ]


# ======================================================================
# Metrics
# ======================================================================
class TestMetrics:
    def test_sharpe_basic(self):
        assert sharpe_ratio([1.0, 1.0, 1.0]) is None       # zero dispersion
        assert sharpe_ratio([2.0]) is None                  # < 2 samples
        s = sharpe_ratio([-1.0, 2.0, -1.0, 2.0])
        assert s is not None and s > 0

    def test_r_distribution_buckets(self):
        trades = _trades([(-1.5, "STOPPED"), (-1.0, "STOPPED"), (0.5, "TP_HIT"), (2.5, "TP_HIT"), (4.0, "TP_HIT")])
        dist = r_multiple_distribution(trades)
        assert dist["<= -1R"] == 2      # -1.5 and -1.0 both <= -1
        assert dist["0R..1R"] == 1
        assert dist["2R..3R"] == 1
        assert dist["3R+"] == 1

    def test_report_flags_small_sample(self):
        rep = build_strategy_report("ict_levels", _trades([(2.0, "TP_HIT"), (-1.0, "STOPPED")]))
        assert rep.total_trades == 2
        assert rep.insufficient_sample is True
        assert rep.sample_warning and "NOT statistically meaningful" in rep.sample_warning
        assert rep.win_rate_pct == 50.0

    def test_report_no_warning_at_scale(self):
        big = _trades([(2.0, "TP_HIT")] * 60 + [(-1.0, "STOPPED")] * 45)  # 105 resolved
        rep = build_strategy_report("ict_levels", big)
        assert rep.total_trades == 105
        assert rep.insufficient_sample is False
        assert rep.sample_warning is None
        assert rep.sharpe_ratio is not None


# ======================================================================
# Backtest adapter
# ======================================================================
class _OneShot(BaseStrategy):
    strategy_id = "stub"
    display_name = "Stub"

    def __init__(self, signal):
        super().__init__(settings=None)
        self._signal = signal
        self._fired = False

    @property
    def enabled(self):
        return True

    async def evaluate(self, market_data):
        if self._fired:
            return None
        self._fired = True
        return self._signal


def _df(lows_highs):
    """Build an OHLCV frame from (low, high) pairs; close=mid, open=mid."""
    rows = []
    for low, high in lows_highs:
        mid = (low + high) / 2
        rows.append({"open": mid, "high": high, "low": low, "close": mid, "volume": 1.0})
    return pd.DataFrame(rows)


def _short(entry=100.0, stop=102.0, tp=96.0, targets=None):
    return StrategySignal(
        strategy_id="stub", symbol="BTCUSDT", direction=Direction.SHORT,
        entry_price=entry, stop_loss=stop, take_profit=tp, risk_reward=2.0, confidence=70,
        targets=targets or [],
    )


class TestBacktestAdapter:
    def test_target_hit_is_a_win_near_2r_minus_costs(self):
        # entry at bar 3 (warmup=3); bar 4 trades down to the 96 target.
        bars = [(99, 101)] * 4 + [(95, 100)] + [(99, 101)] * 3
        trades = _run(backtest_strategy(_OneShot(_short()), _df(bars), warmup=3, cooldown_bars=3))
        assert len(trades) == 1
        assert trades[0]["outcome"] == "TP_HIT"
        assert 1.8 < trades[0]["realized_rr"] < 2.0    # ~2R, shaved by fees/slippage

    def test_stop_hit_is_a_loss_near_minus_1r(self):
        bars = [(99, 101)] * 4 + [(99, 103)] + [(99, 101)] * 3   # bar 4 spikes to 103 >= stop 102
        trades = _run(backtest_strategy(_OneShot(_short()), _df(bars), warmup=3, cooldown_bars=3))
        assert len(trades) == 1
        assert trades[0]["outcome"] == "STOPPED"
        assert -1.15 < trades[0]["realized_rr"] < -1.0  # ~ -1R plus costs

    def test_no_touch_produces_no_trade(self):
        bars = [(99, 101)] * 9   # never reaches stop or target
        trades = _run(backtest_strategy(_OneShot(_short()), _df(bars), warmup=3, cooldown_bars=3))
        assert trades == []

    def test_costs_reduce_the_win(self):
        bars = [(99, 101)] * 4 + [(95, 100)] + [(99, 101)] * 3
        cheap = _run(backtest_strategy(_OneShot(_short()), _df(bars), warmup=3, costs=BacktestCosts(0, 0, 0)))
        pricey = _run(backtest_strategy(_OneShot(_short()), _df(bars), warmup=3, costs=BacktestCosts(0.1, 0.1, 0.05)))
        assert cheap[0]["realized_rr"] > pricey[0]["realized_rr"]
        assert cheap[0]["realized_rr"] == pytest.approx(2.0, abs=1e-6)  # frictionless = exactly 2R

    def test_partial_take_then_final(self):
        # partial at 98, final at 96; bar4 dips to 97 (hits 98 partial), bar5 to 95 (hits 96 final).
        bars = [(99, 101)] * 4 + [(97, 100)] + [(95, 99)] + [(99, 101)] * 2
        sig = _short(tp=96.0, targets=[98.0, 96.0])
        trades = _run(backtest_strategy(_OneShot(sig), _df(bars), warmup=3, costs=BacktestCosts(0, 0, 0)))
        assert len(trades) == 1 and trades[0]["outcome"] == "TP_HIT"
        # 0.5*((100-98)/2) + 0.5*((100-96)/2) = 0.5 + 1.0 = 1.5R
        assert trades[0]["realized_rr"] == pytest.approx(1.5, abs=1e-6)

    def test_walk_forward_splits_into_windows(self):
        bars = [(99, 101)] * 40
        windows = _run(walk_forward_backtest(_OneShot(_short()), _df(bars), windows=4, warmup=2))
        assert len(windows) == 4       # four independent out-of-sample slices
