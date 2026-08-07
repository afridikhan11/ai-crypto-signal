"""
Balanced Price Range (BPR) - the overlap of two opposite FVGs.

When a bullish FVG and a bearish FVG print in close succession and their price
ranges overlap, the overlapping band is a Balanced Price Range: an area where
both buy-side and sell-side imbalance were delivered, which then acts as a
high-probability support/resistance and reversal zone. Price returning to a
BPR is a classic ICT entry.

Pure and stateless-per-call. Works with app.smc.fvg.FairValueGap objects
(fields: top, bottom, type.value in {"bullish","bearish"}, filled, timestamp).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import pandas as pd


@dataclass
class BalancedPriceRange:
    top: float
    bottom: float
    bullish_fvg_timestamp: object
    bearish_fvg_timestamp: object
    # Bias when price is interacting: a BPR defended from above is support
    # (bullish); from below, resistance (bearish). None until price is given.
    reaction_bias: Optional[str] = None

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2.0


def _fvg_dir(f) -> Optional[str]:
    return getattr(getattr(f, "type", None), "value", getattr(f, "type", None))


class BalancedPriceRangeEngine:
    def __init__(self, max_bars_apart: Optional[int] = None):
        # Optional cap on how far apart (in candle timestamps) the two FVGs may
        # form; None = no cap.
        self.max_bars_apart = max_bars_apart

    def analyze(self, fvgs: List, current_price: Optional[float] = None) -> List[BalancedPriceRange]:
        if not fvgs:
            return []
        bulls = [f for f in fvgs if not getattr(f, "filled", False) and _fvg_dir(f) == "bullish"]
        bears = [f for f in fvgs if not getattr(f, "filled", False) and _fvg_dir(f) == "bearish"]

        out: List[BalancedPriceRange] = []
        seen = set()
        for b in bulls:
            for s in bears:
                top = min(float(b.top), float(s.top))
                bottom = max(float(b.bottom), float(s.bottom))
                if top <= bottom:
                    continue  # no overlap
                if self.max_bars_apart is not None:
                    try:
                        gap = abs(
                            (pd.Timestamp(b.timestamp) - pd.Timestamp(s.timestamp))
                            / pd.Timedelta(minutes=1)
                        )
                        if gap > self.max_bars_apart:
                            continue
                    except Exception:
                        pass
                key = (round(top, 10), round(bottom, 10))
                if key in seen:
                    continue
                seen.add(key)

                reaction_bias = None
                if current_price is not None:
                    if current_price >= top:
                        reaction_bias = "bearish"   # approaching from above -> resistance
                    elif current_price <= bottom:
                        reaction_bias = "bullish"   # approaching from below -> support
                out.append(BalancedPriceRange(
                    top=top, bottom=bottom,
                    bullish_fvg_timestamp=b.timestamp, bearish_fvg_timestamp=s.timestamp,
                    reaction_bias=reaction_bias,
                ))
        return out
