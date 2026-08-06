"""Optimal Trade Entry (OTE).

ICT's OTE is the 0.62 - 0.79 retracement of an impulse (displacement) leg,
with the 0.705 level ("the sweet spot") as the ideal fill. Entering inside
this window gives a deep discount/premium entry with a tight, structure-based
stop just beyond the leg's origin.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.smc.displacement import DisplacementLeg, DisplacementType

# Standard ICT OTE fib levels measured from the leg extreme back toward origin.
OTE_MIN = 0.62
OTE_SWEET = 0.705
OTE_MAX = 0.79


@dataclass
class OTEZone:
    direction: DisplacementType
    top: float          # upper price bound of the OTE window
    bottom: float       # lower price bound of the OTE window
    sweet_spot: float   # 0.705 retracement — ideal entry
    leg_origin: float
    leg_extreme: float

    def contains(self, price: float, tolerance: float = 0.0) -> bool:
        lo = self.bottom - tolerance
        hi = self.top + tolerance
        return lo <= price <= hi


def _retrace(origin: float, extreme: float, ratio: float) -> float:
    """Price at ``ratio`` retracement from ``extreme`` back toward ``origin``."""
    return extreme - (extreme - origin) * ratio


def compute_ote(leg: DisplacementLeg) -> OTEZone:
    """Build the OTE window for a displacement leg.

    For a bullish leg (origin=low, extreme=high) the retracement pulls price
    *down* into discount; for a bearish leg it pulls price *up* into premium.
    """
    a = _retrace(leg.origin, leg.extreme, OTE_MIN)
    b = _retrace(leg.origin, leg.extreme, OTE_MAX)
    sweet = _retrace(leg.origin, leg.extreme, OTE_SWEET)
    top = max(a, b)
    bottom = min(a, b)
    return OTEZone(
        direction=leg.type,
        top=top,
        bottom=bottom,
        sweet_spot=sweet,
        leg_origin=leg.origin,
        leg_extreme=leg.extreme,
    )
