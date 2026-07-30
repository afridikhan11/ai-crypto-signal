"""
Regression suite for app.smc.htf_structure_engine.
"""
import numpy as np
import pandas as pd
import pytest

from app.smc.htf_structure_engine import HTFStructureEngine, SUPPORTED_TIMEFRAMES


def _seg(a, b, n):
    return list(np.linspace(a, b, n, endpoint=False))


def _make_df(closes, seed=1):
    rng = np.random.RandomState(seed)
    closes = np.array(closes, dtype=float)
    n = len(closes)
    highs = closes + rng.uniform(0.05, 0.2, size=n)
    lows = closes - rng.uniform(0.05, 0.2, size=n)
    opens = closes - rng.uniform(-0.1, 0.1, size=n)
    idx = pd.date_range("2026-01-01", periods=n, freq="1D")
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes, "volume": 1000}, index=idx)


@pytest.fixture(scope="module")
def trending_df():
    closes = (
        _seg(100, 106, 10) + _seg(106, 98, 10)[1:] + _seg(98, 104, 10)[1:]
        + _seg(104, 97, 10)[1:] + _seg(97, 130, 30)[1:]
    )
    return _make_df(closes)


class TestBuildSnapshot:
    def test_empty_dict_produces_empty_snapshot(self):
        snap = HTFStructureEngine().build_snapshot({})
        assert snap.is_empty()
        assert snap.get("1d") is None

    def test_none_produces_empty_snapshot(self):
        snap = HTFStructureEngine().build_snapshot(None)
        assert snap.is_empty()

    def test_supplied_timeframe_is_populated(self, trending_df):
        snap = HTFStructureEngine().build_snapshot({"1d": trending_df})
        ts = snap.get("1d")
        assert ts is not None
        assert ts.timeframe == "1d"
        assert ts.structure_alignment in ("aligned_bullish", "aligned_bearish", "conflicted", "insufficient_data")

    def test_unsupplied_timeframe_is_none(self, trending_df):
        snap = HTFStructureEngine().build_snapshot({"1d": trending_df})
        assert snap.get("4h") is None
        assert snap.get("1w") is None

    def test_too_short_dataframe_is_omitted_not_guessed(self):
        short_df = _make_df(_seg(100, 105, 5))
        snap = HTFStructureEngine().build_snapshot({"1d": short_df})
        assert snap.get("1d") is None

    def test_multiple_timeframes_all_populated(self, trending_df):
        snap = HTFStructureEngine().build_snapshot({"1w": trending_df, "1d": trending_df, "4h": trending_df})
        assert snap.get("1w") is not None
        assert snap.get("1d") is not None
        assert snap.get("4h") is not None
        assert snap.available_timeframes["1w"] is True
        assert snap.available_timeframes["1h"] is False

    def test_latest_break_fields_populated_when_break_exists(self, trending_df):
        snap = HTFStructureEngine().build_snapshot({"1d": trending_df})
        ts = snap.get("1d")
        if ts.snapshot.external_breaks:
            assert ts.latest_break_direction in ("bullish", "bearish")
            assert ts.latest_break_type in ("BOS", "CHoCH")

    def test_supported_timeframes_constant(self):
        assert SUPPORTED_TIMEFRAMES == ("1w", "1d", "4h", "1h")

    def test_determinism(self, trending_df):
        engine = HTFStructureEngine()
        snap1 = engine.build_snapshot({"1d": trending_df})
        snap2 = engine.build_snapshot({"1d": trending_df})
        assert snap1.get("1d").structure_alignment == snap2.get("1d").structure_alignment
        assert snap1.get("1d").protected_low == snap2.get("1d").protected_low
