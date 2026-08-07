"""
New Day / New Week Opening Gaps (NDOG / NWOG).

The gap between one period's closing price and the next period's opening price
is a standing ICT reference. NDOG = previous-day close -> current-day open;
NWOG = previous-week close -> current-week open. Even in 24/7 crypto (where the
numeric gap is small) the opening-price band acts as support/resistance and a
draw-on-liquidity level; its midpoint is watched as consequent encroachment.

Pure and stateless-per-call. UTC day/week boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class OpeningGap:
    kind: str            # "NDOG" | "NWOG"
    top: float
    bottom: float
    prev_close: float
    new_open: float
    filled: bool         # has price traded back through the gap since it formed

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2.0

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top


class OpeningGapEngine:
    @staticmethod
    def _utc_index(df: pd.DataFrame) -> pd.DatetimeIndex:
        idx = pd.DatetimeIndex(df.index)
        return idx.tz_convert("UTC") if idx.tz is not None else idx.tz_localize("UTC")

    def _gap(self, df: pd.DataFrame, boundary: pd.Timestamp, kind: str) -> Optional[OpeningGap]:
        idx = self._utc_index(df)
        before = idx < boundary
        after = idx >= boundary
        if not before.any() or not after.any():
            return None

        prev_close = float(df["close"].to_numpy()[before][-1])
        new_open = float(df["open"].to_numpy()[after][0])
        top = max(prev_close, new_open)
        bottom = min(prev_close, new_open)

        # Filled once any candle at/after the boundary traded back through the
        # gap band (both sides touched) — for a zero-width gap, treat as filled.
        after_df = df[after]
        highs = after_df["high"].to_numpy(dtype=float)
        lows = after_df["low"].to_numpy(dtype=float)
        filled = bool(top == bottom or ((highs >= top).any() and (lows <= bottom).any()))
        return OpeningGap(kind, top, bottom, prev_close, new_open, filled)

    def day_gap(self, df: pd.DataFrame) -> Optional[OpeningGap]:
        if df is None or df.empty:
            return None
        today = self._utc_index(df)[-1].normalize()
        return self._gap(df, today, "NDOG")

    def week_gap(self, df: pd.DataFrame) -> Optional[OpeningGap]:
        if df is None or df.empty:
            return None
        today = self._utc_index(df)[-1].normalize()
        week_start = today - pd.Timedelta(days=int(today.weekday()))
        return self._gap(df, week_start, "NWOG")
