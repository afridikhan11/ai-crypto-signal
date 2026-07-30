"""
Regression suite for app.smc.supply_demand_engine.

Ported from this project's own standalone verification pass (22/22 checks,
originally run via importlib against the real production file). Exercises
real zone detection (Fresh/Tested/Mitigated/Broken lifecycle), the
backward-compatible Premium/Discount contract (byte-for-byte preserved
from the deleted legacy engine so app.ai.scorer required zero changes
across the whole SMC->ICT migration series), BOS/CHoCH/OB/Liquidity/FVG
relationship tagging, strength grading, multi-timeframe tagging, and
determinism.

Supersedes and subsumes the narrower tests/test_supply_demand.py (which
covered only the Premium/Discount contract - see test class
TestPremiumDiscountContract below for the same byte-for-byte coverage).
"""
import pandas as pd
import pytest

from app.smc.fvg import FairValueGap, FVGType
from app.smc.liquidity_engine import (
    LiquidityKind,
    LiquidityLevel,
    LiquidityScope,
    LiquidityStrength,
    LiquidityType,
)
from app.smc.market_structure_engine import (
    StructureBreak,
    StructureScope,
    SwingPoint,
    SwingStrength,
    SwingType,
)
from app.smc.order_block_engine import BlockType, OBState, OBStrength, OrderBlock
from app.smc.supply_demand_engine import SupplyDemandEngine, ZoneState, ZoneStrength


def _make_df(n, base=100.0):
    idx = pd.date_range("2026-01-01", periods=n, freq="15min")
    data = {
        "open": [base] * n, "high": [base + 0.3] * n, "low": [base - 0.3] * n,
        "close": [base] * n, "volume": [1000.0] * n,
    }
    return pd.DataFrame(data, index=idx)


def _build_ob(ts, high, low, block_type, state=OBState.FRESH):
    return OrderBlock(
        timestamp=ts, high=high, low=low, type=block_type, state=state,
        mitigated=(state != OBState.FRESH), displacement_ratio=1.5,
        caused_bos=False, caused_choch=False, related_break_level=None,
        has_fvg_confluence=False, strength=OBStrength.STRONG,
        mitigated_at=None, breaker_at=None, invalidated_at=None,
    )


def _build_liq(price, liq_type, swept=True):
    return LiquidityLevel(
        price=price, type=liq_type, swing_points=[], kind=LiquidityKind.SINGLE_SWING,
        scope=LiquidityScope.EXTERNAL, strength=LiquidityStrength.MODERATE, swept=swept,
    )


def _build_zone_series(direction="up", n_warmup=20, base_len=3, disp_candles=6,
                        return_to_zone=False, break_through=False, base_price=100.0,
                        base_half_range=0.1, disp_step=1.0):
    """A coherent 'warmup trend + tight base + clean displacement' series
    (open[i] = close[i-1], same style used across this project's other
    engine regression suites)."""
    rows = []
    price = base_price
    for _ in range(n_warmup):
        o = price
        c = price + (0.05 if direction == "up" else -0.05)
        h = max(o, c) + 0.3
        low = min(o, c) - 0.3
        rows.append((o, h, low, c))
        price = c

    base_top = base_price + base_half_range
    base_bottom = base_price - base_half_range
    for _ in range(base_len):
        rows.append((base_price, base_top, base_bottom, base_price))

    disp_price = base_top + disp_step * 2 if direction == "up" else base_bottom - disp_step * 2
    for _ in range(disp_candles):
        if direction == "up":
            o = disp_price
            c = disp_price + disp_step
            h = c + 0.2
            low = o - 0.05
        else:
            o = disp_price
            c = disp_price - disp_step
            h = o + 0.05
            low = c - 0.2
        rows.append((o, h, low, c))
        disp_price = c

    if return_to_zone:
        if direction == "up":
            o = disp_price
            c = base_top + 0.3
            h = max(o, c) + 0.1
            low = base_bottom - 0.05
        else:
            o = disp_price
            c = base_bottom - 0.3
            h = base_top + 0.05
            low = min(o, c) - 0.1
        rows.append((o, h, low, c))
        tail = c
        for _ in range(5):
            step = 0.3 if direction == "up" else -0.3
            rows.append((tail, tail + abs(step) + 0.05, tail - abs(step) - 0.05, tail + step))
            tail += step

    if break_through:
        if direction == "up":
            o = base_top - 0.02
            h = base_top - 0.01
            low = base_bottom - 0.5
            c = base_bottom - 0.3
        else:
            o = base_bottom + 0.02
            h = base_top + 0.5
            low = base_bottom + 0.01
            c = base_top + 0.3
        rows.append((o, h, low, c))
        tail = c
        for _ in range(5):
            step = -0.3 if direction == "up" else 0.3
            rows.append((tail, tail + abs(step) + 0.05, tail - abs(step) - 0.05, tail + step))
            tail += step

    n = len(rows)
    idx = pd.date_range("2026-01-01", periods=n, freq="15min")
    opens = [r[0] for r in rows]
    highs = [r[1] for r in rows]
    lows = [r[2] for r in rows]
    closes = [r[3] for r in rows]
    df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": [1000.0] * n},
        index=idx,
    )
    return df, base_top, base_bottom


