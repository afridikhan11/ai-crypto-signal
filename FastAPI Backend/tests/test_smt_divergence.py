"""
Tests for SMT (Smart Money Technique) cross-asset divergence.

The comparison logic is the heart of the engine, so it is tested directly with
constructed SwingPoint lists (deterministic), plus guard/integration checks on
analyze().
"""
import numpy as np
import pandas as pd
import pytest

from app.smc.market_structure_engine import SwingPoint, SwingType, SwingStrength
from app.smc.smt_divergence import (
    SMTDivergenceEngine,
    default_reference_symbol,
)

T0 = pd.Timestamp("2026-01-01 00:00:00", tz="UTC")
T1 = pd.Timestamp("2026-01-01 04:00:00", tz="UTC")
WIDE = pd.Timedelta(hours=1)   # generous alignment tolerance for the unit tests


def _sh(ts, price):
    return SwingPoint(timestamp=ts, price=price, type=SwingType.HH, index=0,
                      strength=SwingStrength.STRONG, displacement_ratio=1.0)


def _sl(ts, price):
    return SwingPoint(timestamp=ts, price=price, type=SwingType.LL, index=0,
                      strength=SwingStrength.STRONG, displacement_ratio=1.0)


class TestDivergenceAtHighs:
    eng = SMTDivergenceEngine()

    def test_bearish_smt_when_reference_fails_the_higher_high(self):
        # primary: 100 -> 110 (higher high); reference: 100 -> 95 (lower high)
        p = [_sh(T0, 100), _sh(T1, 110)]
        r = [_sh(T0, 100), _sh(T1, 95)]
        d = self.eng._divergence_at_highs(p, r, WIDE, "BTCUSDT", "ETHUSDT")
        assert d is not None
        assert d.bias == "bearish" and d.kind == "bearish_smt_high"
        assert "ETHUSDT" in d.detail  # the one that failed to confirm

    def test_no_divergence_when_both_make_higher_highs(self):
        p = [_sh(T0, 100), _sh(T1, 110)]
        r = [_sh(T0, 100), _sh(T1, 108)]
        assert self.eng._divergence_at_highs(p, r, WIDE, "A", "B") is None

    def test_no_divergence_when_swings_not_time_aligned(self):
        p = [_sh(T0, 100), _sh(T1, 110)]
        far = T1 + pd.Timedelta(days=2)
        r = [_sh(T0, 100), _sh(far, 95)]
        tiny = pd.Timedelta(minutes=1)
        assert self.eng._divergence_at_highs(p, r, tiny, "A", "B") is None

    def test_needs_two_swings_each(self):
        assert self.eng._divergence_at_highs([_sh(T1, 110)], [_sh(T0, 100), _sh(T1, 95)],
                                             WIDE, "A", "B") is None


class TestDivergenceAtLows:
    eng = SMTDivergenceEngine()

    def test_bullish_smt_when_reference_fails_the_lower_low(self):
        # primary: 100 -> 90 (lower low); reference: 100 -> 105 (higher low)
        p = [_sl(T0, 100), _sl(T1, 90)]
        r = [_sl(T0, 100), _sl(T1, 105)]
        d = self.eng._divergence_at_lows(p, r, WIDE, "BTCUSDT", "ETHUSDT")
        assert d is not None
        assert d.bias == "bullish" and d.kind == "bullish_smt_low"
        assert "ETHUSDT" in d.detail

    def test_no_divergence_when_both_make_lower_lows(self):
        p = [_sl(T0, 100), _sl(T1, 90)]
        r = [_sl(T0, 100), _sl(T1, 92)]
        assert self.eng._divergence_at_lows(p, r, WIDE, "A", "B") is None


class TestAnalyzeGuards:
    eng = SMTDivergenceEngine()

    def _flat_df(self, n=20):
        idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
        close = np.full(n, 100.0)
        return pd.DataFrame({"open": close, "high": close + 1, "low": close - 1,
                             "close": close, "volume": np.full(n, 10.0)}, index=idx)

    def test_none_frames_return_none(self):
        assert self.eng.analyze(None, self._flat_df()) is None
        assert self.eng.analyze(self._flat_df(), None) is None

    def test_too_short_frames_return_none(self):
        short = self._flat_df(5)
        assert self.eng.analyze(short, short) is None

    def test_flat_market_has_no_divergence(self):
        df = self._flat_df(60)
        assert self.eng.analyze(df, df) is None

    def test_analyze_runs_on_real_frames_without_raising(self):
        # Two random-walk frames - must never raise; result is SMTDivergence or None.
        rng = np.random.default_rng(7)
        def walk(seed):
            r = np.random.default_rng(seed)
            idx = pd.date_range("2026-01-01", periods=200, freq="1h", tz="UTC")
            c = 100 + np.cumsum(r.normal(0, 1, 200))
            c = np.maximum(c, 1)
            return pd.DataFrame({"open": c, "high": c + r.uniform(.1, 1, 200),
                                 "low": c - r.uniform(.1, 1, 200), "close": c,
                                 "volume": r.uniform(10, 100, 200)}, index=idx)
        out = self.eng.analyze(walk(1), walk(2), "BTCUSDT", "ETHUSDT")
        assert out is None or out.bias in ("bullish", "bearish")


class TestDefaultReference:
    def test_canonical_crypto_pair(self):
        assert default_reference_symbol("BTCUSDT") == "ETHUSDT"
        assert default_reference_symbol("ETHUSDT") == "BTCUSDT"

    def test_unmapped_falls_back_to_btc(self):
        assert default_reference_symbol("XRPUSDT") == "BTCUSDT"
