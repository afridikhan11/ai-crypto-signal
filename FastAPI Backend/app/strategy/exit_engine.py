"""
Exit Engine - builds a structured, multi-level exit plan from real
liquidity and structure targets.

STATUS (2026-07-29): PRODUCTION. New module - final phase of the
institutional ICT migration. Consolidates what `signal_generator.py`
already did inline for take-profit selection (nearest unswept liquidity
level, ATR fallback) into a reusable, testable engine, and adds the
capabilities that were previously entirely missing: Partial Exit, Break
Even, and Trailing Structure.

SUPPORTED (per this phase's explicit requirement list)
--------------------------------------------------------------------------
  - Liquidity Targets    - nearest real, unswept `LiquidityLevel` beyond
                            entry in the favorable direction (reused,
                            same selection rule `signal_generator.py`
                            already used).
  - Structure Targets     - nearest real opposite-type `SwingPoint` beyond
                            entry (a genuine structural level, independent
                            of whether a liquidity pool sits there too).
  - Dynamic TP            - the primary target is simply whichever of the
                            two above is nearer; calling `build()` again
                            later with fresh levels naturally produces an
                            updated target - "dynamic" by construction,
                            not a separate mechanism.
  - Partial Exit           - intermediate levels at configurable fractions
                            of the distance to the primary target.
  - Break Even             - the price at which realized progress equals
                            the original risk (1R) - the standard,
                            disclosed break-even trigger.
  - Trailing Structure     - flagged enabled whenever real structure
                            exists to trail against; the actual stop
                            recalculation happens in
                            `trade_management_engine.py` (a live,
                            continuously-updated decision, not a one-time
                            plan field).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

DEFAULT_PARTIAL_FRACTIONS: Sequence[float] = (0.5,)  # take 50% at the halfway point to the primary target, by default


@dataclass(frozen=True)
class ExitLevel:
    kind: str          # "liquidity_target" | "structure_target" | "partial" | "breakeven"
    price: float
    size_fraction: float   # fraction of the position this level applies to (1.0 = full close)
    reason: str


@dataclass(frozen=True)
class ExitPlan:
    direction: str
    primary_target: Optional[ExitLevel]
    partial_targets: List[ExitLevel] = field(default_factory=list)
    breakeven_trigger: Optional[ExitLevel] = None
    trailing_structure_enabled: bool = False


class ExitEngine:
    """Stateless. `build()` called twice with the same inputs returns an
    equivalent `ExitPlan`."""

    def __init__(self, partial_fractions: Sequence[float] = DEFAULT_PARTIAL_FRACTIONS) -> None:
        self.partial_fractions = tuple(partial_fractions)

    def build(
        self,
        direction: str,
        entry_price: float,
        stop_loss: float,
        liquidity_levels: Optional[List] = None,
        structure_targets: Optional[List] = None,
        min_target_distance: float = 0.0,
        atr_fallback: Optional[float] = None,
    ) -> ExitPlan:
        """`structure_targets` must already be the CORRECT side's swing
        points for this direction (e.g. `MarketStructureSnapshot.swing_highs`
        for a LONG, `.swing_lows` for a SHORT) - this engine does not
        re-classify swing types itself, it only picks the nearest one
        beyond entry, matching the "reuse, never duplicate detection"
        pattern used throughout this migration."""
        is_long = direction == "LONG"

        liquidity_target = self._nearest_liquidity_target(liquidity_levels, entry_price, is_long, min_target_distance)
        structure_target = self._nearest_structure_target(structure_targets, entry_price, is_long, min_target_distance)

        candidates = [t for t in (liquidity_target, structure_target) if t is not None]
        if candidates:
            primary = min(
                candidates,
                key=lambda t: abs(t.price - entry_price),
            )
        elif atr_fallback is not None:
            fallback_price = entry_price + atr_fallback if is_long else entry_price - atr_fallback
            primary = ExitLevel(kind="structure_target", price=fallback_price, size_fraction=1.0, reason="ATR fallback (no real liquidity/structure target available)")
        else:
            primary = None

        partial_targets: List[ExitLevel] = []
        if primary is not None:
            distance = primary.price - entry_price
            for fraction in self.partial_fractions:
                partial_price = entry_price + distance * fraction
                partial_targets.append(ExitLevel(
                    kind="partial", price=partial_price, size_fraction=fraction,
                    reason=f"Partial exit at {int(fraction * 100)}% of the distance to the primary target",
                ))

        risk = abs(entry_price - stop_loss)
        breakeven = None
        if risk > 0:
            breakeven_price = entry_price + risk if is_long else entry_price - risk
            breakeven = ExitLevel(kind="breakeven", price=breakeven_price, size_fraction=0.0, reason="1R reached - move stop to entry")

        trailing_enabled = bool(structure_targets)

        return ExitPlan(
            direction=direction, primary_target=primary, partial_targets=partial_targets,
            breakeven_trigger=breakeven, trailing_structure_enabled=trailing_enabled,
        )

    @staticmethod
    def _nearest_liquidity_target(levels: Optional[List], entry_price: float, is_long: bool, min_distance: float) -> Optional[ExitLevel]:
        if not levels:
            return None
        from app.smc.liquidity_engine import LiquidityType
        wanted_type = LiquidityType.BUYSIDE if is_long else LiquidityType.SELLSIDE
        candidates = [
            lvl for lvl in levels
            if lvl.type == wanted_type and not lvl.swept
            and ((lvl.price > entry_price + min_distance) if is_long else (lvl.price < entry_price - min_distance))
        ]
        if not candidates:
            return None
        nearest = min(candidates, key=lambda lvl: abs(lvl.price - entry_price))
        return ExitLevel(kind="liquidity_target", price=nearest.price, size_fraction=1.0, reason=f"Nearest unswept {wanted_type.value} liquidity")

    @staticmethod
    def _nearest_structure_target(swing_points: Optional[List], entry_price: float, is_long: bool, min_distance: float) -> Optional[ExitLevel]:
        if not swing_points:
            return None
        candidates = [
            sw for sw in swing_points
            if ((sw.price > entry_price + min_distance) if is_long else (sw.price < entry_price - min_distance))
        ]
        if not candidates:
            return None
        nearest = min(candidates, key=lambda sw: abs(sw.price - entry_price))
        return ExitLevel(kind="structure_target", price=nearest.price, size_fraction=1.0, reason="Nearest opposite-side structural swing point")
