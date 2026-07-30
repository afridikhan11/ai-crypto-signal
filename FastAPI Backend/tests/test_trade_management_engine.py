"""Regression suite for app.strategy.trade_management_engine."""
import pandas as pd
import pytest

from app.strategy.trade_management_engine import TradeManagementEngine, ManagementActionType
from app.strategy.exit_engine import ExitLevel, ExitPlan
from app.smc.market_structure_engine import StructureBreak, StructureScope, SwingPoint, SwingStrength, SwingType


def _ts():
    return pd.Timestamp("2026-01-05 00:00:00")


def _swing(price, typ=SwingType.HH):
    return SwingPoint(timestamp=_ts(), price=price, type=typ, index=0, strength=SwingStrength.STRONG, displacement_ratio=1.0)


def _break(direction, level, type_="BOS"):
    return StructureBreak(timestamp=_ts(), type=type_, direction=direction, broken_swing=_swing(level),
                           level=level, scope=StructureScope.EXTERNAL, displacement_ratio=1.0, is_mss=(type_ == "CHoCH"))


@pytest.fixture(scope="module")
def engine():
    return TradeManagementEngine()


class TestLiquidityFailure:
    def test_price_through_stop_closes_and_returns_only_that(self, engine):
        actions = engine.evaluate("LONG", entry_price=100.0, stop_loss=98.0, current_price=97.0)
        assert len(actions) == 1
        assert actions[0].action_type == ManagementActionType.CLOSE_LIQUIDITY_FAILURE

    def test_short_direction_mirror(self, engine):
        actions = engine.evaluate("SHORT", entry_price=100.0, stop_loss=102.0, current_price=103.0)
        assert actions[0].action_type == ManagementActionType.CLOSE_LIQUIDITY_FAILURE


class TestBreakEven:
    def test_1r_triggers_breakeven(self, engine):
        actions = engine.evaluate("LONG", entry_price=100.0, stop_loss=98.0, current_price=102.0)
        types = [a.action_type for a in actions]
        assert ManagementActionType.MOVE_TO_BREAKEVEN in types
        be = next(a for a in actions if a.action_type == ManagementActionType.MOVE_TO_BREAKEVEN)
        assert be.new_stop_loss == 100.0

    def test_below_1r_no_breakeven(self, engine):
        actions = engine.evaluate("LONG", entry_price=100.0, stop_loss=98.0, current_price=101.0)
        types = [a.action_type for a in actions]
        assert ManagementActionType.MOVE_TO_BREAKEVEN not in types


class TestPartialClose:
    def test_reached_partial_target_triggers_close(self, engine):
        plan = ExitPlan(direction="LONG", primary_target=None, partial_targets=[
            ExitLevel(kind="partial", price=103.0, size_fraction=0.5, reason="halfway")
        ])
        actions = engine.evaluate("LONG", entry_price=100.0, stop_loss=98.0, current_price=104.0, exit_plan=plan)
        types = [a.action_type for a in actions]
        assert ManagementActionType.PARTIAL_CLOSE in types


class TestStructureFailureAndTrailing:
    def test_opposing_break_triggers_structure_failure_and_risk_reduction(self, engine):
        actions = engine.evaluate("LONG", entry_price=100.0, stop_loss=98.0, current_price=101.0,
                                   structure_breaks=[_break("bearish", 99.0)])
        types = [a.action_type for a in actions]
        assert ManagementActionType.CLOSE_STRUCTURE_FAILURE in types
        assert ManagementActionType.REDUCE_RISK in types

    def test_same_direction_break_trails_stop_forward_only(self, engine):
        actions = engine.evaluate("LONG", entry_price=100.0, stop_loss=98.0, current_price=101.0,
                                   structure_breaks=[_break("bullish", 99.5)])
        trail = next((a for a in actions if a.action_type == ManagementActionType.TRAIL_STOP), None)
        assert trail is not None
        assert trail.new_stop_loss == 99.5

    def test_same_direction_break_that_would_worsen_stop_is_not_trailed(self, engine):
        actions = engine.evaluate("LONG", entry_price=100.0, stop_loss=99.0, current_price=101.0,
                                   structure_breaks=[_break("bullish", 97.0)])
        trail = next((a for a in actions if a.action_type == ManagementActionType.TRAIL_STOP), None)
        assert trail is None


class TestSessionChange:
    def test_off_session_produces_note(self, engine):
        class _Ctx:
            is_off_session = True
        actions = engine.evaluate("LONG", entry_price=100.0, stop_loss=98.0, current_price=100.5, session_context=_Ctx())
        types = [a.action_type for a in actions]
        assert ManagementActionType.SESSION_CHANGE_NOTE in types


class TestHold:
    def test_nothing_triggered_returns_hold(self, engine):
        actions = engine.evaluate("LONG", entry_price=100.0, stop_loss=98.0, current_price=100.2)
        assert actions == [actions[0]]
        assert actions[0].action_type == ManagementActionType.HOLD
