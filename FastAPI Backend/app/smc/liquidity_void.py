"""
Liquidity Void (Vacuum) - a large, thin, one-directional displacement.

When price is delivered in a fast, near-vertical move it leaves a "void": a
band that traded with almost no two-sided auction. ICT expects price to
retrace back through a void efficiently (it is inefficient/unbalanced), so an
unfilled void is both a magnet and a support/resistance band.

A void here is a candle whose range is a large multiple of the local ATR and
whose body dominates its range (a true displacement, not a wide indecision
candle). Pure and stateless-per-call.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

import numpy as np
import pandas as pd


class VoidDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"


@dataclass
class LiquidityVoid:
    direction: VoidDirection
    top: float
    bottom: float
    timestamp: object
    atr_multiple: float
    filled: bool

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top


class LiquidityVoidEngine:
    def __init__(self, atr_period: int = 14, void_atr_mult: float = 2.5,
                 body_ratio_threshold: float = 0.6, lookback: int = 60):
        self.atr_period = atr_period
        self.void_atr_mult = void_atr_mult
        self.body_ratio_threshold = body_ratio_threshold
        self.lookback = lookback

    @staticmethod
    def _atr(high, low, close, period) -> np.ndarray:
        prev_close = np.concatenate([[close[0]], close[:-1]])
        tr = np.maximum.reduce([
            high - low,
            np.abs(high - prev_close),
            np.abs(low - prev_close),
        ])
        atr = pd.Series(tr).rolling(period, min_periods=1).mean().to_numpy()
        return atr

    def detect(self, df: pd.DataFrame) -> List[LiquidityVoid]:
        if df is None or len(df) < 2:
            return []
        high = df["high"].to_numpy(dtype=float)
        low = df["low"].to_numpy(dtype=float)
        close = df["close"].to_numpy(dtype=float)
        open_ = df["open"].to_numpy(dtype=float)
        idxs = df.index
        atr = self._atr(high, low, close, self.atr_period)
        n = len(df)

        out: List[LiquidityVoid] = []
        start = max(1, n - self.lookback)
        for i in range(start, n):
            rng = high[i] - low[i]
            if rng <= 0 or atr[i] <= 0:
                continue
            mult = rng / atr[i]
            if mult < self.void_atr_mult:
                continue
            body = abs(close[i] - open_[i])
            if body / rng < self.body_ratio_threshold:
                continue
            direction = VoidDirection.BULLISH if close[i] > open_[i] else VoidDirection.BEARISH
            # Filled if any LATER candle traded back into the void band.
            if i + 1 < n:
                later_hi = high[i + 1:]
                later_lo = low[i + 1:]
                filled = bool(((later_hi >= low[i]) & (later_lo <= high[i])).any())
            else:
                filled = False
            out.append(LiquidityVoid(
                direction=direction, top=float(high[i]), bottom=float(low[i]),
                timestamp=idxs[i], atr_multiple=round(float(mult), 2), filled=filled,
            ))
        return out

    def unfilled(self, df: pd.DataFrame) -> List[LiquidityVoid]:
        return [v for v in self.detect(df) if not v.filled]
