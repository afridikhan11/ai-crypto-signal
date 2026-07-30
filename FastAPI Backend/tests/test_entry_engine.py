"""Regression suite for app.strategy.entry_engine."""
import pandas as pd
import pytest

from app.strategy.entry_engine import EntryEngine, EntryType
from app.smc.ote_engine import OTEZone
from app.smc.order_block_engine import BlockType, OBState, OBStrength, OrderBlock
from app.smc.fvg import FairValueGap, FVGType
from app.smc.supply_demand_engine import Zone, ZoneType, ZoneState, ZoneStrength


def _ts():
    return pd.Timestamp("2026-01-05 00:00:00")


@pytest.fixture(scope="module")
def engine():
    return EntryEngine()


class TestPriority:
    def test_ote_wins_over_everything(self, engine):
        ote = OTEZone(
            direction="bullish", leg_start_price=95.0, leg_end_price=105.0, displacement_ratio=1.5,
            zone_top=98.8, zone_bottom=97.1, has_ob_confluence=True, has_fvg_confluence=True,
            has_liquidity_confluence=True, premium_discount_aligned=True, confluence_count=3,
            retracement_quality="clean",
        )
        ob = OrderBlock(timestamp=_ts(), high=100.0, low=99.0, type=BlockType.BULLISH_OB, state=OBState.FRESH,
                         mitigated=False, displacement_ratio=1.0, caused_bos=False, caused_choch=False,
                         related_break_level=None, has_fvg_confluence=False, strength=OBStrength.STRONG,
                         mitigated_at=None, breaker_at=None, invalidated_at=None)
        plan = engine.build("LONG", current_price=100.0, ote_zone=ote, order_block=ob)
        assert plan.entry_type == EntryType.OTE

    def test_order_block_wins_over_fvg(self, engine):
        ob = OrderBlock(timestamp=_ts(), high=100.0, low=99.0, type=BlockType.BULLISH_OB, state=OBState.FRESH,
                         mitigated=False, displacement_ratio=1.0, caused_bos=False, caused_choch=False,
                         related_break_level=None, has_fvg_confluence=False, strength=OBStrength.STRONG,
                         mitigated_at=None, breaker_at=None, invalidated_at=None)
        fvg = FairValueGap(timestamp=_ts(), top=101.0, bottom=100.5, type=FVGType.BULLISH, filled=False)
        plan = engine.build("LONG", current_price=100.0, order_block=ob, fvg=fvg)
        assert plan.entry_type == EntryType.ORDER_BLOCK

    def test_fvg_wins_over_supply_demand(self, engine):
        fvg = FairValueGap(timestamp=_ts(), top=101.0, bottom=100.5, type=FVGType.BULLISH, filled=False)
        zone = Zone(top=102.0, bottom=101.0, type=ZoneType.DEMAND, origin_index=0, origin_timestamp=_ts(),
                    displacement_ratio=1.0, state=ZoneState.FRESH, strength=ZoneStrength.WEAK)
        plan = engine.build("LONG", current_price=100.0, fvg=fvg, supply_demand_zone=zone)
        assert plan.entry_type == EntryType.FVG


class TestFallbacks:
    def test_wrong_direction_zone_ignored(self, engine):
        bearish_ob = OrderBlock(timestamp=_ts(), high=100.0, low=99.0, type=BlockType.BEARISH_OB, state=OBState.FRESH,
                                 mitigated=False, displacement_ratio=1.0, caused_bos=False, caused_choch=False,
                                 related_break_level=None, has_fvg_confluence=False, strength=OBStrength.STRONG,
                                 mitigated_at=None, breaker_at=None, invalidated_at=None)
        plan = engine.build("LONG", current_price=100.0, order_block=bearish_ob)
        assert plan.entry_type == EntryType.MARKET

    def test_limit_used_when_no_zone_but_limit_price_given(self, engine):
        plan = engine.build("LONG", current_price=100.0, limit_price=98.5)
        assert plan.entry_type == EntryType.LIMIT
        assert plan.entry_price == 98.5

    def test_market_fallback_when_nothing_else(self, engine):
        plan = engine.build("SHORT", current_price=100.0)
        assert plan.entry_type == EntryType.MARKET
        assert plan.entry_price == 100.0

    def test_returns_none_when_no_price_and_no_zone(self, engine):
        assert engine.build("LONG", current_price=None) is None

    def test_filled_fvg_is_ignored(self, engine):
        filled_fvg = FairValueGap(timestamp=_ts(), top=101.0, bottom=100.5, type=FVGType.BULLISH, filled=True)
        plan = engine.build("LONG", current_price=100.0, fvg=filled_fvg)
        assert plan.entry_type == EntryType.MARKET
