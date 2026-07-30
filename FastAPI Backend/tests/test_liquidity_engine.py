"""
Regression suite for app.smc.liquidity_engine.

Ported from this project's own standalone verification pass (23/23 checks,
originally run via importlib against the real production file). Builds
small, targeted synthetic OHLCV fixtures and real SwingPoint/
StructureBreak/OrderBlock/FVG objects to exercise every ICT concept the
engine claims to implement: Equal Highs/Lows clustering, lone-swing
liquidity, Internal vs External scope, Liquidity Grab vs Breakout
classification, unconfirmed sweeps, resting liquidity, full-history sweep
scanning, engineered-liquidity detection, strength grading, reversal
linkage (Inducement precursor), OB/FVG confluence tagging, determinism,
and multi-timeframe tagging.
"""
import pandas as pd
import pytest

from app.smc.fvg import FairValueGap, FVGType
from app.smc.liquidity_engine import (
    LiquidityEngine,
    LiquidityKind,
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
from app.smc.order_block_engine import BlockType, OBState, OBStrength, OrderBlock


def _make_df(n=60, base=100.0):
    """Flat, coherent baseline series - individual tests overwrite the
    high/low/close/open of specific candles to build precise scenarios."""
    idx = pd.date_range("2026-01-01", periods=n, freq="15min")
    data = {
        "open": [base] * n,
        "high": [base + 0.1] * n,
        "low": [base - 0.1] * n,
        "close": [base] * n,
        "volume": [1000.0] * n,
    }
    return pd.DataFrame(data, index=idx)


def _sp(idx, price, typ, df, strength=SwingStrength.STRONG):
    return SwingPoint(
        timestamp=df.index[idx], price=price, type=typ, index=idx,
        strength=strength, displacement_ratio=1.0,
    )


def test_equal_highs_cluster_into_bsl():
    df = _make_df(40, base=100.0)
    highs = [_sp(5, 110.00, SwingType.HH, df), _sp(15, 110.02, SwingType.HH, df)]
    lows = [_sp(10, 90.0, SwingType.LL, df)]
    eng = LiquidityEngine(df, highs, lows)
    bsl = eng.detect_buyside_liquidity()
    assert len(bsl) == 1
    assert bsl[0].kind == LiquidityKind.EQUAL_HIGHS
    assert bsl[0].scope == LiquidityScope.EXTERNAL


def test_lone_swing_high_still_produces_single_swing_bsl():
    df = _make_df(40, base=100.0)
    highs = [_sp(5, 111.0, SwingType.HH, df)]
    eng = LiquidityEngine(df, highs, [])
    bsl = eng.detect_buyside_liquidity()
    assert len(bsl) == 1
    assert bsl[0].kind == LiquidityKind.SINGLE_SWING


def test_equal_lows_cluster_into_ssl():
    df = _make_df(40, base=100.0)
    lows = [_sp(5, 90.00, SwingType.LL, df), _sp(15, 90.01, SwingType.LL, df)]
    eng = LiquidityEngine(df, [], lows)
    ssl = eng.detect_sellside_liquidity()
    assert len(ssl) == 1
    assert ssl[0].kind == LiquidityKind.EQUAL_LOWS
    assert ssl[0].type == LiquidityType.SELLSIDE


def test_internal_and_external_bsl_independently_computed():
    df = _make_df(40, base=100.0)
    ext_highs = [_sp(5, 112.0, SwingType.HH, df)]
    int_highs = [_sp(8, 105.0, SwingType.HH, df)]
    eng = LiquidityEngine(df, ext_highs, [], internal_swing_highs=int_highs, internal_swing_lows=[])
    ext = eng.detect_buyside_liquidity()
    internal = eng.detect_internal_buyside_liquidity()
    assert len(ext) == 1 and ext[0].scope == LiquidityScope.EXTERNAL
    assert len(internal) == 1 and internal[0].scope == LiquidityScope.INTERNAL
    assert internal[0].price == 105.0


def test_internal_liquidity_empty_when_not_supplied():
    df = _make_df(40, base=100.0)
    ext_highs = [_sp(5, 112.0, SwingType.HH, df)]
    eng = LiquidityEngine(df, ext_highs, [])
    assert eng.detect_internal_buyside_liquidity() == []


def test_sweep_that_closes_back_below_is_grab():
    df = _make_df(40, base=100.0)
    df.loc[df.index[20], "high"] = 111.0
    df.loc[df.index[20], "close"] = 100.5
    df.loc[df.index[21], "close"] = 99.0
    highs = [_sp(5, 110.0, SwingType.HH, df)]
    eng = LiquidityEngine(df, highs, [])
    bsl = eng.detect_buyside_liquidity()
    swept = eng.detect_liquidity_sweeps(bsl)
    assert swept[0].swept
    assert swept[0].sweep_outcome == SweepOutcome.GRAB
    assert swept[0].sweep_confirmed


def test_sweep_that_closes_beyond_is_breakout():
    df = _make_df(40, base=100.0)
    df.loc[df.index[20], "high"] = 111.0
    df.loc[df.index[20], "close"] = 110.5
    highs = [_sp(5, 110.0, SwingType.HH, df)]
    eng = LiquidityEngine(df, highs, [])
    bsl = eng.detect_buyside_liquidity()
    swept = eng.detect_liquidity_sweeps(bsl)
    assert swept[0].swept
    assert swept[0].sweep_outcome == SweepOutcome.BREAKOUT
    assert swept[0].sweep_confirmed


def test_sweep_with_no_resolving_close_is_unconfirmed():
    df = _make_df(40, base=100.0)
    df.loc[df.index[35], "high"] = 111.0
    for k in range(35, 39):
        df.loc[df.index[k], "close"] = 110.0
    highs = [_sp(5, 110.0, SwingType.HH, df)]
    eng = LiquidityEngine(df, highs, [], sweep_confirmation_window=3)
    bsl = eng.detect_buyside_liquidity()
    swept = eng.detect_liquidity_sweeps(bsl)
    assert swept[0].swept
    assert swept[0].sweep_confirmed is False


def test_untouched_level_is_resting_liquidity():
    df = _make_df(40, base=100.0)
    highs = [_sp(5, 200.0, SwingType.HH, df)]
    eng = LiquidityEngine(df, highs, [])
    bsl = eng.detect_buyside_liquidity()
    swept = eng.detect_liquidity_sweeps(bsl)
    assert not swept[0].swept
    assert swept[0].sweep_outcome == SweepOutcome.UNSWEPT


def test_full_history_sweep_scan_finds_old_sweep():
    """A level swept many candles ago, with no touch since, must still
    report swept=True when checked 'today' (old single-candle bug)."""
    df = _make_df(50, base=100.0)
    df.loc[df.index[10], "high"] = 111.0
    df.loc[df.index[10], "close"] = 99.0
    highs = [_sp(5, 110.0, SwingType.HH, df)]
    eng = LiquidityEngine(df, highs, [])
    bsl = eng.detect_buyside_liquidity()
    swept = eng.detect_liquidity_sweeps(bsl)
    assert swept[0].swept
    assert swept[0].swept_at == df.index[10]


def test_engineered_liquidity_flags_extremely_tight_cluster():
    df = _make_df(40, base=100.0)
    tight_highs = [_sp(5, 110.000, SwingType.HH, df), _sp(15, 110.001, SwingType.HH, df)]
    eng = LiquidityEngine(df, tight_highs, [], tolerance=0.0005)
    lvl = eng.detect_buyside_liquidity()
    assert len(lvl) == 1
    assert lvl[0].engineered


def test_normally_tight_cluster_not_flagged_engineered():
    df = _make_df(40, base=100.0)
    loose_highs = [_sp(5, 110.00, SwingType.HH, df), _sp(15, 110.045, SwingType.HH, df)]
    eng = LiquidityEngine(df, loose_highs, [], tolerance=0.0005)
    lvl = eng.detect_buyside_liquidity()
    assert len(lvl) == 1
    assert not lvl[0].engineered


def test_strength_grading_external_cluster_vs_single_vs_internal():
    df = _make_df(40, base=100.0)
    cluster = [_sp(5, 110.0, SwingType.HH, df), _sp(15, 110.01, SwingType.HH, df)]
    single_ext = [_sp(5, 120.0, SwingType.HH, df)]
    single_int = [_sp(5, 105.0, SwingType.HH, df)]
    eng = LiquidityEngine(
        df, cluster + single_ext, [], internal_swing_highs=single_int, internal_swing_lows=[]
    )
    levels = eng.detect_buyside_liquidity()
    cluster_lvl = next(l for l in levels if l.kind == LiquidityKind.EQUAL_HIGHS)
    single_lvl = next(l for l in levels if l.kind == LiquidityKind.SINGLE_SWING)
    int_lvl = eng.detect_internal_buyside_liquidity()[0]
    assert cluster_lvl.strength == LiquidityStrength.STRONG
    assert single_lvl.strength == LiquidityStrength.MODERATE
    assert int_lvl.strength == LiquidityStrength.WEAK


def test_reversal_linkage_to_opposite_direction_choch():
    """A BSL sweep followed by a BEARISH structure break shortly after is
    tagged with reversal_break_level/reversal_is_choch; a same-direction
    (bullish) break is correctly ignored. This is the precursor evidence
    Inducement detection (Phase C) will build on."""
    df = _make_df(40, base=100.0)
    df.loc[df.index[10], "high"] = 111.0
    df.loc[df.index[10], "close"] = 99.0
    highs = [_sp(5, 110.0, SwingType.HH, df)]
    bearish_break = StructureBreak(
        timestamp=df.index[12], type="CHoCH", direction="bearish",
        broken_swing=highs[0], level=95.0, scope=StructureScope.EXTERNAL,
        displacement_ratio=1.2, is_mss=True,
    )
    bullish_break_same_window = StructureBreak(
        timestamp=df.index[11], type="BOS", direction="bullish",
        broken_swing=highs[0], level=112.0, scope=StructureScope.EXTERNAL,
        displacement_ratio=1.0, is_mss=False,
    )
    eng = LiquidityEngine(df, highs, [], structure_breaks=[bullish_break_same_window, bearish_break])
    bsl = eng.detect_buyside_liquidity()
    swept = eng.detect_liquidity_sweeps(bsl)
    assert swept[0].reversal_break_level == 95.0
    assert swept[0].reversal_is_choch is True


def test_ob_and_fvg_confluence_tagged_after_sweep():
    df = _make_df(40, base=100.0)
    df.loc[df.index[10], "high"] = 111.0
    df.loc[df.index[10], "close"] = 99.0
    highs = [_sp(5, 110.0, SwingType.HH, df)]
    bear_ob = OrderBlock(
        timestamp=df.index[12], high=101.0, low=99.5, type=BlockType.BEARISH_OB,
        state=OBState.FRESH, mitigated=False, displacement_ratio=1.5,
        caused_bos=False, caused_choch=False, related_break_level=None,
        has_fvg_confluence=False, strength=OBStrength.STRONG,
        mitigated_at=None, breaker_at=None, invalidated_at=None,
    )
    bear_fvg = FairValueGap(timestamp=df.index[13], top=100.0, bottom=98.0, type=FVGType.BEARISH)
    eng = LiquidityEngine(df, highs, [], order_blocks=[bear_ob], fvgs=[bear_fvg])
    bsl = eng.detect_buyside_liquidity()
    swept = eng.detect_liquidity_sweeps(bsl)
    assert swept[0].has_ob_confluence is True
    assert swept[0].has_fvg_confluence is True


def test_ssl_grab_mirror_on_lows_side():
    df = _make_df(40, base=100.0)
    df.loc[df.index[20], "low"] = 89.0
    df.loc[df.index[20], "close"] = 90.5
    lows = [_sp(5, 90.0, SwingType.LL, df)]
    eng = LiquidityEngine(df, [], lows)
    ssl = eng.detect_sellside_liquidity()
    swept = eng.detect_liquidity_sweeps(ssl)
    assert swept[0].swept
    assert swept[0].sweep_outcome == SweepOutcome.GRAB


def test_sweep_detection_is_idempotent():
    df = _make_df(40, base=100.0)
    df.loc[df.index[10], "high"] = 111.0
    df.loc[df.index[10], "close"] = 99.0
    highs = [_sp(5, 110.0, SwingType.HH, df)]
    eng = LiquidityEngine(df, highs, [])
    bsl = eng.detect_buyside_liquidity()
    first = eng.detect_liquidity_sweeps(bsl)
    snapshot = (first[0].swept, first[0].sweep_outcome, first[0].swept_at)
    second = eng.detect_liquidity_sweeps(first)
    assert (second[0].swept, second[0].sweep_outcome, second[0].swept_at) == snapshot


def test_timeframe_tag_passes_through():
    df = _make_df(20, base=100.0)
    eng = LiquidityEngine(df, [_sp(5, 110.0, SwingType.HH, df)], [], timeframe="1h")
    lvl = eng.detect_buyside_liquidity()
    assert lvl[0].timeframe == "1h"


def test_no_swings_no_crash():
    eng = LiquidityEngine(_make_df(10), [], [])
    assert eng.detect_buyside_liquidity() == []
    assert eng.detect_sellside_liquidity() == []


def test_resting_liquidity_is_swept_false_subset():
    df_grab = _make_df(40, base=100.0)
    df_grab.loc[df_grab.index[20], "high"] = 111.0
    df_grab.loc[df_grab.index[20], "close"] = 100.5
    df_grab.loc[df_grab.index[21], "close"] = 99.0
    eng_grab = LiquidityEngine(df_grab, [_sp(5, 110.0, SwingType.HH, df_grab)], [])
    swept_grab = eng_grab.detect_liquidity_sweeps(eng_grab.detect_buyside_liquidity())

    df_untouched = _make_df(40, base=100.0)
    eng_untouched = LiquidityEngine(df_untouched, [_sp(5, 200.0, SwingType.HH, df_untouched)], [])
    swept_untouched = eng_untouched.detect_liquidity_sweeps(eng_untouched.detect_buyside_liquidity())

    resting = [l for l in swept_grab + swept_untouched if not l.swept]
    assert len(resting) == 1
    assert resting[0] is swept_untouched[0]
