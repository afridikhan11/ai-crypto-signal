"""
Regression suite for app.smc.institutional_bias_engine.
"""
import pytest

from app.smc.htf_structure_engine import HTFSnapshot, TimeframeStructure
from app.smc.institutional_bias_engine import InstitutionalBiasEngine
from app.smc.market_structure_engine import MarketStructureSnapshot
from app.smc.liquidity_engine import LiquidityLevel, LiquidityKind, LiquidityScope, LiquidityStrength, LiquidityType


def _bare_snapshot(structure_alignment, protected_high=None, protected_low=None):
    return MarketStructureSnapshot(
        swing_highs=[], swing_lows=[], internal_breaks=[], external_breaks=[],
        protected_high=protected_high, protected_low=protected_low,
        structure_alignment=structure_alignment,
    )


def _ts(timeframe, structure_alignment, protected_high=None, protected_low=None):
    return TimeframeStructure(
        timeframe=timeframe, structure_alignment=structure_alignment,
        protected_high=protected_high, protected_low=protected_low,
        latest_break_direction=None, latest_break_type=None,
        snapshot=_bare_snapshot(structure_alignment, protected_high, protected_low),
    )


@pytest.fixture(scope="module")
def engine():
    return InstitutionalBiasEngine()


class TestNoData:
    def test_none_snapshot_is_neutral(self, engine):
        bias = engine.assess(None)
        assert bias.direction == "neutral"
        assert bias.confluence_count == 0
        assert "No higher-timeframe" in bias.supporting_evidence[0]

    def test_empty_snapshot_is_neutral(self, engine):
        bias = engine.assess(HTFSnapshot(timeframes={}))
        assert bias.direction == "neutral"


class TestDirectionalAggregation:
    def test_all_bullish_is_bullish(self, engine):
        snap = HTFSnapshot(timeframes={
            "1w": _ts("1w", "aligned_bullish"),
            "1d": _ts("1d", "aligned_bullish"),
            "4h": _ts("4h", "aligned_bullish"),
        })
        bias = engine.assess(snap)
        assert bias.direction == "bullish"
        assert len(bias.supporting_evidence) == 3

    def test_all_bearish_is_bearish(self, engine):
        snap = HTFSnapshot(timeframes={
            "1w": _ts("1w", "aligned_bearish"),
            "1d": _ts("1d", "aligned_bearish"),
            "4h": _ts("4h", "aligned_bearish"),
        })
        bias = engine.assess(snap)
        assert bias.direction == "bearish"

    def test_close_split_is_conflicted(self, engine):
        # 1w bearish(1.5) vs 1d+4h bullish(1.2+1.0=2.2) -> diff=0.7, total=3.7,
        # band=0.3*3.7=1.11 -> diff < band -> conflicted.
        snap = HTFSnapshot(timeframes={
            "1w": _ts("1w", "aligned_bearish"),
            "1d": _ts("1d", "aligned_bullish"),
            "4h": _ts("4h", "aligned_bullish"),
        })
        bias = engine.assess(snap)
        assert bias.direction == "conflicted"

    def test_clear_majority_wins_outside_conflict_band(self, engine):
        # 1w bearish(1.5) + 1d bearish(1.2) vs 4h bullish(1.0) -> bull=1.0,
        # bear=2.7, total=3.7, diff=1.7, band=0.3*3.7=1.11 -> diff > band ->
        # a clean bearish majority, not conflicted.
        snap = HTFSnapshot(timeframes={
            "1w": _ts("1w", "aligned_bearish"),
            "1d": _ts("1d", "aligned_bearish"),
            "4h": _ts("4h", "aligned_bullish"),
        })
        bias = engine.assess(snap)
        assert bias.direction == "bearish"

    def test_insufficient_data_timeframe_abstains_cleanly(self, engine):
        snap = HTFSnapshot(timeframes={"1d": _ts("1d", "insufficient_data")})
        bias = engine.assess(snap)
        assert bias.direction == "neutral"
        assert bias.daily_alignment == "insufficient_data"


class TestPremiumDiscountTilt:
    def test_discount_alone_tilts_bullish(self, engine):
        snap = HTFSnapshot(timeframes={"1d": _ts("1d", "insufficient_data")})
        bias = engine.assess(snap, premium_discount_zone="discount")
        assert bias.direction == "bullish"
        assert any("discount" in e.lower() for e in bias.supporting_evidence)

    def test_premium_alone_tilts_bearish(self, engine):
        snap = HTFSnapshot(timeframes={"1d": _ts("1d", "insufficient_data")})
        bias = engine.assess(snap, premium_discount_zone="premium")
        assert bias.direction == "bearish"


class TestConfluenceCount:
    def test_major_liquidity_order_blocks_zones_counted(self, engine):
        snap = HTFSnapshot(timeframes={"1d": _ts("1d", "aligned_bullish")})
        swept_level = LiquidityLevel(
            price=100.0, type=LiquidityType.BUYSIDE, swing_points=[], kind=LiquidityKind.EQUAL_HIGHS,
            scope=LiquidityScope.EXTERNAL, strength=LiquidityStrength.STRONG, swept=True,
        )
        unswept_level = LiquidityLevel(
            price=90.0, type=LiquidityType.SELLSIDE, swing_points=[], kind=LiquidityKind.SINGLE_SWING,
            scope=LiquidityScope.EXTERNAL, strength=LiquidityStrength.WEAK, swept=False,
        )
        bias = engine.assess(
            snap, major_liquidity=[swept_level, unswept_level],
            major_order_blocks=["ob1"], major_zones=["z1", "z2"],
        )
        assert bias.confluence_count == 1 + 1 + 2  # 1 swept liquidity + 1 OB + 2 zones


class TestPassthroughFields:
    def test_alignment_fields_and_protected_levels_from_daily(self, engine):
        snap = HTFSnapshot(timeframes={
            "1w": _ts("1w", "aligned_bullish"),
            "1d": _ts("1d", "aligned_bullish", protected_high=110.0, protected_low=95.0),
        })
        bias = engine.assess(snap)
        assert bias.weekly_alignment == "aligned_bullish"
        assert bias.daily_alignment == "aligned_bullish"
        assert bias.h4_alignment is None
        assert bias.protected_high == 110.0
        assert bias.protected_low == 95.0

    def test_no_daily_means_no_protected_levels(self, engine):
        snap = HTFSnapshot(timeframes={"1w": _ts("1w", "aligned_bullish")})
        bias = engine.assess(snap)
        assert bias.protected_high is None
        assert bias.protected_low is None


class TestDeterminism:
    def test_same_inputs_same_output(self, engine):
        snap = HTFSnapshot(timeframes={"1w": _ts("1w", "aligned_bullish"), "1d": _ts("1d", "aligned_bearish")})
        b1 = engine.assess(snap, premium_discount_zone="discount")
        b2 = engine.assess(snap, premium_discount_zone="discount")
        assert b1.direction == b2.direction
        assert b1.confluence_count == b2.confluence_count
