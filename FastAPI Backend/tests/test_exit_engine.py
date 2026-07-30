"""Regression suite for app.strategy.exit_engine."""
import pandas as pd
import pytest

from app.strategy.exit_engine import ExitEngine
from app.smc.liquidity_engine import LiquidityLevel, LiquidityKind, LiquidityScope, LiquidityStrength, LiquidityType
from app.smc.market_structure_engine import SwingPoint, SwingStrength, SwingType


def _ts():
    return pd.Timestamp("2026-01-05 00:00:00")


def _liq(price, liq_type, swept=False):
    return LiquidityLevel(price=price, type=liq_type, swing_points=[], kind=LiquidityKind.SINGLE_SWING,
                           scope=LiquidityScope.EXTERNAL, strength=LiquidityStrength.MODERATE, swept=swept)


def _swing(price, typ=SwingType.HH):
    return SwingPoint(timestamp=_ts(), price=price, type=typ, index=0, strength=SwingStrength.STRONG, displacement_ratio=1.0)


@pytest.fixture(scope="module")
def engine():
    return ExitEngine()


class TestPrimaryTargetSelection:
    def test_nearest_of_liquidity_and_structure_wins(self, engine):
        levels = [_liq(110.0, LiquidityType.BUYSIDE)]
        structure = [_swing(105.0)]
        plan = engine.build("LONG", entry_price=100.0, stop_loss=98.0, liquidity_levels=levels, structure_targets=structure)
        assert plan.primary_target.price == 105.0
        assert plan.primary_target.kind == "structure_target"

    def test_swept_liquidity_ignored(self, engine):
        levels = [_liq(102.0, LiquidityType.BUYSIDE, swept=True)]
        plan = engine.build("LONG", entry_price=100.0, stop_loss=98.0, liquidity_levels=levels)
        assert plan.primary_target is None

    def test_atr_fallback_used_when_no_real_target(self, engine):
        plan = engine.build("LONG", entry_price=100.0, stop_loss=98.0, atr_fallback=5.0)
        assert plan.primary_target.price == 105.0
        assert "ATR fallback" in plan.primary_target.reason

    def test_no_target_and_no_fallback_is_none(self, engine):
        plan = engine.build("LONG", entry_price=100.0, stop_loss=98.0)
        assert plan.primary_target is None

    def test_short_direction_targets_below_entry(self, engine):
        levels = [_liq(90.0, LiquidityType.SELLSIDE)]
        plan = engine.build("SHORT", entry_price=100.0, stop_loss=102.0, liquidity_levels=levels)
        assert plan.primary_target.price == 90.0


class TestPartialAndBreakeven:
    def test_partial_target_at_50pct(self, engine):
        levels = [_liq(110.0, LiquidityType.BUYSIDE)]
        plan = engine.build("LONG", entry_price=100.0, stop_loss=98.0, liquidity_levels=levels)
        assert len(plan.partial_targets) == 1
        assert plan.partial_targets[0].price == 105.0  # halfway from 100 to 110

    def test_custom_partial_fractions(self):
        engine = ExitEngine(partial_fractions=(0.3, 0.6))
        levels = [_liq(110.0, LiquidityType.BUYSIDE)]
        plan = engine.build("LONG", entry_price=100.0, stop_loss=98.0, liquidity_levels=levels)
        assert len(plan.partial_targets) == 2

    def test_breakeven_trigger_is_1r(self, engine):
        plan = engine.build("LONG", entry_price=100.0, stop_loss=98.0, atr_fallback=5.0)
        assert plan.breakeven_trigger.price == 102.0  # entry + risk(2.0)

    def test_zero_risk_no_breakeven(self, engine):
        plan = engine.build("LONG", entry_price=100.0, stop_loss=100.0, atr_fallback=5.0)
        assert plan.breakeven_trigger is None


class TestTrailingStructureFlag:
    def test_enabled_when_structure_targets_supplied(self, engine):
        plan = engine.build("LONG", entry_price=100.0, stop_loss=98.0, structure_targets=[_swing(105.0)])
        assert plan.trailing_structure_enabled is True

    def test_disabled_when_none_supplied(self, engine):
        plan = engine.build("LONG", entry_price=100.0, stop_loss=98.0, atr_fallback=5.0)
        assert plan.trailing_structure_enabled is False


class TestMinTargetDistance:
    def test_too_close_target_excluded(self, engine):
        levels = [_liq(100.5, LiquidityType.BUYSIDE)]
        plan = engine.build("LONG", entry_price=100.0, stop_loss=98.0, liquidity_levels=levels, min_target_distance=2.0)
        assert plan.primary_target is None
