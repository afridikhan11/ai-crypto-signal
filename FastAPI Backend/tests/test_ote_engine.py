"""
Regression suite for app.smc.ote_engine.

Covers the full valid bullish/bearish case, one dedicated rejection test
per individual missing condition (false-positive minimization, same
philosophy as Inducement), and determinism.
"""
import pandas as pd
import pytest

from app.smc.market_structure_engine import (
    MarketStructureSnapshot,
    StructureBreak,
    StructureScope,
    SwingPoint,
    SwingStrength,
    SwingType,
)
from app.smc.order_block_engine import BlockType, OBState, OBStrength, OrderBlock
from app.smc.fvg import FairValueGap, FVGType
from app.smc.liquidity_engine import LiquidityLevel, LiquidityKind, LiquidityScope, LiquidityStrength, LiquidityType
from app.smc.ote_engine import OTEEngine


def _ts(i=0):
    return pd.Timestamp("2026-01-05 00:00:00") + pd.Timedelta(minutes=15 * i)


def _swing(idx, price, typ):
    return SwingPoint(timestamp=_ts(idx), price=price, type=typ, index=idx, strength=SwingStrength.STRONG, displacement_ratio=1.0)


def _bullish_snapshot(displacement_ratio=1.2, with_high_after=True):
    swing_lows = [_swing(5, 95.0, SwingType.LL)]
    swing_highs = [_swing(10, 105.0, SwingType.HH)] if with_high_after else []
    brk = StructureBreak(
        timestamp=_ts(10), type="BOS", direction="bullish", broken_swing=swing_lows[0],
        level=100.0, scope=StructureScope.EXTERNAL, displacement_ratio=displacement_ratio, is_mss=False,
    )
    return MarketStructureSnapshot(
        swing_highs=swing_highs, swing_lows=swing_lows, internal_breaks=[], external_breaks=[brk],
        protected_high=None, protected_low=None, structure_alignment="aligned_bullish",
    )


def _full_confluence():
    ob = OrderBlock(
        timestamp=_ts(6), high=98.5, low=97.5, type=BlockType.BULLISH_OB, state=OBState.FRESH,
        mitigated=False, displacement_ratio=1.5, caused_bos=False, caused_choch=False,
        related_break_level=None, has_fvg_confluence=False, strength=OBStrength.STRONG,
        mitigated_at=None, breaker_at=None, invalidated_at=None,
    )
    fvg = FairValueGap(timestamp=_ts(7), top=97.5, bottom=97.0, type=FVGType.BULLISH, filled=False)
    liq = LiquidityLevel(
        price=95.0, type=LiquidityType.SELLSIDE, swing_points=[], kind=LiquidityKind.EQUAL_LOWS,
        scope=LiquidityScope.EXTERNAL, strength=LiquidityStrength.STRONG, swept=True,
    )
    return [ob], [fvg], [liq]


class TestValidBullishOTE:
    def test_full_confluence_produces_zone(self):
        snap = _bullish_snapshot()
        obs, fvgs, liqs = _full_confluence()
        zone = OTEEngine().detect(snap, order_blocks=obs, fvgs=fvgs, liquidity_levels=liqs, premium_discount_zone="discount")
        assert zone is not None
        assert zone.direction == "bullish"
        assert zone.confluence_count == 3
        assert zone.has_ob_confluence and zone.has_fvg_confluence and zone.has_liquidity_confluence
        assert zone.premium_discount_aligned is True
        assert zone.zone_bottom < zone.zone_top

    def test_contains_price_inside_and_outside(self):
        snap = _bullish_snapshot()
        obs, fvgs, liqs = _full_confluence()
        zone = OTEEngine().detect(snap, order_blocks=obs, fvgs=fvgs, liquidity_levels=liqs, premium_discount_zone="discount")
        mid = (zone.zone_top + zone.zone_bottom) / 2
        assert zone.contains(mid) is True
        assert zone.contains(zone.zone_top + 100) is False

    def test_clean_vs_shallow_retracement_quality(self):
        obs, fvgs, liqs = _full_confluence()
        shallow = OTEEngine().detect(_bullish_snapshot(displacement_ratio=1.2), order_blocks=obs, fvgs=fvgs, liquidity_levels=liqs, premium_discount_zone="discount")
        clean = OTEEngine().detect(_bullish_snapshot(displacement_ratio=2.0), order_blocks=obs, fvgs=fvgs, liquidity_levels=liqs, premium_discount_zone="discount")
        assert shallow.retracement_quality == "shallow"
        assert clean.retracement_quality == "clean"


