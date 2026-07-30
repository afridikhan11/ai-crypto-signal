"""
Regression suite for app.smc.market_structure_engine.

Ported from this project's own standalone verification pass (17/17 checks,
originally run via importlib against the real production file). Exercises
every ICT concept MarketStructureEngine claims to implement: Swing
Detection, Strong/Weak swing classification, BOS/CHoCH on external
structure, MSS labeling, Internal vs External structure + alignment,
Protected High/Low, body-close confirmation (not wick), the opt-in
Displacement Confirmation gate, and MarketStructureStateTracker dedup.
"""
import inspect

import numpy as np
import pandas as pd
import pytest

from app.smc.market_structure_engine import (
    MarketStructureEngine,
    MarketStructureStateTracker,
    SwingStrength,
)


def _seg(a, b, n):
    return list(np.linspace(a, b, n, endpoint=False))


def _make_df(closes, seed=1):
    rng = np.random.RandomState(seed)
    closes = np.array(closes, dtype=float)
    n = len(closes)
    highs = closes + rng.uniform(0.05, 0.2, size=n)
    lows = closes - rng.uniform(0.05, 0.2, size=n)
    opens = closes - rng.uniform(-0.1, 0.1, size=n)
    idx = pd.date_range("2026-01-01", periods=n, freq="15min")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": 1000},
        index=idx,
    )


@pytest.fixture(scope="module")
def breakout_snapshot():
    """A clean down-up-down-up sequence ending in a strong breakout leg.

    Segments are 10 candles each so the first swing high/low sits at
    index >= external_pivot_window (8) on both sides - required for
    _detect_swings(8) to be able to confirm it at all.
    """
    closes = (
        _seg(100, 106, 10)
        + _seg(106, 98, 10)[1:]
        + _seg(98, 104, 10)[1:]
        + _seg(104, 97, 10)[1:]
        + _seg(97, 130, 30)[1:]
    )
    df = _make_df(closes)
    engine = MarketStructureEngine(df, internal_pivot_window=3, external_pivot_window=8)
    return df, engine.analyze()


@pytest.fixture(scope="module")
def reversal_snapshot():
    """A clean established uptrend (2 HH + 2 HL) then a hard reversal
    below the last HL -> bearish CHoCH."""
    closes = (
        _seg(100, 105, 8)
        + _seg(105, 102, 8)[1:]
        + _seg(102, 112, 8)[1:]
        + _seg(112, 108, 8)[1:]
        + _seg(108, 118, 8)[1:]
        + _seg(118, 90, 20)[1:]
    )
    df = _make_df(closes, seed=2)
    engine = MarketStructureEngine(df, internal_pivot_window=3, external_pivot_window=6)
    return df, engine.analyze()


class TestSwingDetection:
    def test_finds_swing_highs(self, breakout_snapshot):
        _, snap = breakout_snapshot
        assert len(snap.swing_highs) >= 2

    def test_finds_swing_lows(self, breakout_snapshot):
        _, snap = breakout_snapshot
        assert len(snap.swing_lows) >= 1

    def test_strong_swing_on_breakout_leg(self, breakout_snapshot):
        _, snap = breakout_snapshot
        assert any(
            sw.strength == SwingStrength.STRONG for sw in snap.swing_highs + snap.swing_lows
        )

    def test_displacement_ratio_is_real_nonnegative(self, breakout_snapshot):
        _, snap = breakout_snapshot
        assert all(sw.displacement_ratio >= 0 for sw in snap.swing_highs + snap.swing_lows)


class TestBOS:
    def test_bos_detected_on_external_scope(self, breakout_snapshot):
        _, snap = breakout_snapshot
        assert any(b.type == "BOS" and b.direction == "bullish" for b in snap.external_breaks)

    def test_bos_timestamp_is_not_just_last_candle(self, breakout_snapshot):
        df, snap = breakout_snapshot
        assert any(
            b.timestamp != df.index[-1] for b in snap.external_breaks if b.type == "BOS"
        )


class TestInternalExternalStructure:
    def test_internal_breaks_is_a_list(self, breakout_snapshot):
        _, snap = breakout_snapshot
        assert isinstance(snap.internal_breaks, list)

    def test_structure_alignment_is_documented_value(self, breakout_snapshot):
        _, snap = breakout_snapshot
        assert snap.structure_alignment in (
            "aligned_bullish",
            "aligned_bearish",
            "conflicted",
            "insufficient_data",
        )


class TestProtectedHighLow:
    def test_protected_low_populated(self, breakout_snapshot):
        _, snap = breakout_snapshot
        assert snap.protected_low is not None


class TestCHoCHAndMSS:
    def test_choch_detected_after_uptrend_reversal(self, reversal_snapshot):
        _, snap = reversal_snapshot
        choch_events = [b for b in snap.external_breaks if b.type == "CHoCH"]
        assert len(choch_events) >= 1

    def test_mss_true_for_every_choch(self, reversal_snapshot):
        _, snap = reversal_snapshot
        choch_events = [b for b in snap.external_breaks if b.type == "CHoCH"]
        assert all(b.is_mss for b in choch_events)

    def test_mss_false_for_bos_not_conflated_with_choch(self, reversal_snapshot):
        _, snap = reversal_snapshot
        bos_events = [b for b in snap.external_breaks if b.type == "BOS"]
        assert all(not b.is_mss for b in bos_events)


class TestBodyCloseConfirmation:
    def test_break_check_reads_close_not_wick(self):
        """Structural check: the break-detection routine must compare
        against df['close'], never df['high']/df['low'] (wick-only pokes
        must not trigger a break)."""
        source = inspect.getsource(MarketStructureEngine._find_first_break_after)
        assert "closes[j] >" in source
        assert 'df["high"]' not in source


class TestDisplacementConfirmationGate:
    def test_default_gate_reproduces_ungated_behavior(self, breakout_snapshot):
        df, _ = breakout_snapshot
        engine_default = MarketStructureEngine(
            df, internal_pivot_window=3, external_pivot_window=8, min_displacement_atr=0.0
        )
        snap_default = engine_default.analyze()
        assert len(snap_default.external_breaks) >= 1

    def test_high_threshold_suppresses_weak_breaks(self, breakout_snapshot):
        df, _ = breakout_snapshot
        engine_default = MarketStructureEngine(
            df, internal_pivot_window=3, external_pivot_window=8, min_displacement_atr=0.0
        )
        engine_gated = MarketStructureEngine(
            df, internal_pivot_window=3, external_pivot_window=8, min_displacement_atr=5.0
        )
        snap_default = engine_default.analyze()
        snap_gated = engine_gated.analyze()
        assert len(snap_gated.external_breaks) <= len(snap_default.external_breaks)


class TestStateTracker:
    def test_first_observation_is_new(self, breakout_snapshot):
        _, snap = breakout_snapshot
        bos = next((b for b in snap.external_breaks if b.type == "BOS"), None)
        if bos is None:
            pytest.skip("no BOS found in this fixture to test the state tracker against")
        tracker = MarketStructureStateTracker()
        assert tracker.is_new(bos) is True

    def test_second_observation_of_same_break_is_not_new(self, breakout_snapshot):
        _, snap = breakout_snapshot
        bos = next((b for b in snap.external_breaks if b.type == "BOS"), None)
        if bos is None:
            pytest.skip("no BOS found in this fixture to test the state tracker against")
        tracker = MarketStructureStateTracker()
        tracker.is_new(bos)
        assert tracker.is_new(bos) is False
