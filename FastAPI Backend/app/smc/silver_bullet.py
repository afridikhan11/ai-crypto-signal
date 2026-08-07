"""
Silver Bullet - ICT's three fixed one-hour New York windows.

The Silver Bullet is a time-based setup: inside a specific one-hour window,
after price has drawn on liquidity, an FVG forms in the direction of the
higher-timeframe bias and is taken as the entry. There are three windows,
defined in New York local time:

    London SB   03:00 - 04:00 NY
    AM SB       10:00 - 11:00 NY
    PM SB       14:00 - 15:00 NY

Following this project's fixed-UTC convention (session_engine treats candle
timestamps as UTC and does not model DST), the windows are expressed in UTC
using EST (UTC-5):

    London SB   08:00 - 09:00 UTC
    AM SB       15:00 - 16:00 UTC
    PM SB       19:00 - 20:00 UTC

Pure and stateless-per-call, like the other `app/smc/` engines.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

import pandas as pd


class SilverBulletWindowName(str, Enum):
    LONDON = "london_silver_bullet"
    AM = "am_silver_bullet"
    PM = "pm_silver_bullet"


@dataclass(frozen=True)
class SilverBulletWindow:
    name: SilverBulletWindowName
    start_hour: float   # inclusive, UTC fractional hour
    end_hour: float     # exclusive, UTC fractional hour

    def contains(self, utc_hour: float) -> bool:
        return self.start_hour <= utc_hour < self.end_hour


DEFAULT_WINDOWS: List[SilverBulletWindow] = [
    SilverBulletWindow(SilverBulletWindowName.LONDON, 8.0, 9.0),
    SilverBulletWindow(SilverBulletWindowName.AM, 15.0, 16.0),
    SilverBulletWindow(SilverBulletWindowName.PM, 19.0, 20.0),
]


@dataclass
class SilverBulletSetup:
    window: SilverBulletWindowName
    direction: str                 # "bullish" | "bearish"
    fvg_top: float
    fvg_bottom: float
    entry_price: float             # consequent encroachment (50% of the FVG)


class SilverBulletEngine:
    def __init__(self, windows: Optional[List[SilverBulletWindow]] = None):
        self.windows = list(windows) if windows is not None else list(DEFAULT_WINDOWS)

    @staticmethod
    def _utc_hour(ts: pd.Timestamp) -> float:
        ts = pd.Timestamp(ts)
        if ts.tzinfo is not None:
            ts = ts.tz_convert("UTC")
        return ts.hour + ts.minute / 60.0 + ts.second / 3600.0

    def active_window(self, timestamp: pd.Timestamp) -> Optional[SilverBulletWindow]:
        """Return the Silver Bullet window active at `timestamp`, or None."""
        hour = self._utc_hour(timestamp)
        return next((w for w in self.windows if w.contains(hour)), None)

    def detect(
        self,
        df: pd.DataFrame,
        direction: str,
        fvgs: Optional[List] = None,
    ) -> Optional[SilverBulletSetup]:
        """A valid Silver Bullet setup requires:

        * the most recent candle to be inside a Silver Bullet window, and
        * an unfilled FVG, aligned with `direction`, that *formed inside*
          that same window (the institutional entry gap).

        `fvgs` are FairValueGap objects (see app/smc/fvg.py); each carries a
        `timestamp`, `top`, `bottom`, `type.value` ("bullish"/"bearish") and
        `filled`. Returns the setup with a consequent-encroachment (50%) entry,
        or None.
        """
        if df is None or df.empty or not fvgs:
            return None

        window = self.active_window(df.index[-1])
        if window is None:
            return None

        for f in fvgs:
            if getattr(f, "filled", False):
                continue
            f_dir = getattr(getattr(f, "type", None), "value", getattr(f, "type", None))
            if f_dir != direction:
                continue
            if not window.contains(self._utc_hour(f.timestamp)):
                continue
            top, bottom = float(f.top), float(f.bottom)
            return SilverBulletSetup(
                window=window.name,
                direction=direction,
                fvg_top=top,
                fvg_bottom=bottom,
                entry_price=(top + bottom) / 2.0,   # consequent encroachment
            )
        return None
