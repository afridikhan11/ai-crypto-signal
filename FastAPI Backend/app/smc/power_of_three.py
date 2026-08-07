"""
Power of 3 (AMD) - Accumulation, Manipulation, Distribution.

ICT models the daily (or session) candle as three phases:

    Accumulation  - price consolidates near the period open (orders built).
    Manipulation  - a false push (the Judas) sweeps one side of that range.
    Distribution  - the real, opposite move delivers the day's range.

The manipulation runs liquidity AGAINST the true direction, so the side swept
FIRST tells you the distribution bias: lows taken first -> bullish day; highs
taken first -> bearish day.

This engine classifies the CURRENT period (default: the UTC day) from an
intraday OHLCV frame. Pure and stateless-per-call.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd


class AMDPhase(str, Enum):
    ACCUMULATION = "accumulation"
    MANIPULATION = "manipulation"
    DISTRIBUTION = "distribution"


@dataclass
class PowerOfThree:
    period_open: float
    phase: AMDPhase
    manipulation_direction: Optional[str]   # side swept first ("up"/"down"), or None
    distribution_bias: Optional[str]         # "bullish" | "bearish" | None
    period_high: float
    period_low: float


class PowerOfThreeEngine:
    def __init__(self, accumulation_range_fraction: float = 0.25):
        # If the period's range so far is within this fraction of the open,
        # it's still accumulating.
        self.accumulation_range_fraction = accumulation_range_fraction

    @staticmethod
    def _utc_index(df: pd.DataFrame) -> pd.DatetimeIndex:
        idx = pd.DatetimeIndex(df.index)
        return idx.tz_convert("UTC") if idx.tz is not None else idx.tz_localize("UTC")

    def analyze(self, df: pd.DataFrame) -> Optional[PowerOfThree]:
        if df is None or df.empty:
            return None
        idx = self._utc_index(df)
        today = idx[-1].normalize()
        mask = idx >= today
        if not mask.any():
            return None

        day = df[mask.to_numpy() if hasattr(mask, "to_numpy") else mask]
        highs = day["high"].to_numpy(dtype=float)
        lows = day["low"].to_numpy(dtype=float)
        period_open = float(day["open"].iloc[0])
        period_high = float(highs.max())
        period_low = float(lows.min())
        last_close = float(day["close"].iloc[-1])

        # Accumulation: range still tight around the open.
        open_ref = abs(period_open) if period_open != 0 else 1.0
        range_frac = (period_high - period_low) / open_ref
        if range_frac < self.accumulation_range_fraction / 100.0:
            return PowerOfThree(period_open, AMDPhase.ACCUMULATION, None, None,
                                period_high, period_low)

        # Which side was swept first?
        idx_high = int(np.argmax(highs))
        idx_low = int(np.argmin(lows))
        if idx_low < idx_high:
            manipulation_direction = "down"    # lows taken first
            distribution_bias = "bullish"
        elif idx_high < idx_low:
            manipulation_direction = "up"
            distribution_bias = "bearish"
        else:
            manipulation_direction = None
            distribution_bias = None

        # Distribution once price has reclaimed the open in the true direction;
        # otherwise still manipulating.
        phase = AMDPhase.MANIPULATION
        if distribution_bias == "bullish" and last_close > period_open:
            phase = AMDPhase.DISTRIBUTION
        elif distribution_bias == "bearish" and last_close < period_open:
            phase = AMDPhase.DISTRIBUTION

        return PowerOfThree(period_open, phase, manipulation_direction,
                            distribution_bias, period_high, period_low)
