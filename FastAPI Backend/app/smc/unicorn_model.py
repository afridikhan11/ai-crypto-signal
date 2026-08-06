"""
Unicorn Model - a Breaker Block overlapped by a Fair Value Gap.

The Unicorn is one of ICT's highest-probability entries: a breaker block
(a failed order block whose level price reclaimed) that coincides with an FVG
pointing the same way. The overlap of the two is the entry zone - two
independent institutional footprints confirming the same price.

Because a breaker's stored polarity is the pre-flip order-block polarity (see
order_block_engine notes), the Unicorn's directional read is taken from the
unambiguous FVG, and the breaker only needs to be in the BREAKER state and
overlap the gap in price.

Pure and stateless-per-call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

try:  # keep the module importable even if the enum name shifts
    from app.smc.order_block_engine import OBState
    _BREAKER_STATE = OBState.BREAKER
except Exception:  # pragma: no cover
    _BREAKER_STATE = "breaker"


@dataclass
class Unicorn:
    direction: str          # from the FVG: "bullish" | "bearish"
    zone_top: float         # overlap of breaker and FVG
    zone_bottom: float
    breaker_timestamp: object
    fvg_timestamp: object

    def contains(self, price: float) -> bool:
        return self.zone_bottom <= price <= self.zone_top

    @property
    def entry_price(self) -> float:
        return (self.zone_top + self.zone_bottom) / 2.0


def _fvg_dir(f) -> Optional[str]:
    return getattr(getattr(f, "type", None), "value", getattr(f, "type", None))


def _is_breaker(ob) -> bool:
    state = getattr(ob, "state", None)
    state_val = getattr(state, "value", state)
    wanted = getattr(_BREAKER_STATE, "value", _BREAKER_STATE)
    return state_val == wanted


class UnicornModelEngine:
    def analyze(self, order_blocks: List, fvgs: List) -> List[Unicorn]:
        if not order_blocks or not fvgs:
            return []
        breakers = [ob for ob in order_blocks if _is_breaker(ob)]
        active_fvgs = [f for f in fvgs if not getattr(f, "filled", False)]

        out: List[Unicorn] = []
        for br in breakers:
            b_high, b_low = float(br.high), float(br.low)
            for f in active_fvgs:
                top = min(b_high, float(f.top))
                bottom = max(b_low, float(f.bottom))
                if top <= bottom:
                    continue  # no price overlap
                out.append(Unicorn(
                    direction=_fvg_dir(f),
                    zone_top=top,
                    zone_bottom=bottom,
                    breaker_timestamp=getattr(br, "timestamp", None),
                    fvg_timestamp=getattr(f, "timestamp", None),
                ))
        return out
