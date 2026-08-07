"""
IPDA Data Ranges - the Interbank Price Delivery Algorithm lookbacks.

ICT teaches that the algorithm re-references price over the last 20, 40 and 60
trading days. Each lookback defines a dealing range (highest high / lowest low)
whose 50% splits premium (sell) from discount (buy). Reading current price
against all three tells you where institutional delivery sits on the short,
medium and longer horizon at once.

This engine is timeframe-agnostic: pass daily candles for the classic 20/40/60
*day* ranges, or intraday candles for an intraday equivalent (the lookbacks are
in BARS). Pure and stateless-per-call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import pandas as pd

DEFAULT_LOOKBACKS = (20, 40, 60)

# Standard ICT premium/discount split with an equilibrium band around 50%.
DISCOUNT_MAX = 0.5
PREMIUM_MIN = 0.5


@dataclass
class IPDARange:
    lookback: int
    high: float
    low: float
    position: float          # 0.0 (at low) .. 1.0 (at high)
    zone: str                # "premium" | "discount" | "equilibrium"

    @property
    def equilibrium(self) -> float:
        return (self.high + self.low) / 2.0


class IPDAEngine:
    def __init__(self, lookbacks=DEFAULT_LOOKBACKS, equilibrium_band: float = 0.02):
        self.lookbacks = tuple(lookbacks)
        self.equilibrium_band = equilibrium_band

    def _zone(self, position: float) -> str:
        if abs(position - 0.5) <= self.equilibrium_band:
            return "equilibrium"
        return "premium" if position > 0.5 else "discount"

    def analyze(self, df: pd.DataFrame) -> List[IPDARange]:
        if df is None or df.empty:
            return []
        highs = df["high"].to_numpy(dtype=float)
        lows = df["low"].to_numpy(dtype=float)
        price = float(df["close"].iloc[-1])
        n = len(df)

        out: List[IPDARange] = []
        for lb in self.lookbacks:
            take = min(lb, n)
            hi = float(highs[-take:].max())
            lo = float(lows[-take:].min())
            rng = hi - lo
            position = (price - lo) / rng if rng > 0 else 0.5
            position = max(0.0, min(1.0, position))
            out.append(IPDARange(lookback=lb, high=hi, low=lo,
                                  position=round(position, 4), zone=self._zone(position)))
        return out

    def consensus_zone(self, df: pd.DataFrame) -> Optional[str]:
        """The zone agreed by a majority of the lookbacks (the dominant
        premium/discount read), or None if there's no data."""
        ranges = self.analyze(df)
        if not ranges:
            return None
        votes = {}
        for r in ranges:
            votes[r.zone] = votes.get(r.zone, 0) + 1
        return max(votes, key=votes.get)