class TestZoneLifecycle:
    def test_clean_demand_zone_found_and_fresh(self):
        df, _, _ = _build_zone_series(direction="up")
        zones = SupplyDemandEngine(df, min_displacement_atr=1.0).find_demand_zones()
        assert len(zones) == 1
        assert zones[0].state == ZoneState.FRESH

    def test_clean_supply_zone_found_and_fresh(self):
        df, _, _ = _build_zone_series(direction="down")
        zones = SupplyDemandEngine(df, min_displacement_atr=1.0).find_supply_zones()
        assert len(zones) == 1
        assert zones[0].state == ZoneState.FRESH

    def test_demand_zone_wicked_but_closed_outside_is_tested(self):
        df, _, _ = _build_zone_series(direction="up", return_to_zone=True)
        zones = SupplyDemandEngine(df, min_displacement_atr=1.0).find_demand_zones()
        assert len(zones) == 1
        assert zones[0].state in (ZoneState.TESTED, ZoneState.MITIGATED)

    def test_demand_zone_closed_through_bottom_is_broken(self):
        df, _, _ = _build_zone_series(direction="up", break_through=True)
        zones = SupplyDemandEngine(df, min_displacement_atr=1.0).find_demand_zones()
        assert len(zones) == 1
        assert zones[0].state == ZoneState.BROKEN
        assert zones[0].broken_at is not None

    def test_supply_zone_closed_through_top_is_broken(self):
        df, _, _ = _build_zone_series(direction="down", break_through=True)
        zones = SupplyDemandEngine(df, min_displacement_atr=1.0).find_supply_zones()
        assert len(zones) == 1
        assert zones[0].state == ZoneState.BROKEN

    def test_weak_displacement_below_threshold_produces_no_zone(self):
        df, _, _ = _build_zone_series(direction="up", disp_step=0.05)
        zones = SupplyDemandEngine(df, min_displacement_atr=3.0).find_demand_zones()
        assert len(zones) == 0


class TestPremiumDiscountContract:
    """Byte-for-byte preserved from the deleted legacy supply_demand.py -
    the exact contract app.ai.scorer's supply_demand_zone block reads."""

    def test_above_fib_618_is_premium(self):
        df = pd.DataFrame({"high": [200.0] * 50, "low": [100.0] * 50, "close": [150.0] * 50,
                            "open": [150.0] * 50, "volume": [1000.0] * 50})
        eng = SupplyDemandEngine(df)
        eng.calculate_recent_range()
        assert eng.get_zone(210) == "premium"

    def test_below_fib_382_is_discount(self):
        df = pd.DataFrame({"high": [200.0] * 50, "low": [100.0] * 50, "close": [150.0] * 50,
                            "open": [150.0] * 50, "volume": [1000.0] * 50})
        eng = SupplyDemandEngine(df)
        eng.calculate_recent_range()
        assert eng.get_zone(90) == "discount"

    def test_between_fibs_is_equilibrium(self):
        df = pd.DataFrame({"high": [200.0] * 50, "low": [100.0] * 50, "close": [150.0] * 50,
                            "open": [150.0] * 50, "volume": [1000.0] * 50})
        eng = SupplyDemandEngine(df)
        eng.calculate_recent_range()
        assert eng.get_zone(150) == "equilibrium"

    def test_boundary_edges(self):
        df = pd.DataFrame({"high": [200.0] * 50, "low": [100.0] * 50, "close": [150.0] * 50,
                            "open": [150.0] * 50, "volume": [1000.0] * 50})
        eng = SupplyDemandEngine(df)
        eng.calculate_recent_range()
        fib_0_382 = 100 + 0.382 * 100
        fib_0_618 = 100 + 0.618 * 100
        assert eng.get_zone(fib_0_618) == "premium"
        assert eng.get_zone(fib_0_382) == "discount"
        assert eng.get_zone((fib_0_382 + fib_0_618) / 2) == "equilibrium"

    def test_insufficient_data_returns_unknown(self):
        df = pd.DataFrame({"high": [100.0] * 5, "low": [90.0] * 5, "close": [95.0] * 5,
                            "open": [95.0] * 5, "volume": [1000.0] * 5})
        eng = SupplyDemandEngine(df)
        eng.calculate_recent_range(lookback=50)
        assert eng.get_zone(95) == "unknown"

    def test_zero_range_returns_equilibrium(self):
        df = pd.DataFrame({"high": [100.0] * 50, "low": [100.0] * 50, "close": [100.0] * 50,
                            "open": [100.0] * 50, "volume": [1000.0] * 50})
        eng = SupplyDemandEngine(df)
        eng.calculate_recent_range()
        assert eng.get_zone(100) == "equilibrium"


