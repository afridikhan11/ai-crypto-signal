"""Tests for Phase-3 engines: Turtle Soup, Power of 3, Opening Gaps, Liquidity Void."""

import numpy as np
import pandas as pd

from app.smc.turtle_soup import TurtleSoupEngine, SoupDirection
from app.smc.power_of_three import PowerOfThreeEngine, AMDPhase
from app.smc.opening_gaps import OpeningGapEngine
from app.smc.liquidity_void import LiquidityVoidEngine, VoidDirection


def _df(rows, start="2026-01-05 00:00", freq="15min"):
    idx = pd.date_range(start=start, periods=len(rows), freq=freq)
    return pd.DataFrame(rows, index=idx)


# ---------------------------------------------------------- Turtle Soup
class TestTurtleSoup:
    def _base(self, n=22):
        return [{"open": 102, "high": 103, "low": 100, "close": 102, "volume": 1} for _ in range(n)]

    def test_bullish_turtle_soup(self):
        rows = self._base(22) + [
            {"open": 101, "high": 101, "low": 98, "close": 99, "volume": 1},   # raid the low
            {"open": 99, "high": 101, "low": 99, "close": 100.5, "volume": 1},
            {"open": 100.5, "high": 103, "low": 100, "close": 102, "volume": 1},  # back above 100
        ]
        soup = TurtleSoupEngine(lookback=20, confirm=3).detect(_df(rows))
        assert soup is not None
        assert soup.direction == SoupDirection.BULLISH
        assert soup.reference_level == 100.0

    def test_no_soup_without_sweep(self):
        rows = self._base(22) + [
            {"open": 102, "high": 103, "low": 101, "close": 102, "volume": 1} for _ in range(3)
        ]
        assert TurtleSoupEngine(lookback=20, confirm=3).detect(_df(rows)) is None


# ---------------------------------------------------------- Power of 3
class TestPowerOfThree:
    def test_bullish_distribution_when_low_taken_first(self):
        rows = [
            {"open": 100, "high": 100.5, "low": 95, "close": 97, "volume": 1},   # low first
            {"open": 97, "high": 99, "low": 96, "close": 98, "volume": 1},
            {"open": 98, "high": 105, "low": 98, "close": 104, "volume": 1},     # high later
            {"open": 104, "high": 104, "low": 101, "close": 103, "volume": 1},   # close > open
        ]
        p3 = PowerOfThreeEngine().analyze(_df(rows, freq="1h"))
        assert p3 is not None
        assert p3.manipulation_direction == "down"
        assert p3.distribution_bias == "bullish"
        assert p3.phase == AMDPhase.DISTRIBUTION

    def test_empty(self):
        assert PowerOfThreeEngine().analyze(pd.DataFrame()) is None


# ---------------------------------------------------------- Opening Gaps
class TestOpeningGaps:
    def test_ndog_detected(self):
        rows = [
            {"open": 100, "high": 100.2, "low": 99.8, "close": 100, "volume": 1},  # 01-05 22:00
            {"open": 100, "high": 100.2, "low": 99.9, "close": 100, "volume": 1},  # 01-05 23:00
            {"open": 102, "high": 103, "low": 101.5, "close": 102.5, "volume": 1}, # 01-06 00:00 (gap up)
            {"open": 102.5, "high": 103, "low": 101.6, "close": 102.8, "volume": 1},
        ]
        idx = [
            pd.Timestamp("2026-01-05 22:00"), pd.Timestamp("2026-01-05 23:00"),
            pd.Timestamp("2026-01-06 00:00"), pd.Timestamp("2026-01-06 01:00"),
        ]
        df = pd.DataFrame(rows, index=pd.DatetimeIndex(idx))
        gap = OpeningGapEngine().day_gap(df)
        assert gap is not None
        assert gap.kind == "NDOG"
        assert gap.bottom == 100.0 and gap.top == 102.0
        assert gap.filled is False   # price stayed above 100

    def test_empty(self):
        assert OpeningGapEngine().day_gap(pd.DataFrame()) is None


# ---------------------------------------------------------- Liquidity Void
class TestLiquidityVoid:
    def test_large_displacement_is_a_void(self):
        rng = np.random.RandomState(0)
        rows = []
        price = 100.0
        for _ in range(20):  # calm baseline, range ~1
            rows.append({"open": price, "high": price + 0.5, "low": price - 0.5, "close": price + 0.1, "volume": 1})
            price += 0.1
        # one violent bullish displacement, range ~6 >> ATR(~1)
        rows.append({"open": price, "high": price + 6.0, "low": price - 0.2, "close": price + 5.7, "volume": 1})
        price += 5.7
        rows.append({"open": price, "high": price + 0.5, "low": price - 0.5, "close": price, "volume": 1})
        voids = LiquidityVoidEngine(void_atr_mult=2.5).detect(_df(rows))
        assert len(voids) >= 1
        assert voids[0].direction == VoidDirection.BULLISH
        assert voids[0].atr_multiple >= 2.5

    def test_calm_market_no_void(self):
        rows = [{"open": 100, "high": 100.5, "low": 99.5, "close": 100.1, "volume": 1} for _ in range(30)]
        assert LiquidityVoidEngine(void_atr_mult=2.5).detect(_df(rows)) == []
