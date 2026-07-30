"""
Entry Engine - builds an institutional entry plan from whatever real ICT
zones are available, preferring the most refined anchor.

STATUS (2026-07-29): PRODUCTION. New module - final phase of the
institutional ICT migration. Constructs a plan only; it does not decide
whether the setup is good enough to take - that is the separate
`EntryValidationEngine`'s explicit job (`entry_validation_engine.py`), by
this phase's own design (Entry Engine vs. Entry Validation Engine are two
distinct required components).

ENTRY TYPES SUPPORTED (priority order - most refined first)
--------------------------------------------------------------------------
  1. OTE Entry           - an `OTEZone` matching the trade direction
                            (already fully confluence-gated by
                            `OTEEngine` - see that module).
  2. Order Block Entry    - a real `OrderBlock` matching direction.
  3. FVG Entry             - a real, unfilled `FairValueGap` matching direction.
  4. Supply/Demand Entry   - a real `Zone` (from `SupplyDemandEngine`)
                            matching direction.
  5. Limit Entry           - no zone at all, but a caller-supplied limit
                            price (e.g. a round number or manual level).
  6. Market Entry          - final fallback: enter at the current live
                            price with no zone anchor at all - the
                            WEAKEST entry type, by construction.

Every entry type other than Market reuses an object an existing engine
already produced - no new zone/level detection happens in this file.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


class EntryType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    OTE = "ote"
    ORDER_BLOCK = "order_block"
    FVG = "fvg"
    SUPPLY_DEMAND = "supply_demand"


# Priority order, most refined (tightest, most confluence-backed) first -
# used to pick among multiple simultaneously-available anchors.
_TYPE_PRIORITY = {
    EntryType.OTE: 0,
    EntryType.ORDER_BLOCK: 1,
    EntryType.FVG: 2,
    EntryType.SUPPLY_DEMAND: 3,
    EntryType.LIMIT: 4,
    EntryType.MARKET: 5,
}


@dataclass(frozen=True)
class EntryPlan:
    entry_type: EntryType
    direction: str
    entry_price: float
    reason: str
    anchor_top: Optional[float] = None
    anchor_bottom: Optional[float] = None
    confluence_count: int = 0


class EntryEngine:
    """Stateless. `build()` called twice with the same inputs returns an
    equivalent `EntryPlan`."""

    def build(
        self,
        direction: str,
        current_price: Optional[float],
        ote_zone: Optional[object] = None,
        order_block: Optional[object] = None,
        fvg: Optional[object] = None,
        supply_demand_zone: Optional[object] = None,
        limit_price: Optional[float] = None,
    ) -> Optional[EntryPlan]:
        candidates: List[EntryPlan] = []

        if ote_zone is not None and getattr(ote_zone, "direction", None) == self._ote_direction(direction):
            candidates.append(EntryPlan(
                entry_type=EntryType.OTE, direction=direction,
                entry_price=(ote_zone.zone_top + ote_zone.zone_bottom) / 2,
                anchor_top=ote_zone.zone_top, anchor_bottom=ote_zone.zone_bottom,
                confluence_count=ote_zone.confluence_count,
                reason=f"OTE zone ({ote_zone.retracement_quality}, {ote_zone.confluence_count} confluences)",
            ))

        ob_wanted_type = "bullish_ob" if direction == "LONG" else "bearish_ob"
        if order_block is not None and getattr(getattr(order_block, "type", None), "value", None) == ob_wanted_type:
            candidates.append(EntryPlan(
                entry_type=EntryType.ORDER_BLOCK, direction=direction,
                entry_price=(order_block.high + order_block.low) / 2,
                anchor_top=order_block.high, anchor_bottom=order_block.low,
                confluence_count=1,
                reason=f"Order Block entry ({order_block.strength.value})",
            ))

        fvg_wanted_type = "bullish" if direction == "LONG" else "bearish"
        if fvg is not None and getattr(getattr(fvg, "type", None), "value", None) == fvg_wanted_type and not getattr(fvg, "filled", True):
            candidates.append(EntryPlan(
                entry_type=EntryType.FVG, direction=direction,
                entry_price=(fvg.top + fvg.bottom) / 2,
                anchor_top=fvg.top, anchor_bottom=fvg.bottom,
                confluence_count=1,
                reason="Fair Value Gap entry",
            ))

        zone_wanted_type = "demand" if direction == "LONG" else "supply"
        if supply_demand_zone is not None and getattr(getattr(supply_demand_zone, "type", None), "value", None) == zone_wanted_type:
            candidates.append(EntryPlan(
                entry_type=EntryType.SUPPLY_DEMAND, direction=direction,
                entry_price=(supply_demand_zone.top + supply_demand_zone.bottom) / 2,
                anchor_top=supply_demand_zone.top, anchor_bottom=supply_demand_zone.bottom,
                confluence_count=1,
                reason=f"Supply/Demand zone entry ({supply_demand_zone.strength.value})",
            ))

        if candidates:
            candidates.sort(key=lambda p: (_TYPE_PRIORITY[p.entry_type], -p.confluence_count))
            return candidates[0]

        if limit_price is not None:
            return EntryPlan(
                entry_type=EntryType.LIMIT, direction=direction, entry_price=limit_price,
                confluence_count=0, reason="Limit entry (no ICT zone anchor available)",
            )

        if current_price is not None:
            return EntryPlan(
                entry_type=EntryType.MARKET, direction=direction, entry_price=current_price,
                confluence_count=0, reason="Market entry (no ICT zone anchor available)",
            )

        return None  # no anchor and no price at all - nothing can be built

    @staticmethod
    def _ote_direction(direction: str) -> str:
        return "bullish" if direction == "LONG" else "bearish"
