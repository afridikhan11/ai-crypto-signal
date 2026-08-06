"""Tests for Phase-2 entry-model engines: CE, BPR, Unicorn, Rejection/Mitigation."""

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.smc.fvg import FairValueGap, FVGType
from app.smc.consequent_encroachment import ConsequentEncroachmentEngine
from app.smc.balanced_price_range import BalancedPriceRangeEngine
from app.smc.unicorn_model import UnicornModelEngine
from app.smc.order_block_engine import OBState
from app.smc.rejection_mitigation_blocks import (
    RejectionBlockEngine, MitigationBlockEngine, BlockDirection,
)


def _fvg(top, bottom, typ=FVGType.BULLISH, filled=False, ts="2026-01-05 00:00"):
    return FairValueGap(timestamp=pd.Timestamp(ts), top=top, bottom=bottom, type=typ, filled=filled)


# ------------------------------------------------------------------ CE
class TestConsequentEncroachment:
    def test_ce_is_midpoint(self):
        ce = ConsequentEncroachmentEngine().for_fvg(_fvg(100.4, 100.0))
        assert ce.ce_price == 100.2
        assert ce.direction == "bullish"

    def test_at_ce_within_tolerance(self):
        eng = ConsequentEncroachmentEngine()
        assert eng.for_fvg(_fvg(100.4, 100.0), current_price=100.21).at_ce is True
        assert eng.for_fvg(_fvg(100.4, 100.0), current_price=100.40).at_ce is False

    def test_price_at_any_ce_filters_direction(self):
        eng = ConsequentEncroachmentEngine()
        fvgs = [_fvg(100.4, 100.0, FVGType.BULLISH)]
        assert eng.price_at_any_ce(fvgs, 100.2, direction="bullish") is True
        assert eng.price_at_any_ce(fvgs, 100.2, direction="bearish") is False

    def test_filled_fvgs_ignored(self):
        eng = ConsequentEncroachmentEngine()
        assert eng.analyze([_fvg(100.4, 100.0, filled=True)]) == []


# ------------------------------------------------------------------ BPR
class TestBalancedPriceRange:
    def test_overlap_detected(self):
        bull = _fvg(100.5, 100.0, FVGType.BULLISH)
        bear = _fvg(100.8, 100.3, FVGType.BEARISH)
        bprs = BalancedPriceRangeEngine().analyze([bull, bear])
        assert len(bprs) == 1
        assert bprs[0].top == 100.5 and bprs[0].bottom == 100.3

    def test_no_overlap_returns_empty(self):
        bull = _fvg(100.2, 100.0, FVGType.BULLISH)
        bear = _fvg(100.8, 100.5, FVGType.BEARISH)
        assert BalancedPriceRangeEngine().analyze([bull, bear]) == []

    def test_reaction_bias_from_price(self):
        bull = _fvg(100.5, 100.0, FVGType.BULLISH)
        bear = _fvg(100.8, 100.3, FVGType.BEARISH)
        bprs = BalancedPriceRangeEngine().analyze([bull, bear], current_price=99.0)
        assert bprs[0].reaction_bias == "bullish"   # approaching from below


# ------------------------------------------------------------------ Unicorn
class TestUnicorn:
    def test_breaker_fvg_overlap(self):
        breaker = SimpleNamespace(high=100.5, low=100.0, state=OBState.BREAKER, timestamp=_fvg(0, 0).timestamp)
        fvg = _fvg(100.8, 100.3, FVGType.BULLISH)
        unis = UnicornModelEngine().analyze([breaker], [fvg])
        assert len(unis) == 1
        assert unis[0].direction == "bullish"
        assert unis[0].zone_top == 100.5 and unis[0].zone_bottom == 100.3
        assert unis[0].contains(100.4)

    def test_non_breaker_ignored(self):
        ob = SimpleNamespace(high=100.5, low=100.0, state=OBState.FRESH, timestamp=_fvg(0, 0).timestamp)
        fvg = _fvg(100.8, 100.3, FVGType.BULLISH)
        assert UnicornModelEngine().analyze([ob], [fvg]) == []


# ------------------------------------------------------------------ Rejection block
class TestRejectionBlock:
    def _df(self, rows):
        idx = pd.date_range("2026-01-05", periods=len(rows), freq="15min")
        return pd.DataFrame(rows, index=idx)

    def test_bullish_rejection_from_long_lower_wick(self):
        # A candle with a long lower wick: low 95, body 100-101, high 101.2.
        df = self._df([
            {"open": 100.5, "high": 101.0, "low": 100.0, "close": 100.6, "volume": 1},
            {"open": 100.0, "high": 101.2, "low": 95.0, "close": 101.0, "volume": 1},
        ])
        rb = RejectionBlockEngine().latest(df, direction="bullish")
        assert rb is not None
        assert rb.direction == BlockDirection.BULLISH
        assert rb.bottom == 95.0 and rb.top == 100.0  # the wick zone

    def test_no_rejection_on_full_body_candle(self):
        df = self._df([
            {"open": 100.0, "high": 101.0, "low": 100.0, "close": 101.0, "volume": 1},
        ])
        assert RejectionBlockEngine().detect(df) == []


# ------------------------------------------------------------------ Mitigation block
class TestMitigationBlock:
    def test_bullish_mitigation_from_higher_low(self):
        # earlier low at 90, then a higher-low origin (down candle) at ~98,
        # then a strong up displacement -> bullish mitigation block.
        rows = [
            {"open": 100.0, "high": 100.0, "low": 90.0, "close": 92.0, "volume": 1},  # earlier low 90
            {"open": 96.0, "high": 97.0, "low": 95.0, "close": 95.5, "volume": 1},
            {"open": 99.0, "high": 99.5, "low": 98.0, "close": 98.2, "volume": 1},    # higher-low origin (down candle)
            {"open": 98.3, "high": 104.0, "low": 98.2, "close": 103.5, "volume": 1},  # up displacement
        ]
        idx = pd.date_range("2026-01-05", periods=len(rows), freq="15min")
        df = pd.DataFrame(rows, index=idx)
        mb = MitigationBlockEngine().latest(df, direction="bullish")
        assert mb is not None
        assert mb.direction == BlockDirection.BULLISH
        assert mb.bottom == 98.0 and mb.top == 99.5
