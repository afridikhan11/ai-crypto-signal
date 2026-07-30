"""
Regression suite for app.smc.order_block_engine.

Ported from this project's own standalone verification pass (21/21 checks,
originally run via importlib against the real production file). Exercises:
bullish/bearish OB detection + displacement, honest degradation when no
structure_breaks/fvgs supplied, BOS/CHoCH relationship tagging, FVG
confluence, the Fresh->Mitigated->Breaker->Invalidated lifecycle state
machine, Breaker Block finders, and the opt-in displacement gate.
"""
import numpy as np
import pandas as pd
import pytest

from app.smc.market_structure_engine import MarketStructureEngine
from app.smc.order_block_engine import BlockType, OBState, OBStrength, OrderBlockEngine


def _seg(a, b, n):
    return list(np.linspace(a, b, n, endpoint=False))


def _make_trend_df(targets, seed=1, wick=0.05):
    """open[i] = close[i-1] (a coherent price path, not independent noise
    per candle), high/low wrap the body with a small wick so body/range
    stays high enough for `_is_momentum_candle`'s body-ratio >= 0.6 gate
    to fire on synthetic data."""
    rng = np.random.RandomState(seed)
    n = len(targets)
    closes = np.array(targets, dtype=float)
    opens = np.empty(n)
    opens[0] = closes[0] - (closes[1] - closes[0] if n > 1 else 0.1)
    opens[1:] = closes[:-1]
    highs = np.maximum(opens, closes) + np.abs(rng.normal(wick, wick * 0.3, size=n))
    lows = np.minimum(opens, closes) - np.abs(rng.normal(wick, wick * 0.3, size=n))
    idx = pd.date_range("2026-01-01", periods=n, freq="15min")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": 1000.0},
        index=idx,
    )


@pytest.fixture(scope="module")
def bullish_ob_fixture():
    closes = _seg(100, 95, 20) + [94, 93.5] + _seg(93.5, 130, 30)[1:]
    df = _make_trend_df(closes, seed=1)
    engine = OrderBlockEngine(df)
    return df, engine.find_bullish_order_block()


@pytest.fixture(scope="module")
def bearish_ob_fixture():
    closes = _seg(100, 105, 20) + [106, 106.5] + _seg(106.5, 70, 30)[1:]
    df = _make_trend_df(closes, seed=2)
    engine = OrderBlockEngine(df)
    return df, engine.find_bearish_order_block()


class TestBasicDetection:
    def test_bullish_ob_detected(self, bullish_ob_fixture):
        _, bull_ob = bullish_ob_fixture
        assert bull_ob is not None

    def test_bullish_ob_type_value(self, bullish_ob_fixture):
        _, bull_ob = bullish_ob_fixture
        assert bull_ob.type.value == "bullish_ob"

    def test_bullish_ob_displacement_ratio_nonnegative(self, bullish_ob_fixture):
        _, bull_ob = bullish_ob_fixture
        assert bull_ob.displacement_ratio >= 0

    def test_bullish_ob_honest_no_structure_breaks(self, bullish_ob_fixture):
        _, bull_ob = bullish_ob_fixture
        assert bull_ob.caused_bos is False

    def test_bullish_ob_honest_no_fvgs(self, bullish_ob_fixture):
        _, bull_ob = bullish_ob_fixture
        assert bull_ob.has_fvg_confluence is False

    def test_bullish_ob_starts_fresh(self, bullish_ob_fixture):
        _, bull_ob = bullish_ob_fixture
        assert bull_ob.state == OBState.FRESH

    def test_bullish_ob_mitigated_matches_state(self, bullish_ob_fixture):
        _, bull_ob = bullish_ob_fixture
        assert bull_ob.mitigated == (bull_ob.state != OBState.FRESH)

    def test_bearish_ob_detected(self, bearish_ob_fixture):
        _, bear_ob = bearish_ob_fixture
        assert bear_ob is not None

    def test_bearish_ob_type_value(self, bearish_ob_fixture):
        _, bear_ob = bearish_ob_fixture
        assert bear_ob.type.value == "bearish_ob"


class TestBOSCHoCHRelationship:
    def test_same_candle_with_or_without_structure_breaks(self):
        closes = (
            _seg(100, 106, 10)
            + _seg(106, 98, 10)[1:]
            + _seg(98, 104, 10)[1:]
            + _seg(104, 97, 10)[1:]
            + _seg(97, 130, 30)[1:]
        )
        df = _make_trend_df(closes, seed=3, wick=0.03)
        ms_snap = MarketStructureEngine(df, internal_pivot_window=3, external_pivot_window=8).analyze()
        ob_with = OrderBlockEngine(df, structure_breaks=ms_snap.external_breaks).find_bullish_order_block()
        ob_without = OrderBlockEngine(df).find_bullish_order_block()
        assert ob_with and ob_without
        assert ob_with.timestamp == ob_without.timestamp

    def test_relationship_sets_caused_bos_or_choch(self):
        closes = (
            _seg(100, 106, 10)
            + _seg(106, 98, 10)[1:]
            + _seg(98, 104, 10)[1:]
            + _seg(104, 97, 10)[1:]
            + _seg(97, 130, 30)[1:]
        )
        df = _make_trend_df(closes, seed=3, wick=0.03)
        ms_snap = MarketStructureEngine(df, internal_pivot_window=3, external_pivot_window=8).analyze()
        ob_with = OrderBlockEngine(df, structure_breaks=ms_snap.external_breaks).find_bullish_order_block()
        assert ob_with is not None
        assert ob_with.caused_bos or ob_with.caused_choch

    def test_strength_upgraded_or_equal_with_real_relationship(self):
        closes = (
            _seg(100, 106, 10)
            + _seg(106, 98, 10)[1:]
            + _seg(98, 104, 10)[1:]
            + _seg(104, 97, 10)[1:]
            + _seg(97, 130, 30)[1:]
        )
        df = _make_trend_df(closes, seed=3, wick=0.03)
        ms_snap = MarketStructureEngine(df, internal_pivot_window=3, external_pivot_window=8).analyze()
        ob_with = OrderBlockEngine(df, structure_breaks=ms_snap.external_breaks).find_bullish_order_block()
        ob_without = OrderBlockEngine(df).find_bullish_order_block()
        order = {OBStrength.WEAK: 0, OBStrength.MODERATE: 1, OBStrength.STRONG: 2}
        assert ob_with and ob_without
        assert order[ob_with.strength] >= order[ob_without.strength]


