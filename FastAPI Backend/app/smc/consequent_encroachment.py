"""
Consequent Encroachment (CE) - the 50% of a Fair Value Gap.

ICT teaches that the midpoint of an FVG is the level price delivers to and
reacts from most reliably: a shallow tap of the near edge is weak, a full fill
closes the gap, but the CE (50%) is the institutional "consequent encroachment"
entry. This module derives CE levels from FVGs and tells whether current price
is trading at a CE.

Pure and stateless-per-call. Works with app.smc.fvg.FairValueGap objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

# How close (as a fraction of the gap height) price must be to the midpoint to
# count as "at CE".
DEFAULT_CE_TOLERANCE_FRACTION = 0.10


@dataclass
class ConsequentEncroachment:
    direction: str          # "bullish" | "bearish" (from the source FVG)
    ce_price: float         # 50% of the gap
    fvg_top: float
    fvg_bottom: float
    at_ce: bool             # is the reference price currently at the CE


class ConsequentEncroachmentEngine:
    def __init__(self, tolerance_fraction: float = DEFAULT_CE_TOLERANCE_FRACTION):
        self.tolerance_fraction = tolerance_fraction

    @staticmethod
    def ce_price(fvg) -> float:
        return (float(fvg.top) + float(fvg.bottom)) / 2.0

    def for_fvg(self, fvg, current_price: Optional[float] = None) -> ConsequentEncroachment:
        top, bottom = float(fvg.top), float(fvg.bottom)
        ce = (top + bottom) / 2.0
        height = abs(top - bottom)
        at_ce = (
            current_price is not None
            and height > 0
            and abs(current_price - ce) <= height * self.tolerance_fraction
        )
        direction = getattr(getattr(fvg, "type", None), "value", getattr(fvg, "type", None))
        return ConsequentEncroachment(
            direction=direction, ce_price=ce, fvg_top=top, fvg_bottom=bottom, at_ce=at_ce,
        )

    def analyze(self, fvgs: List, current_price: Optional[float] = None) -> List[ConsequentEncroachment]:
        return [self.for_fvg(f, current_price) for f in (fvgs or []) if not getattr(f, "filled", False)]

    def price_at_any_ce(self, fvgs: List, current_price: float, direction: Optional[str] = None) -> bool:
        for ce in self.analyze(fvgs, current_price):
            if ce.at_ce and (direction is None or ce.direction == direction):
                return True
        return False
