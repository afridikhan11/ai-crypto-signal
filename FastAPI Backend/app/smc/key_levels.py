"""Key liquidity levels: Previous Day High/Low and the Asian range.

These are the classic ICT *draw on liquidity* reference points. Price is
engineered toward them (to grab resting orders) and away from them after a
sweep. London typically runs the Asian range; New York runs the London /
previous-day levels.

All calculations assume a UTC ``DatetimeIndex`` (as produced by
``BinanceDataManager.get_dataframe``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class KeyLevels:
    prev_day_high: Optional[float] = None
    prev_day_low: Optional[float] = None
    curr_day_high: Optional[float] = None
    curr_day_low: Optional[float] = None
    asian_high: Optional[float] = None
    asian_low: Optional[float] = None


# Asian accumulation window in UTC (00:00 - 06:00).
ASIAN_START_HOUR = 0
ASIAN_END_HOUR = 6


def compute_key_levels(df: pd.DataFrame) -> KeyLevels:
    """Derive previous-day and Asian-range levels from an intraday OHLC frame."""
    if df.empty or not isinstance(df.index, pd.DatetimeIndex):
        return KeyLevels()

    idx = df.index
    last_ts = idx[-1]
    today = last_ts.normalize()
    yesterday = today - pd.Timedelta(days=1)

    levels = KeyLevels()

    prev_mask = (idx >= yesterday) & (idx < today)
    if prev_mask.any():
        prev = df.loc[prev_mask]
        levels.prev_day_high = float(prev["high"].max())
        levels.prev_day_low = float(prev["low"].min())

    curr_mask = idx >= today
    if curr_mask.any():
        curr = df.loc[curr_mask]
        levels.curr_day_high = float(curr["high"].max())
        levels.curr_day_low = float(curr["low"].min())

    asian_start = today + pd.Timedelta(hours=ASIAN_START_HOUR)
    asian_end = today + pd.Timedelta(hours=ASIAN_END_HOUR)
    asian_mask = (idx >= asian_start) & (idx < asian_end)
    if asian_mask.any():
        asian = df.loc[asian_mask]
        levels.asian_high = float(asian["high"].max())
        levels.asian_low = float(asian["low"].min())

    return levels
