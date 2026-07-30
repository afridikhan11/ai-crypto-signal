"""
Regression suite for app.smc.inducement_engine (Phase C of the
institutional ICT migration).

Covers the full positive case (every required ICT condition satisfied),
one dedicated rejection test per individual missing condition (the false-
positive-minimization mandate - Inducement must NOT fire unless ALL
conditions hold), the bullish mirror, determinism, non-mutation of the
LiquidityLevel objects this engine reads, and a mixed-batch test proving
only genuinely valid levels produce events.
"""
import pandas as pd
import pytest

from app.smc.inducement_engine import InducementEngine
from app.smc.liquidity_engine import (
    LiquidityKind,
    LiquidityLevel,
    LiquidityScope,
    LiquidityStrength,
    LiquidityType,
    SweepOutcome,
)
from app.smc.market_structure_engine import (
    StructureBreak,
    StructureScope,
    SwingPoint,
    SwingStrength,
    SwingType,
)


def _make_df(n=40, base=100.0):
    idx = pd.date_range("2026-01-01", periods=n, freq="15min")
    data = {
        "open": [base] * n, "high": [base + 0.2] * n, "low": [base - 0.2] * n,
        "close": [base] * n, "volume": [1000.0] * n,
    }
    return pd.DataFrame(data, index=idx)


def _set_close(df, pos, value):
    df.loc[df.index[pos], "close"] = value


def _swing(df, idx, price, typ):
    return SwingPoint(
        timestamp=df.index[idx], price=price, type=typ, index=idx,
        strength=SwingStrength.STRONG, displacement_ratio=1.0,
    )


def _valid_bsl_level(df, swept_pos=20, price=110.0, **overrides):
    """A BSL LiquidityLevel with every field set to satisfy the full
    Inducement gate by default - individual tests override one field at a
    time to prove that single-condition failure correctly rejects it."""
    defaults = dict(
        price=price, type=LiquidityType.BUYSIDE,
        swing_points=[_swing(df, 5, price, SwingType.HH), _swing(df, 15, price + 0.01, SwingType.HH)],
        kind=LiquidityKind.EQUAL_HIGHS, scope=LiquidityScope.EXTERNAL, strength=LiquidityStrength.STRONG,
        swept=True, engineered=True, sweep_outcome=SweepOutcome.GRAB,
        swept_at=df.index[swept_pos], sweep_confirmed=True,
        reversal_break_level=95.0, reversal_is_choch=True,
    )
    defaults.update(overrides)
    return LiquidityLevel(**defaults)


def _matching_choch_break(df, timestamp_pos=22, level=95.0, displacement_ratio=1.5):
    return StructureBreak(
        timestamp=df.index[timestamp_pos], type="CHoCH", direction="bearish",
        broken_swing=_swing(df, timestamp_pos - 2, level, SwingType.LL),
        level=level, scope=StructureScope.EXTERNAL,
        displacement_ratio=displacement_ratio, is_mss=True,
    )


def _apply_bearish_continuation(df, break_pos=22, lookahead=5):
    """Closes strictly decreasing and below the break level for the
    continuation window - satisfies item 7 without round-tripping."""
    for i, pos in enumerate(range(break_pos + 1, break_pos + 1 + lookahead)):
        _set_close(df, pos, 94.0 - i)


class TestFullValidInducement:
    def test_all_conditions_met_produces_event(self):
        df = _make_df()
        level = _valid_bsl_level(df)
        brk = _matching_choch_break(df)
        _apply_bearish_continuation(df)

        events = InducementEngine(df, structure_breaks=[brk]).detect([level])
        assert len(events) == 1
        event = events[0]
        assert event.reversal_direction == "bearish"
        assert event.reversal_break_type == "CHoCH"
        assert event.displacement_ratio == 1.5
        assert event.continuation_confirmed is True
        assert event.swept_liquidity_price == 110.0

    def test_conditions_met_audit_trail_is_complete(self):
        df = _make_df()
        level = _valid_bsl_level(df)
        brk = _matching_choch_break(df)
        _apply_bearish_continuation(df)

        events = InducementEngine(df, structure_breaks=[brk]).detect([level])
        trail = " | ".join(events[0].conditions_met)
        assert "sweep confirmed" in trail.lower()
        assert "build-up" in trail.lower()
        assert "engineered" in trail.lower()
        assert "trap" in trail.lower()
        assert "choch" in trail.lower()
        assert "displacement" in trail.lower()
        assert "continuation" in trail.lower()