class TestBearishMirror:
    def test_full_confluence_bearish(self):
        swing_highs = [_swing(5, 105.0, SwingType.HH)]
        swing_lows = [_swing(10, 95.0, SwingType.LL)]
        brk = StructureBreak(
            timestamp=_ts(10), type="CHoCH", direction="bearish", broken_swing=swing_highs[0],
            level=100.0, scope=StructureScope.EXTERNAL, displacement_ratio=1.5, is_mss=True,
        )
        snap = MarketStructureSnapshot(
            swing_highs=swing_highs, swing_lows=swing_lows, internal_breaks=[], external_breaks=[brk],
            protected_high=None, protected_low=None, structure_alignment="aligned_bearish",
        )
        ob = OrderBlock(
            timestamp=_ts(6), high=102.5, low=101.5, type=BlockType.BEARISH_OB, state=OBState.FRESH,
            mitigated=False, displacement_ratio=1.5, caused_bos=False, caused_choch=False,
            related_break_level=None, has_fvg_confluence=False, strength=OBStrength.STRONG,
            mitigated_at=None, breaker_at=None, invalidated_at=None,
        )
        fvg = FairValueGap(timestamp=_ts(7), top=103.0, bottom=102.5, type=FVGType.BEARISH, filled=False)
        liq = LiquidityLevel(
            price=105.0, type=LiquidityType.BUYSIDE, swing_points=[], kind=LiquidityKind.EQUAL_HIGHS,
            scope=LiquidityScope.EXTERNAL, strength=LiquidityStrength.STRONG, swept=True,
        )
        zone = OTEEngine().detect(snap, order_blocks=[ob], fvgs=[fvg], liquidity_levels=[liq], premium_discount_zone="premium")
        assert zone is not None
        assert zone.direction == "bearish"
        assert zone.confluence_count == 3


class TestRejections:
    def _valid_args(self):
        obs, fvgs, liqs = _full_confluence()
        return dict(order_blocks=obs, fvgs=fvgs, liquidity_levels=liqs, premium_discount_zone="discount")

    def test_no_structure_breaks(self):
        snap = _bullish_snapshot()
        empty_snap = MarketStructureSnapshot(
            swing_highs=snap.swing_highs, swing_lows=snap.swing_lows, internal_breaks=[], external_breaks=[],
            protected_high=None, protected_low=None, structure_alignment="insufficient_data",
        )
        assert OTEEngine().detect(empty_snap, **self._valid_args()) is None

    def test_weak_displacement(self):
        snap = _bullish_snapshot(displacement_ratio=0.2)
        assert OTEEngine().detect(snap, **self._valid_args()) is None

    def test_no_leg_found_missing_swing_lows(self):
        snap = _bullish_snapshot()
        no_lows = MarketStructureSnapshot(
            swing_highs=snap.swing_highs, swing_lows=[], internal_breaks=[], external_breaks=snap.external_breaks,
            protected_high=None, protected_low=None, structure_alignment="aligned_bullish",
        )
        assert OTEEngine().detect(no_lows, **self._valid_args()) is None

    def test_insufficient_confluence(self):
        snap = _bullish_snapshot()
        obs, fvgs, liqs = _full_confluence()
        # Only ONE confluence type supplied (below default min_confluence_count=2)
        zone = OTEEngine().detect(snap, order_blocks=obs, fvgs=None, liquidity_levels=None, premium_discount_zone="discount")
        assert zone is None

    def test_premium_discount_not_supplied(self):
        snap = _bullish_snapshot()
        obs, fvgs, liqs = _full_confluence()
        zone = OTEEngine().detect(snap, order_blocks=obs, fvgs=fvgs, liquidity_levels=liqs, premium_discount_zone=None)
        assert zone is None

    def test_premium_discount_wrong_side(self):
        snap = _bullish_snapshot()
        obs, fvgs, liqs = _full_confluence()
        zone = OTEEngine().detect(snap, order_blocks=obs, fvgs=fvgs, liquidity_levels=liqs, premium_discount_zone="premium")
        assert zone is None


class TestDeterminism:
    def test_same_inputs_same_output(self):
        snap = _bullish_snapshot()
        obs, fvgs, liqs = _full_confluence()
        engine = OTEEngine()
        z1 = engine.detect(snap, order_blocks=obs, fvgs=fvgs, liquidity_levels=liqs, premium_discount_zone="discount")
        z2 = engine.detect(snap, order_blocks=obs, fvgs=fvgs, liquidity_levels=liqs, premium_discount_zone="discount")
        assert z1 == z2
