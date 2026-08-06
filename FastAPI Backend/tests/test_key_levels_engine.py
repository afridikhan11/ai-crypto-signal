"""Regression suite for app.smc.key_levels_engine (PDH/PDL/PWH/PWL + Asian)."""

import numpy as np
import pandas as pd

from app.smc.key_levels_engine import KeyLevelsEngine, LevelKind


def _hourly_df(days=9, start="2026-01-01 00:00", base=100.0):
    n = days * 24
    idx = pd.date_range(start=start, periods=n, freq="1h")  # tz-naive == UTC
    t = np.arange(n)
    closes = base + 5 * np.sin(t / 12.0) + t * 0.02
    highs = closes + 0.5
    lows = closes - 0.5
    return pd.DataFrame(
        {"open": closes, "high": highs, "low": lows, "close": closes, "volume": 1000.0},
        index=idx,
    )


def test_empty_df_returns_no_levels():
    snap = KeyLevelsEngine().analyze(pd.DataFrame())
    assert snap.levels == []
    assert snap.nearest_unswept_above is None


def test_previous_and_current_day_levels_present():
    snap = KeyLevelsEngine().analyze(_hourly_df())
    for kind in (LevelKind.PDH, LevelKind.PDL, LevelKind.CDH, LevelKind.CDL):
        lv = snap.by_kind(kind)
        assert lv is not None, f"{kind} missing"
    assert snap.by_kind(LevelKind.PDH).price >= snap.by_kind(LevelKind.PDL).price


def test_previous_week_levels_present_with_enough_history():
    snap = KeyLevelsEngine().analyze(_hourly_df(days=14))
    assert snap.by_kind(LevelKind.PWH) is not None
    assert snap.by_kind(LevelKind.PWL) is not None
    assert snap.by_kind(LevelKind.PWH).price >= snap.by_kind(LevelKind.PWL).price


def test_asian_range_present():
    snap = KeyLevelsEngine().analyze(_hourly_df())
    assert snap.by_kind(LevelKind.ASIAN_HIGH) is not None
    assert snap.by_kind(LevelKind.ASIAN_LOW) is not None


def test_buyside_levels_sit_above_sellside():
    snap = KeyLevelsEngine().analyze(_hourly_df())
    buysides = [lv for lv in snap.levels if lv.is_buyside]
    sellsides = [lv for lv in snap.levels if not lv.is_buyside]
    assert all(lv.kind in (LevelKind.PDH, LevelKind.PWH, LevelKind.CDH,
                           LevelKind.CWH, LevelKind.ASIAN_HIGH) for lv in buysides)
    assert all(not lv.is_buyside for lv in sellsides)


def test_sweep_flag_consistency():
    df = _hourly_df()
    snap = KeyLevelsEngine().analyze(df)
    price = float(df["close"].iloc[-1])
    for lv in snap.levels:
        expected = (price > lv.price) if lv.is_buyside else (price < lv.price)
        assert lv.swept == expected


def test_nearest_unswept_above_is_above_price():
    df = _hourly_df()
    snap = KeyLevelsEngine().analyze(df)
    price = float(df["close"].iloc[-1])
    if snap.nearest_unswept_above is not None:
        assert snap.nearest_unswept_above.price > price
        assert not snap.nearest_unswept_above.swept
    if snap.nearest_unswept_below is not None:
        assert snap.nearest_unswept_below.price < price
