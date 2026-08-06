"""
Turtle Soup - a false breakout that reverses (liquidity grab reversal).

Named after fading the classic Turtle 20-day breakout, ICT's Turtle Soup is a
stop raid: price pushes just beyond a prior reference high/low - triggering
breakout orders and running resting liquidity - then snaps back inside. The
failed break is the signal, faded in the opposite direction.

    Bearish Turtle Soup: sweeps the reference HIGH, closes back below it.
    Bullish Turtle Soup: sweeps the reference LOW, closes back above it.

The reference extreme is the highest high / lowest low over a lookback window
ending `confirm` bars ago (so the raid itself is not part of the reference).
Pure and stateless-per-call.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

import pandas as pd


class SoupDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"


@dataclass
class TurtleSoup:
    direction: SoupDirection
    reference_level: float     # the swept prior high/low
    extreme_reached: float     # how far the raid went
    reversal_close: float      # last close (back inside)


class TurtleSoupEngine:
    def __init__(self, lookback: int = 20, confirm: int = 3):
        self.lookback = lookback
        self.confirm = confirm

    def detect(self, df: pd.DataFrame) -> Optional[TurtleSoup]:
        if df is None or len(df) < self.lookback + self.confirm + 1:
            return None

        highs = df["high"].to_numpy(dtype=float)
        lows = df["low"].to_numpy(dtype=float)
        closes = df["close"].to_numpy(dtype=float)

        ref_slice_end = len(df) - self.confirm
        ref_slice_start = ref_slice_end - self.lookback
        ref_high = highs[ref_slice_start:ref_slice_end].max()
        ref_low = lows[ref_slice_start:ref_slice_end].min()

        raid_high = highs[ref_slice_end:].max()
        raid_low = lows[ref_slice_end:].min()
        last_close = float(closes[-1])

        swept_high = raid_high > ref_high and last_close < ref_high
        swept_low = raid_low < ref_low and last_close > ref_low

        # If both fired, take the larger raid (deterministic tie-break).
        if swept_high and swept_low:
            if (raid_high - ref_high) >= (ref_low - raid_low):
                swept_low = False
            else:
                swept_high = False

        if swept_high:
            return TurtleSoup(SoupDirection.BEARISH, float(ref_high), float(raid_high), last_close)
        if swept_low:
            return TurtleSoup(SoupDirection.BULLISH, float(ref_low), float(raid_low), last_close)
        return None
