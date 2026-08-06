"""
Key Levels Engine - ICT Draw on Liquidity reference levels.

Institutional price delivery is engineered toward, and away from, a small set
of standing reference levels that every desk watches:

    PDH / PDL   Previous Day High / Low
    PWH / PWL   Previous Week High / Low
    CDH / CDL   Current Day High / Low (formed so far)
    CWH / CWL   Current Week High / Low (formed so far)
    Asian range high / low  (the accumulation range London typically runs)

These are the *external* liquidity magnets a bias is drawn to. This engine is
pure and stateless-per-call, matching the other `app/smc/` engines: it takes a
UTC-indexed OHLCV DataFrame and returns the levels plus whether each has been
swept (traded through) by the most recent price.

Timezone convention (identical to session_engine): a tz-naive index is treated
as already-UTC; a tz-aware index is converted to UTC. Binance kline open times
are UTC, so day/week boundaries are UTC day/week boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

import pandas as pd

# Asian accumulation window in UTC (00:00 - 06:00) - the range London runs.
ASIAN_START_HOUR = 0
ASIAN_END_HOUR = 6


class LevelKind(str, Enum):
    PDH = "previous_day_high"
    PDL = "previous_day_low"
    PWH = "previous_week_high"
    PWL = "previous_week_low"
    CDH = "current_day_high"
    CDL = "current_day_low"
    CWH = "current_week_high"
    CWL = "current_week_low"
    ASIAN_HIGH = "asian_high"
    ASIAN_LOW = "asian_low"


# Which side the resting liquidity sits on (buy-side above highs, sell-side
# below lows) and therefore how a sweep is defined.
_HIGH_KINDS = {
    LevelKind.PDH, LevelKind.PWH, LevelKind.CDH, LevelKind.CWH, LevelKind.ASIAN_HIGH,
}


@dataclass
class KeyLevel:
    kind: LevelKind
    price: float
    is_buyside: bool          # True = liquidity rests above (a high)
    swept: bool = False       # True once price has traded beyond it


@dataclass
class KeyLevelsSnapshot:
    levels: List[KeyLevel]
    # Nearest unswept draw-on-liquidity level on each side of current price -
    # the natural next target price is being delivered toward.
    nearest_unswept_above: Optional[KeyLevel] = None
    nearest_unswept_below: Optional[KeyLevel] = None

    def by_kind(self, kind: LevelKind) -> Optional[KeyLevel]:
        return next((lv for lv in self.levels if lv.kind == kind), None)

    @property
    def unswept(self) -> List[KeyLevel]:
        return [lv for lv in self.levels if not lv.swept]


class KeyLevelsEngine:
    def __init__(self, asian_start_hour: int = ASIAN_START_HOUR, asian_end_hour: int = ASIAN_END_HOUR):
        self.asian_start_hour = asian_start_hour
        self.asian_end_hour = asian_end_hour

    @staticmethod
    def _utc_index(df: pd.DataFrame) -> pd.DatetimeIndex:
        idx = pd.DatetimeIndex(df.index)
        if idx.tz is not None:
            idx = idx.tz_convert("UTC")
        else:
            idx = idx.tz_localize("UTC")
        return idx

    def analyze(self, df: pd.DataFrame) -> KeyLevelsSnapshot:
        if df is None or df.empty:
            return KeyLevelsSnapshot(levels=[])

        idx = self._utc_index(df)
        highs = df["high"].to_numpy(dtype=float)
        lows = df["low"].to_numpy(dtype=float)
        last_price = float(df["close"].iloc[-1])

        last_ts = idx[-1]
        today = last_ts.normalize()
        yesterday = today - pd.Timedelta(days=1)
        # ISO week start (Monday) in UTC.
        week_start = today - pd.Timedelta(days=int(today.weekday()))
        prev_week_start = week_start - pd.Timedelta(days=7)

        def _hl(mask):
            if not mask.any():
                return None, None
            return float(highs[mask].max()), float(lows[mask].min())

        raw: List[tuple] = []  # (kind, price, is_buyside)

        pdh, pdl = _hl((idx >= yesterday) & (idx < today))
        if pdh is not None:
            raw += [(LevelKind.PDH, pdh, True), (LevelKind.PDL, pdl, False)]

        pwh, pwl = _hl((idx >= prev_week_start) & (idx < week_start))
        if pwh is not None:
            raw += [(LevelKind.PWH, pwh, True), (LevelKind.PWL, pwl, False)]

        cdh, cdl = _hl(idx >= today)
        if cdh is not None:
            raw += [(LevelKind.CDH, cdh, True), (LevelKind.CDL, cdl, False)]

        cwh, cwl = _hl(idx >= week_start)
        if cwh is not None:
            raw += [(LevelKind.CWH, cwh, True), (LevelKind.CWL, cwl, False)]

        ah, al = _hl(
            (idx >= today + pd.Timedelta(hours=self.asian_start_hour))
            & (idx < today + pd.Timedelta(hours=self.asian_end_hour))
        )
        if ah is not None:
            raw += [(LevelKind.ASIAN_HIGH, ah, True), (LevelKind.ASIAN_LOW, al, False)]

        levels: List[KeyLevel] = []
        for kind, price, is_buyside in raw:
            # A buy-side level is swept once price has traded above it; a
            # sell-side level once price has traded below it.
            swept = (last_price > price) if is_buyside else (last_price < price)
            levels.append(KeyLevel(kind=kind, price=price, is_buyside=is_buyside, swept=swept))

        unswept = [lv for lv in levels if not lv.swept]
        above = [lv for lv in unswept if lv.price > last_price]
        below = [lv for lv in unswept if lv.price < last_price]
        return KeyLevelsSnapshot(
            levels=levels,
            nearest_unswept_above=min(above, key=lambda lv: lv.price) if above else None,
            nearest_unswept_below=max(below, key=lambda lv: lv.price) if below else None,
        )