class TestRejectionsMinimizeFalsePositives:
    def test_rejects_when_not_engineered(self):
        df = _make_df()
        level = _valid_bsl_level(df, engineered=False)
        brk = _matching_choch_break(df)
        _apply_bearish_continuation(df)
        assert InducementEngine(df, structure_breaks=[brk]).detect([level]) == []

    def test_rejects_breakout_outcome_not_a_trap(self):
        df = _make_df()
        level = _valid_bsl_level(df, sweep_outcome=SweepOutcome.BREAKOUT)
        brk = _matching_choch_break(df)
        _apply_bearish_continuation(df)
        assert InducementEngine(df, structure_breaks=[brk]).detect([level]) == []

    def test_rejects_single_swing_no_build_up(self):
        df = _make_df()
        level = _valid_bsl_level(df, kind=LiquidityKind.SINGLE_SWING)
        brk = _matching_choch_break(df)
        _apply_bearish_continuation(df)
        assert InducementEngine(df, structure_breaks=[brk]).detect([level]) == []

    def test_rejects_no_reversal_break_level(self):
        df = _make_df()
        level = _valid_bsl_level(df, reversal_break_level=None)
        brk = _matching_choch_break(df)
        _apply_bearish_continuation(df)
        assert InducementEngine(df, structure_breaks=[brk]).detect([level]) == []

    def test_rejects_when_no_matching_structure_break_supplied(self):
        df = _make_df()
        level = _valid_bsl_level(df)
        _apply_bearish_continuation(df)
        # reversal_break_level=95.0 is set on the level, but no matching
        # StructureBreak is supplied to this engine at all.
        assert InducementEngine(df, structure_breaks=[]).detect([level]) == []

    def test_rejects_weak_displacement(self):
        df = _make_df()
        level = _valid_bsl_level(df)
        brk = _matching_choch_break(df, displacement_ratio=0.3)  # below default 1.2 threshold
        _apply_bearish_continuation(df)
        assert InducementEngine(df, structure_breaks=[brk]).detect([level]) == []

    def test_rejects_round_tripped_continuation(self):
        df = _make_df()
        level = _valid_bsl_level(df)
        brk = _matching_choch_break(df)
        # Price makes initial progress below the break level, but then
        # closes back ABOVE the original swept liquidity price (110) -
        # a round trip that negates the reversal thesis.
        _set_close(df, 23, 94.0)
        _set_close(df, 24, 111.0)
        assert InducementEngine(df, structure_breaks=[brk]).detect([level]) == []

    def test_rejects_no_progress_beyond_break_level(self):
        df = _make_df()
        level = _valid_bsl_level(df)
        brk = _matching_choch_break(df)
        # Closes stay above the break level (95) for the whole window -
        # no genuine continuation, even though nothing round-trips either.
        for pos in range(23, 28):
            _set_close(df, pos, 96.0)
        assert InducementEngine(df, structure_breaks=[brk]).detect([level]) == []

    def test_rejects_insufficient_data_for_continuation(self):
        df = _make_df(n=23)  # break at index 22 is the LAST candle - no room to look ahead
        level = _valid_bsl_level(df, swept_pos=20)
        brk = _matching_choch_break(df, timestamp_pos=22)
        assert InducementEngine(df, structure_breaks=[brk]).detect([level]) == []

    def test_rejects_unswept_level(self):
        df = _make_df()
        level = _valid_bsl_level(df, swept=False, sweep_confirmed=False, sweep_outcome=SweepOutcome.UNSWEPT)
        brk = _matching_choch_break(df)
        _apply_bearish_continuation(df)
        assert InducementEngine(df, structure_breaks=[brk]).detect([level]) == []

    def test_rejects_unconfirmed_sweep(self):
        df = _make_df()
        level = _valid_bsl_level(df, sweep_confirmed=False)
        brk = _matching_choch_break(df)
        _apply_bearish_continuation(df)
        assert InducementEngine(df, structure_breaks=[brk]).detect([level]) == []


class TestBullishMirror:
    def test_ssl_sweep_with_bos_reversal(self):
        df = _make_df()
        level = LiquidityLevel(
            price=90.0, type=LiquidityType.SELLSIDE,
            swing_points=[_swing(df, 5, 90.0, SwingType.LL), _swing(df, 15, 89.99, SwingType.LL)],
            kind=LiquidityKind.EQUAL_LOWS, scope=LiquidityScope.EXTERNAL, strength=LiquidityStrength.STRONG,
            swept=True, engineered=True, sweep_outcome=SweepOutcome.GRAB,
            swept_at=df.index[20], sweep_confirmed=True,
            reversal_break_level=105.0, reversal_is_choch=False,
        )
        brk = StructureBreak(
            timestamp=df.index[22], type="BOS", direction="bullish",
            broken_swing=_swing(df, 20, 105.0, SwingType.HH),
            level=105.0, scope=StructureScope.EXTERNAL, displacement_ratio=1.8, is_mss=False,
        )
        for i, pos in enumerate(range(23, 28)):
            _set_close(df, pos, 106.0 + i)  # rising, beyond 105, never back below 90

        events = InducementEngine(df, structure_breaks=[brk]).detect([level])
        assert len(events) == 1
        assert events[0].reversal_direction == "bullish"
        assert events[0].reversal_break_type == "BOS"


class TestDeterminismAndNonMutation:
    def test_determinism(self):
        df = _make_df()
        level = _valid_bsl_level(df)
        brk = _matching_choch_break(df)
        _apply_bearish_continuation(df)
        engine = InducementEngine(df, structure_breaks=[brk])
        first = engine.detect([level])
        second = engine.detect([level])
        assert len(first) == len(second) == 1
        assert first[0].displacement_ratio == second[0].displacement_ratio
        assert first[0].reversal_direction == second[0].reversal_direction

    def test_does_not_mutate_liquidity_level(self):
        df = _make_df()
        level = _valid_bsl_level(df)
        brk = _matching_choch_break(df)
        _apply_bearish_continuation(df)
        snapshot = (level.swept, level.sweep_outcome, level.reversal_break_level, level.engineered)
        InducementEngine(df, structure_breaks=[brk]).detect([level])
        assert (level.swept, level.sweep_outcome, level.reversal_break_level, level.engineered) == snapshot


class TestMixedBatch:
    def test_only_valid_levels_in_a_mixed_batch_produce_events(self):
        df = _make_df()
        valid_level = _valid_bsl_level(df, swept_pos=20)
        invalid_level = _valid_bsl_level(df, swept_pos=20, engineered=False, price=111.0)
        brk = _matching_choch_break(df)
        _apply_bearish_continuation(df)

        events = InducementEngine(df, structure_breaks=[brk]).detect([valid_level, invalid_level])
        assert len(events) == 1
        assert events[0].liquidity_level is valid_level
