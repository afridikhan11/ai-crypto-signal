"""
Standard Deviation Projections - ICT price targets.

Once a manipulation/impulse leg is defined, ICT projects standard-deviation
(fibonacci extension) multiples of that leg to set objective targets for where
price is likely to be delivered. Common multiples are 1.0, 1.5, 2.0, 2.5 and
4.0 of the leg, measured from the leg's end in the leg's direction.

Pure and stateless-per-call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

DEFAULT_MULTIPLES = (1.0, 1.5, 2.0, 2.5, 4.0)


@dataclass
class Projection:
    multiple: float
    price: float


class StandardDeviationProjectionEngine:
    def __init__(self, multiples=DEFAULT_MULTIPLES):
        self.multiples = tuple(multiples)

    def project(self, leg_start: float, leg_end: float) -> List[Projection]:
        """Project the leg (leg_start -> leg_end) forward. The sign of
        (leg_end - leg_start) sets the direction, so a bullish leg projects
        upward targets and a bearish leg downward — no direction flag needed."""
        leg = leg_end - leg_start
        if leg == 0:
            return []
        return [Projection(multiple=m, price=leg_end + m * leg) for m in self.multiples]

    def next_target(self, leg_start: float, leg_end: float, current_price: float) -> Optional[Projection]:
        """The nearest projection still ahead of current price in the leg's
        direction (the next objective)."""
        leg = leg_end - leg_start
        if leg == 0:
            return None

        def _is_ahead(p: Projection) -> bool:
            return p.price > current_price if leg > 0 else p.price < current_price

        ahead = [p for p in self.project(leg_start, leg_end) if _is_ahead(p)]
        if not ahead:
            return None
        return min(ahead, key=lambda p: abs(p.price - current_price))
