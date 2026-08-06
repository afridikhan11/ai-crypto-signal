"""Regression suite for app.smc.judas_swing."""

import numpy as np
import pandas as pd

from app.smc.judas_swing import JudasSwingEngine, JudasSession


def _day_df(bars):
    """bars: list of (hour, high, low, close). 15-min candles at the given
    UTC hours on 2026-01-05."""
    rows, idx = [], []
    for hour, hi, lo, cl in bars:
        idx.append(pd.Timestamp("2026-01-05 00:00") + pd.Timedelta(hours=hour))
        rows.append({"open": cl, "high": hi, "low": lo, "close": cl, "volume": 1000.0})
    return pd.DataFrame(rows, index=pd.DatetimeIndex(idx))


def test_bearish_judas_london_sweeps_high_then_rejects():
    # Asian range (00:00-07:00) high ~= 105. London window (07:00-10:00) spikes
    # to 108 (sweeps the high) then closes back at 103 (below the range high).
    bars = [
        (1, 105.0, 100.0, 104.0),   # asian range
        (3, 104.0, 101.0, 102.0),
        (6, 103.0, 100.0, 101.0),
        (7.5, 108.0, 104.0, 107.0),  # london: sweep the high
        (9.5, 106.0, 102.0, 103.0),  # ... reject back below 105
    ]
    j = JudasSwingEngine().detect(_day_df(bars), JudasSession.LONDON)
    assert j is not None
    assert j.false_direction == "bullish"
    assert j.bias_direction == "bearish"
    assert j.swept_level == 105.0


def test_bullish_judas_london_sweeps_low_then_rejects():
    bars = [
        (1, 105.0, 100.0, 102.0),   # asian range low ~= 100
        (3, 104.0, 100.5, 103.0),
        (6, 103.0, 100.0, 101.0),
        (7.5, 102.0, 96.0, 98.0),   # london: sweep the low
        (9.5, 104.0, 99.0, 103.0),  # ... reject back above 100
    ]
    j = JudasSwingEngine().detect(_day_df(bars), JudasSession.LONDON)
    assert j is not None
    assert j.false_direction == "bearish"
    assert j.bias_direction == "bullish"


def test_no_judas_when_no_sweep():
    bars = [
        (1, 105.0, 100.0, 104.0),
        (6, 103.0, 100.0, 101.0),
        (7.5, 104.5, 101.0, 103.0),  # stays inside the range
        (9.5, 104.0, 102.0, 103.5),
    ]
    assert JudasSwingEngine().detect(_day_df(bars), JudasSession.LONDON) is None


def test_no_judas_when_sweep_not_rejected():
    # Sweeps the high AND holds above it -> a genuine breakout, not a Judas.
    bars = [
        (1, 105.0, 100.0, 104.0),
        (6, 103.0, 100.0, 101.0),
        (7.5, 108.0, 104.0, 107.0),
        (9.5, 110.0, 106.0, 109.0),  # closes above 105 -> not rejected
    ]
    assert JudasSwingEngine().detect(_day_df(bars), JudasSession.LONDON) is None


def test_empty_df():
    assert JudasSwingEngine().detect(pd.DataFrame(), JudasSession.LONDON) is None
