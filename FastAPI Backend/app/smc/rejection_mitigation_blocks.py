"""
Rejection Blocks and Mitigation Blocks - two order-block cousins.

REJECTION BLOCK: built from the WICK of a candle, not its body. A long lower
wick that rejected lower prices leaves a bullish rejection block (the wick
zone, low -> body); a long upper wick leaves a bearish one. Price returning to
that wick zone tends to reject again. This is what distinguishes it from a
classic (body-based) order block.

MITIGATION BLOCK: the last opposite-colour candle before a displacement whose
leg originated from a HIGHER LOW (bullish) / LOWER HIGH (bearish) - i.e. price
FAILED to take the prior liquidity before reversing. That "failure to continue"
origin is the defining difference from an order block, which forms after a
sweep of liquidity.

Both are pure and stateless-per-call over a UTC-indexed OHLCV DataFrame.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

import pandas as pd


class BlockDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"


@dataclass
class RejectionBlock:
    direction: BlockDirection
    top: float
    bottom: float
    timestamp: object
    wick_ratio: float

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top


@dataclass
class MitigationBlock:
    direction: BlockDirection
    top: float
    bottom: float
    timestamp: object

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top


class RejectionBlockEngine:
    def __init__(self, wick_ratio_threshold: float = 0.5, lookback: int = 50):
        self.wick_ratio_threshold = wick_ratio_threshold
        self.lookback = lookback

    def detect(self, df: pd.DataFrame) -> List[RejectionBlock]:
        if df is None or df.empty:
            return []
        recent = df.iloc[-self.lookback:]
        out: List[RejectionBlock] = []
        for ts, row in recent.iterrows():
            high, low = float(row["high"]), float(row["low"])
            open_, close = float(row["open"]), float(row["close"])
            rng = high - low
            if rng <= 0:
                continue
            body_top = max(open_, close)
            body_bottom = min(open_, close)
            lower_wick = body_bottom - low
            upper_wick = high - body_top
            if lower_wick / rng >= self.wick_ratio_threshold and lower_wick >= upper_wick:
                out.append(RejectionBlock(
                    BlockDirection.BULLISH, top=body_bottom, bottom=low,
                    timestamp=ts, wick_ratio=round(lower_wick / rng, 3),
                ))
            elif upper_wick / rng >= self.wick_ratio_threshold and upper_wick > lower_wick:
                out.append(RejectionBlock(
                    BlockDirection.BEARISH, top=high, bottom=body_top,
                    timestamp=ts, wick_ratio=round(upper_wick / rng, 3),
                ))
        return out

    def latest(self, df: pd.DataFrame, direction: Optional[str] = None) -> Optional[RejectionBlock]:
        blocks = self.detect(df)
        if direction is not None:
            blocks = [b for b in blocks if b.direction.value == direction]
        return blocks[-1] if blocks else None


class MitigationBlockEngine:
    def __init__(self, body_ratio_threshold: float = 0.5, lookback: int = 60):
        self.body_ratio_threshold = body_ratio_threshold
        self.lookback = lookback

    def _is_displacement(self, o, c, h, l, direction: str) -> bool:
        rng = h - l
        if rng <= 0:
            return False
        body = abs(c - o)
        if body / rng < self.body_ratio_threshold:
            return False
        return c > o if direction == "up" else c < o

    def detect(self, df: pd.DataFrame) -> List[MitigationBlock]:
        if df is None or len(df) < 4:
            return []
        window = df.iloc[-self.lookback:]
        o = window["open"].to_numpy(dtype=float)
        c = window["close"].to_numpy(dtype=float)
        h = window["high"].to_numpy(dtype=float)
        l = window["low"].to_numpy(dtype=float)
        idx = window.index
        n = len(window)
        out: List[MitigationBlock] = []

        for i in range(2, n):
            # Bullish MB: strong up candle at i, its origin (i-1) an opposite
            # candle, and the low that preceded the leg is a HIGHER low than an
            # earlier low in the window (failure to take sell-side liquidity).
            if self._is_displacement(o[i], c[i], h[i], l[i], "up") and c[i - 1] < o[i - 1]:
                origin_low = l[i - 1]
                earlier_min_low = l[:i - 1].min() if i - 1 > 0 else origin_low
                if origin_low > earlier_min_low:  # higher low -> failure/mitigation
                    out.append(MitigationBlock(
                        BlockDirection.BULLISH, top=float(h[i - 1]), bottom=float(l[i - 1]),
                        timestamp=idx[i - 1],
                    ))
            elif self._is_displacement(o[i], c[i], h[i], l[i], "down") and c[i - 1] > o[i - 1]:
                origin_high = h[i - 1]
                earlier_max_high = h[:i - 1].max() if i - 1 > 0 else origin_high
                if origin_high < earlier_max_high:  # lower high -> failure/mitigation
                    out.append(MitigationBlock(
                        BlockDirection.BEARISH, top=float(h[i - 1]), bottom=float(l[i - 1]),
                        timestamp=idx[i - 1],
                    ))
        return out

    def latest(self, df: pd.DataFrame, direction: Optional[str] = None) -> Optional[MitigationBlock]:
        blocks = self.detect(df)
        if direction is not None:
            blocks = [b for b in blocks if b.direction.value == direction]
        return blocks[-1] if blocks else None