class TestRelationshipTagging:
    def test_demand_zone_linked_to_bullish_choch(self):
        df, top, bottom = _build_zone_series(direction="up")
        bullish_choch = StructureBreak(
            timestamp=df.index[-5], type="CHoCH", direction="bullish",
            broken_swing=SwingPoint(
                timestamp=df.index[-6], price=top, type=SwingType.HH, index=len(df) - 6,
                strength=SwingStrength.STRONG, displacement_ratio=1.0,
            ),
            level=top, scope=StructureScope.EXTERNAL, displacement_ratio=1.2, is_mss=True,
        )
        zones = SupplyDemandEngine(
            df, min_displacement_atr=1.0, structure_breaks=[bullish_choch]
        ).find_demand_zones()
        assert len(zones) == 1
        assert zones[0].caused_choch
        assert zones[0].strength == ZoneStrength.STRONG

    def test_opposite_direction_break_ignored(self):
        df, top, bottom = _build_zone_series(direction="up")
        bearish_break = StructureBreak(
            timestamp=df.index[-5], type="BOS", direction="bearish",
            broken_swing=SwingPoint(
                timestamp=df.index[-6], price=bottom, type=SwingType.LL, index=len(df) - 6,
                strength=SwingStrength.STRONG, displacement_ratio=1.0,
            ),
            level=bottom, scope=StructureScope.EXTERNAL, displacement_ratio=1.2, is_mss=False,
        )
        zones = SupplyDemandEngine(
            df, min_displacement_atr=1.0, structure_breaks=[bearish_break]
        ).find_demand_zones()
        assert len(zones) == 1
        assert not zones[0].caused_bos
        assert not zones[0].caused_choch

    def test_overlapping_ob_tags_confluence(self):
        df, top, bottom = _build_zone_series(direction="up")
        overlapping_ob = _build_ob(df.index[-3], high=top + 0.02, low=bottom - 0.02, block_type=BlockType.BULLISH_OB)
        zones = SupplyDemandEngine(
            df, min_displacement_atr=1.0, order_blocks=[overlapping_ob]
        ).find_demand_zones()
        assert len(zones) == 1
        assert zones[0].has_ob_confluence

    def test_non_overlapping_ob_does_not_tag_confluence(self):
        df, top, bottom = _build_zone_series(direction="up")
        non_overlapping_ob = _build_ob(df.index[-3], high=top + 50, low=top + 40, block_type=BlockType.BULLISH_OB)
        zones = SupplyDemandEngine(
            df, min_displacement_atr=1.0, order_blocks=[non_overlapping_ob]
        ).find_demand_zones()
        assert len(zones) == 1
        assert not zones[0].has_ob_confluence

    def test_liquidity_level_inside_range_tags_confluence(self):
        df, top, bottom = _build_zone_series(direction="up")
        liq_inside = _build_liq((top + bottom) / 2, LiquidityType.SELLSIDE)
        zones = SupplyDemandEngine(
            df, min_displacement_atr=1.0, liquidity_levels=[liq_inside]
        ).find_demand_zones()
        assert len(zones) == 1
        assert zones[0].has_liquidity_confluence

    def test_overlapping_fvg_tags_confluence(self):
        df, top, bottom = _build_zone_series(direction="up")
        overlapping_fvg = FairValueGap(timestamp=df.index[-3], top=top + 0.01, bottom=bottom - 0.01, type=FVGType.BULLISH)
        zones = SupplyDemandEngine(
            df, min_displacement_atr=1.0, fvgs=[overlapping_fvg]
        ).find_demand_zones()
        assert len(zones) == 1
        assert zones[0].has_fvg_confluence


class TestStrengthGrading:
    def test_large_displacement_no_confluence_is_at_least_moderate(self):
        df, _, _ = _build_zone_series(direction="up", disp_step=3.0)
        zones = SupplyDemandEngine(df, min_displacement_atr=1.0).find_demand_zones()
        assert len(zones) == 1
        assert zones[0].strength in (ZoneStrength.MODERATE, ZoneStrength.STRONG)

    def test_modest_displacement_no_confluence_is_weak(self):
        df, _, _ = _build_zone_series(direction="up", disp_step=0.18, disp_candles=1)
        zones = SupplyDemandEngine(df, min_displacement_atr=1.0).find_demand_zones()
        assert len(zones) == 1
        assert zones[0].strength == ZoneStrength.WEAK


class TestMiscBehavior:
    def test_timeframe_tag_passes_through(self):
        df, _, _ = _build_zone_series(direction="up")
        zones = SupplyDemandEngine(df, min_displacement_atr=1.0, timeframe="4h").find_demand_zones()
        assert len(zones) == 1
        assert zones[0].timeframe == "4h"

    def test_flat_insufficient_data_no_crash(self):
        eng = SupplyDemandEngine(_make_df(10), min_displacement_atr=1.0)
        assert eng.find_demand_zones() == []
        assert eng.find_supply_zones() == []

    def test_repeated_calls_return_equivalent_zones(self):
        df, _, _ = _build_zone_series(direction="up")
        eng = SupplyDemandEngine(df, min_displacement_atr=1.0)
        first = eng.find_demand_zones()
        second = eng.find_demand_zones()
        assert len(first) == len(second) == 1
        assert first[0].top == second[0].top
        assert first[0].state == second[0].state
