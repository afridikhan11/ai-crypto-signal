"""Unit tests for deterministic ICT components."""

from datetime import datetime, timezone

import pandas as pd

from app.smc.sessions import active_kill_zone, kill_zone_weight, in_kill_zone
from app.smc.ote import compute_ote, OTE_MIN, OTE_MAX
from app.smc.displacement import DisplacementLeg, DisplacementType
from app.smc.key_levels import compute_key_levels
from tests.conftest import build_df, uptrend_closes


# ---------------------------------------------------------------- sessions
def test_kill_zone_new_york_am():
    kz = active_kill_zone(datetime(2024, 1, 1, 13, 0, tzinfo=timezone.utc))
    assert kz is not None and kz.name == "New York AM"
    assert in_kill_zone(datetime(2024, 1, 1, 13, 0, tzinfo=timezone.utc))


def test_kill_zone_outside():
    kz = active_kill_zone(datetime(2024, 1, 1, 22, 0, tzinfo=timezone.utc))
    assert kz is None
    assert kill_zone_weight(datetime(2024, 1, 1, 22, 0, tzinfo=timezone.utc)) == 0.3


def test_kill_zone_naive_treated_as_utc():
    assert active_kill_zone(datetime(2024, 1, 1, 8, 0)).name == "London"


# ---------------------------------------------------------------- OTE
def test_ote_bullish_window():
    leg = DisplacementLeg(DisplacementType.BULLISH, index=5, origin=100.0,
                          extreme=110.0, body_atr=2.0, has_fvg=True,
                          fvg_top=None, fvg_bottom=None)
    zone = compute_ote(leg)
    # 0.62-0.79 retrace of a 100->110 leg => 103.9 .. 101.0
    assert round(zone.top, 1) == round(110 - 10 * OTE_MIN, 1)
    assert round(zone.bottom, 1) == round(110 - 10 * OTE_MAX, 1)
    assert zone.contains(103.0)
    assert not zone.contains(108.0)
    assert abs(zone.sweet_spot - (110 - 10 * 0.705)) < 1e-9


def test_ote_bearish_window():
    leg = DisplacementLeg(DisplacementType.BEARISH, index=5, origin=110.0,
                          extreme=100.0, body_atr=2.0, has_fvg=True,
                          fvg_top=None, fvg_bottom=None)
    zone = compute_ote(leg)
    assert zone.bottom < zone.sweet_spot < zone.top
    assert zone.contains(106.5)


# ---------------------------------------------------------------- key levels
def test_key_levels_prev_day_and_asian():
    # Two full UTC days of hourly candles.
    closes = uptrend_closes(48, base=100.0, slope=0.1)
    df = build_df(closes, start="2024-01-01 00:00:00", freq="1h", wick=0.2)
    levels = compute_key_levels(df)
    assert levels.prev_day_high is not None
    assert levels.prev_day_low is not None
    assert levels.asian_high is not None
    assert levels.prev_day_high >= levels.prev_day_low


def test_key_levels_empty():
    levels = compute_key_levels(pd.DataFrame())
    assert levels.prev_day_high is None
