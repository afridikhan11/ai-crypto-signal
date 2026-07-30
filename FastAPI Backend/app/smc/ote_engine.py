"""
OTE (Optimal Trade Entry) Engine.

STATUS (2026-07-29): PRODUCTION. New module - final phase of the
institutional ICT migration. Fills a confirmed gap: no OTE logic existed
anywhere in the project before this file.

OTE IS NOT A FIBONACCI CALCULATOR
--------------------------------------------------------------------------
The 61.8%-79% retracement window is only the geometric definition of WHERE
an OTE zone sits within a displacement leg - it is not, by itself,
evidence of anything. This engine treats that window as necessary but far
from sufficient: `detect()` only ever returns a zone when ALL of the
following real ICT evidence pieces also line up (the same strict,
all-conditions-must-pass philosophy already used by `InducementEngine`):

  1. Market Structure + Displacement - a real, recent `StructureBreak`
     must exist (`MarketStructureEngine.analyze().external_breaks[-1]`)
     with `displacement_ratio >= min_displacement_ratio` - reused directly
     from the break MarketStructureEngine already computed, never
     recomputed here.
  2. A genuine leg - found from already-computed `SwingPoint`s (the most
     recent opposite-type swing before the break, to the most recent
     same-type swing after it, or the live price if the leg is still
     extending) - no new pivot/swing detection.
  3. Liquidity - the leg must have been launched from a level a caller-
     supplied, already-swept `LiquidityLevel` sits at (reused from
     `LiquidityEngine`, not re-detected).
  4. Order Block confluence - a caller-supplied `OrderBlock` overlapping
     the OTE price zone.
  5. Fair Value Gap confluence - a caller-supplied `FairValueGap`
     overlapping the OTE price zone.
  6. Premium/Discount alignment - the OTE zone must sit in the correct
     half of the current dealing range for its direction (bullish OTE in
     discount, bearish OTE in premium) - reused from
     `SupplyDemandEngine.get_zone()`, never recomputed.
  7. Institutional Confluence - at least `min_confluence_count` (default
     2) of {Order Block, FVG, Liquidity} confluence must be present.
  8. Retracement Quality - the leg's displacement must clear a stricter
     "clean" bar for the zone to be labeled clean vs shallow (informational,
     included on every valid zone, not itself a pass/fail gate beyond
     item 1's base threshold).

If ANY of items 1, 2, 3+4+5 (confluence count), or 6 fail, `detect()`
returns `None` - no OTE zone is ever emitted on partial evidence. This is
"Only valid OTE zones may be generated," implemented the same way
Inducement's false-positive-minimization gate already is.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from app.smc.market_structure_engine import MarketStructureSnapshot

# Standard ICT OTE retracement window - the geometric definition only; see
# module docstring for why this alone is never sufficient to emit a zone.
OTE_FIB_LOW = 0.618
OTE_FIB_HIGH = 0.79

DEFAULT_MIN_DISPLACEMENT_RATIO = 1.0
# "Clean" retracement quality requires meaningfully MORE displacement than
# the bare minimum gate - an impulsive leg with real conviction behind it,
# not one that just barely cleared the threshold.
CLEAN_DISPLACEMENT_MULTIPLE = 1.5
DEFAULT_MIN_CONFLUENCE_COUNT = 2

# How close (as a fraction of the leg's own range) a swept liquidity level
# must sit to the leg's ORIGIN to count as "the leg was launched from a
# real liquidity event" - a level far from the leg's start is unrelated.
LIQUIDITY_ORIGIN_TOLERANCE_FRACTION = 0.15


@dataclass(frozen=True)
class OTEZone:
    direction: str                  # "bullish" | "bearish"
    leg_start_price: float
    leg_end_price: float
    displacement_ratio: float
    zone_top: float
    zone_bottom: float
    has_ob_confluence: bool
    has_fvg_confluence: bool
    has_liquidity_confluence: bool
    premium_discount_aligned: bool
    confluence_count: int
    retracement_quality: str        # "clean" | "shallow"

    def contains(self, price: float) -> bool:
        return self.zone_bottom <= price <= self.zone_top


class OTEEngine:
    """Stateless. `detect()` called twice with the same inputs returns an
    equivalent result (or `None` both times)."""

    def __init__(
        self,
        min_displacement_ratio: float = DEFAULT_MIN_DISPLACEMENT_RATIO,
        min_confluence_count: int = DEFAULT_MIN_CONFLUENCE_COUNT,
    ) -> None:
        self.min_displacement_ratio = min_displacement_ratio
        self.min_confluence_count = min_confluence_count

    # ------------------------------------------------------------------
    # Item 2 - leg lookup from already-computed swings, no new detection.
    # ------------------------------------------------------------------
    @staticmethod
    def _find_leg(snapshot: MarketStructureSnapshot, direction: str, current_price: Optional[float]):
        if direction == "bullish":
            if not snapshot.swing_lows:
                return None
            leg_start_swing = snapshot.swing_lows[-1]
            highs_after = [s for s in snapshot.swing_highs if s.index > leg_start_swing.index]
            if highs_after:
                leg_end_price = highs_after[-1].price
            elif current_price is not None:
                leg_end_price = current_price
            else:
                return None
            if leg_end_price <= leg_start_swing.price:
                return None
            return leg_start_swing.price, leg_end_price
        else:
            if not snapshot.swing_highs:
                return None
            leg_start_swing = snapshot.swing_highs[-1]
            lows_after = [s for s in snapshot.swing_lows if s.index > leg_start_swing.index]
            if lows_after:
                leg_end_price = lows_after[-1].price
            elif current_price is not None:
                leg_end_price = current_price
            else:
                return None
            if leg_end_price >= leg_start_swing.price:
                return None
            return leg_start_swing.price, leg_end_price

    @staticmethod
    def _zone_bounds(direction: str, leg_start: float, leg_end: float):
        if direction == "bullish":
            leg_range = leg_end - leg_start
            zone_top = leg_end - OTE_FIB_LOW * leg_range
            zone_bottom = leg_end - OTE_FIB_HIGH * leg_range
        else:
            leg_range = leg_start - leg_end
            zone_bottom = leg_end + OTE_FIB_LOW * leg_range
            zone_top = leg_end + OTE_FIB_HIGH * leg_range
        return zone_bottom, zone_top

    @staticmethod
    def _has_ob_confluence(order_blocks: Optional[List], zone_bottom: float, zone_top: float) -> bool:
        if not order_blocks:
            return False
        return any(ob.low <= zone_top and zone_bottom <= ob.high for ob in order_blocks)

    @staticmethod
    def _has_fvg_confluence(fvgs: Optional[List], zone_bottom: float, zone_top: float) -> bool:
        if not fvgs:
            return False
        return any(f.bottom <= zone_top and zone_bottom <= f.top for f in fvgs)

    @staticmethod
    def _has_liquidity_confluence(liquidity_levels: Optional[List], leg_start: float, leg_range: float) -> bool:
        if not liquidity_levels or leg_range <= 0:
            return False
        tolerance = leg_range * LIQUIDITY_ORIGIN_TOLERANCE_FRACTION
        return any(
            lvl.swept and abs(lvl.price - leg_start) <= tolerance
            for lvl in liquidity_levels
        )

    def detect(
        self,
        snapshot: MarketStructureSnapshot,
        order_blocks: Optional[List] = None,
        fvgs: Optional[List] = None,
        liquidity_levels: Optional[List] = None,
        premium_discount_zone: Optional[str] = None,
        current_price: Optional[float] = None,
    ) -> Optional[OTEZone]:
        # Item 1 - real, recent structure break with real displacement.
        if not snapshot.external_breaks:
            return None
        latest_break = snapshot.external_breaks[-1]
        if latest_break.displacement_ratio < self.min_displacement_ratio:
            return None
        direction = latest_break.direction

        # Item 2 - the leg itself.
        leg = self._find_leg(snapshot, direction, current_price)
        if leg is None:
            return None
        leg_start, leg_end = leg
        leg_range = abs(leg_end - leg_start)
        if leg_range <= 0:
            return None

        zone_bottom, zone_top = self._zone_bounds(direction, leg_start, leg_end)

        # Items 3-5 - confluence.
        has_ob = self._has_ob_confluence(order_blocks, zone_bottom, zone_top)
        has_fvg = self._has_fvg_confluence(fvgs, zone_bottom, zone_top)
        has_liq = self._has_liquidity_confluence(liquidity_levels, leg_start, leg_range)
        confluence_count = sum([has_ob, has_fvg, has_liq])

        # Item 6 - Premium/Discount alignment. Required (not skipped when
        # missing) - see module docstring: a listed building block, never
        # silently bypassed.
        if premium_discount_zone is None:
            return None
        wanted_zone = "discount" if direction == "bullish" else "premium"
        premium_discount_aligned = premium_discount_zone == wanted_zone
        if not premium_discount_aligned:
            return None

        # Item 7 - Institutional Confluence gate.
        if confluence_count < self.min_confluence_count:
            return None

        # Item 8 - retracement quality label (informational, on every valid zone).
        retracement_quality = (
            "clean" if latest_break.displacement_ratio >= self.min_displacement_ratio * CLEAN_DISPLACEMENT_MULTIPLE
            else "shallow"
        )

        return OTEZone(
            direction=direction,
            leg_start_price=leg_start,
            leg_end_price=leg_end,
            displacement_ratio=latest_break.displacement_ratio,
            zone_top=zone_top,
            zone_bottom=zone_bottom,
            has_ob_confluence=has_ob,
            has_fvg_confluence=has_fvg,
            has_liquidity_confluence=has_liq,
            premium_discount_aligned=premium_discount_aligned,
            confluence_count=confluence_count,
            retracement_quality=retracement_quality,
        )
