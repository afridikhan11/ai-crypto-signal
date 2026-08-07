"""Regression suite for app.smc.silver_bullet."""

import pandas as pd

from app.smc.fvg import FairValueGap, FVGType
from app.smc.silver_bullet import (
    SilverBulletEngine, SilverBulletWindowName,
)


def _df_ending_at(ts_str, periods=6):
    end = pd.Timestamp(ts_str)
    idx = pd.date_range(end=end, periods=periods, freq="15min")
    return pd.DataFrame(
        {"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 1000.0},
        index=idx,
    )


def test_active_window_am():
    eng = SilverBulletEngine()
    w = eng.active_window(pd.Timestamp("2026-01-05 15:30"))  # 15:30 UTC = AM SB
    assert w is not None and w.name == SilverBulletWindowName.AM


def test_no_window_outside():
    eng = SilverBulletEngine()
    assert eng.active_window(pd.Timestamp("2026-01-05 12:00")) is None


def test_all_three_windows_exist():
    eng = SilverBulletEngine()
    assert eng.active_window(pd.Timestamp("2026-01-05 08:30")).name == SilverBulletWindowName.LONDON
    assert eng.active_window(pd.Timestamp("2026-01-05 19:30")).name == SilverBulletWindowName.PM


def test_detect_setup_with_aligned_fvg_in_window():
    # Last candle at 15:30 UTC (AM SB). A bullish FVG formed at 15:15 (in window).
    df = _df_ending_at("2026-01-05 15:30")
    fvg = FairValueGap(
        timestamp=pd.Timestamp("2026-01-05 15:15"), top=100.4, bottom=100.0,
        type=FVGType.BULLISH, filled=False,
    )
    setup = SilverBulletEngine().detect(df, "bullish", fvgs=[fvg])
    assert setup is not None
    assert setup.window == SilverBulletWindowName.AM
    assert setup.direction == "bullish"
    assert setup.entry_price == (100.4 + 100.0) / 2  # consequent encroachment


def test_no_setup_when_fvg_direction_mismatch():
    df = _df_ending_at("2026-01-05 15:30")
    fvg = FairValueGap(
        timestamp=pd.Timestamp("2026-01-05 15:15"), top=100.4, bottom=100.0,
        type=FVGType.BEARISH, filled=False,
    )
    assert SilverBulletEngine().detect(df, "bullish", fvgs=[fvg]) is None


def test_no_setup_outside_window():
    df = _df_ending_at("2026-01-05 12:00")  # not an SB window
    fvg = FairValueGap(
        timestamp=pd.Timestamp("2026-01-05 11:45"), top=100.4, bottom=100.0,
        type=FVGType.BULLISH, filled=False,
    )
    assert SilverBulletEngine().detect(df, "bullish", fvgs=[fvg]) is None


def test_no_setup_when_fvg_filled():
    df = _df_ending_at("2026-01-05 15:30")
    fvg = FairValueGap(
        timestamp=pd.Timestamp("2026-01-05 15:15"), top=100.4, bottom=100.0,
        type=FVGType.BULLISH, filled=True,
    )
    assert SilverBulletEngine().detect(df, "bullish", fvgs=[fvg]) is None