class TestFVGConfluence:
    def test_fvg_confluence_true_when_matching_fvg_supplied(self, bullish_ob_fixture):
        df, bull_ob = bullish_ob_fixture
        assert bull_ob is not None

        class _FakeFVG:
            def __init__(self, timestamp, type_value):
                self.timestamp = timestamp
                self.type = type_value

        confirm_pos = df.index.get_loc(bull_ob.timestamp) + 1
        confirm_ts = df.index[confirm_pos]
        fake_fvgs = [_FakeFVG(confirm_ts, "bullish")]
        ob_with_fvg = OrderBlockEngine(df, fvgs=fake_fvgs).find_bullish_order_block()
        assert ob_with_fvg is not None
        assert ob_with_fvg.has_fvg_confluence is True


class TestLifecycleStateMachine:
    """Directly unit-tests the private `_lifecycle` state machine with
    fully controlled close-price arrays, independent of the momentum-
    candle detector.

    2026-07-30: this previously injected a hand-rolled fake DataFrame that
    exposed only `.iloc[j]["close"]` and `.index[j]`, which coupled the
    test to `_lifecycle`'s internal access pattern - so the engine's
    numpy-array performance refactor broke the test without any behavior
    changing. It now builds a REAL one-candle-per-close DataFrame and a
    REAL `OrderBlockEngine`, which exercises the same state machine with
    the same controlled closes while being immune to how the engine reads
    its own data internally."""

    @staticmethod
    def _run_lifecycle(closes, low, high, direction="bullish"):
        # open/high/low all equal close: `_lifecycle` is a close-only state
        # machine (body-close confirmation, never wicks - see its docstring),
        # so the other columns are irrelevant to what is under test here.
        df = pd.DataFrame(
            {"open": closes, "high": closes, "low": closes, "close": closes,
             "volume": [1.0] * len(closes)},
            index=pd.date_range("2026-03-01", periods=len(closes), freq="15min"),
        )
        return OrderBlockEngine(df)._lifecycle(0, low=low, high=high, direction=direction)

    def test_stays_fresh_when_never_revisited(self):
        state, m_at, b_at, i_at = self._run_lifecycle(
            [100, 102, 104, 106, 108, 110, 112], low=99, high=101
        )
        assert state == OBState.FRESH
        assert m_at is None

    def test_mitigated_when_close_returns_inside_zone(self):
        state, m_at, b_at, i_at = self._run_lifecycle(
            [100, 102, 104, 100.5, 106, 108, 110], low=99, high=101
        )
        assert state == OBState.MITIGATED
        assert m_at is not None
        assert b_at is None

    def test_breaker_when_close_fully_below_bullish_ob_low(self):
        state, m_at, b_at, i_at = self._run_lifecycle(
            [100, 102, 104, 98.0, 97, 96, 95], low=99, high=101
        )
        assert state == OBState.BREAKER
        assert b_at is not None

    def test_invalidated_when_breaker_reclaimed(self):
        state, m_at, b_at, i_at = self._run_lifecycle(
            [100, 102, 104, 98.0, 97, 102.0, 103], low=99, high=101
        )
        assert state == OBState.INVALIDATED
        assert i_at is not None
        assert b_at is not None


class TestBreakerBlockFinders:
    def test_bullish_breaker_is_former_bearish_ob(self):
        closes = _seg(100, 106, 15) + [107, 107.5] + _seg(107.5, 95, 15)[1:] + _seg(95, 115, 20)[1:]
        df = _make_trend_df(closes, seed=5, wick=0.03)
        bullish_breaker = OrderBlockEngine(df).find_bullish_breaker_block()
        assert bullish_breaker is not None
        assert bullish_breaker.type == BlockType.BEARISH_OB
        assert bullish_breaker.state == OBState.BREAKER

    def test_bearish_breaker_is_former_bullish_ob(self):
        closes = _seg(100, 94, 15) + [93, 92.5] + _seg(92.5, 110, 15)[1:] + _seg(110, 85, 20)[1:]
        df = _make_trend_df(closes, seed=6, wick=0.03)
        bearish_breaker = OrderBlockEngine(df).find_bearish_breaker_block()
        assert bearish_breaker is not None
        assert bearish_breaker.type == BlockType.BULLISH_OB
        assert bearish_breaker.state == OBState.BREAKER


class TestDisplacementGate:
    def test_default_gate_reproduces_ungated_behavior(self, bullish_ob_fixture):
        df, _ = bullish_ob_fixture
        ob_default = OrderBlockEngine(df, min_displacement_atr=0.0).find_bullish_order_block()
        assert ob_default is not None

    def test_extreme_threshold_suppresses_weak_obs(self, bullish_ob_fixture):
        df, _ = bullish_ob_fixture
        ob_gated = OrderBlockEngine(df, min_displacement_atr=50.0).find_bullish_order_block()
        assert ob_gated is None
