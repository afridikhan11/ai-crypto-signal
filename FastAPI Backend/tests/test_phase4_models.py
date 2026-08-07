"""Tests for Phase-4 engines: Standard Deviation Projections, IPDA Ranges."""

import numpy as np
import pandas as pd

from app.smc.projections import StandardDeviationProjectionEngine
from app.smc.ipda_ranges import IPDAEngine


class TestProjections:
    def test_bullish_projections_extend_upward(self):
        projs = StandardDeviationProjectionEngine(multiples=(1.0, 2.0)).project(100.0, 110.0)
        # leg = 10; 1.0 -> 120, 2.0 -> 130
        assert [p.price for p in projs] == [120.0, 130.0]

    def test_bearish_projections_extend_downward(self):
        projs = StandardDeviationProjectionEngine(multiples=(1.0, 2.0)).project(110.0, 100.0)
        assert [p.price for p in projs] == [90.0, 80.0]

    def test_zero_leg_returns_empty(self):
        assert StandardDeviationProjectionEngine().project(100.0, 100.0) == []

    def test_next_target_ahead_of_price(self):
        eng = StandardDeviationProjectionEngine(multiples=(1.0, 2.0, 4.0))
        nxt = eng.next_target(100.0, 110.0, current_price=125.0)  # 120 passed, next 130
        assert nxt is not None and nxt.price == 130.0


class TestIPDA:
    def _df(self, n=70):
        idx = pd.date_range("2026-01-01", periods=n, freq="1D")
        t = np.arange(n)
        close = 100 + t * 0.5
        return pd.DataFrame(
            {"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1},
            index=idx,
        )

    def test_three_lookbacks_returned(self):
        ranges = IPDAEngine().analyze(self._df())
        assert [r.lookback for r in ranges] == [20, 40, 60]

    def test_premium_when_price_near_range_high(self):
        # rising series -> current price near the top of each lookback -> premium
        ranges = IPDAEngine().analyze(self._df())
        assert all(r.zone == "premium" for r in ranges)
        assert IPDAEngine().consensus_zone(self._df()) == "premium"

    def test_discount_when_price_near_range_low(self):
        idx = pd.date_range("2026-01-01", periods=70, freq="1D")
        t = np.arange(70)
        close = 200 - t * 0.5   # falling -> current near lows -> discount
        df = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1}, index=idx)
        assert IPDAEngine().consensus_zone(df) == "discount"

    def test_position_bounds(self):
        for r in IPDAEngine().analyze(self._df()):
            assert 0.0 <= r.position <= 1.0

    def test_empty(self):
        assert IPDAEngine().analyze(pd.DataFrame()) == []
