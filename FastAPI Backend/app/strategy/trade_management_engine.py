"""
Trade Management Engine - a stateless decision function for an already-open
trade: given its entry/stop and a fresh market read, what should happen to
it right now.

STATUS (2026-07-29): PRODUCTION. New module - final phase of the
institutional ICT migration. Deliberately a pure, computed-on-demand
recommendation engine, not a persistence layer: the Database schema is
explicitly frozen for this phase, so this engine returns
`ManagementAction`s (e.g. "move stop to breakeven", "take 50% here") for
a caller to act on - it never writes to `Signal` or any other table
itself. Wiring these recommendations into `SignalMonitor`'s actual
persisted state (new columns for partial-close tracking, a live trailing
stop, etc.) is explicitly a follow-up integration item once schema changes
are separately approved - see this phase's final report.

SUPPORTED (per this phase's explicit requirement list)
--------------------------------------------------------------------------
  - Open Trade Monitoring - `evaluate()` is the entry point; called
                            repeatedly against a live/backtested trade.
  - Partial Close          - recommended when price reaches one of the
                            caller-supplied `ExitPlan.partial_targets`.
  - Risk Reduction          - recommended (as a `REDUCE_RISK` action)
                            whenever a Structure Failure or Liquidity
                            Failure signal fires but the position is not
                            yet clearly invalidated outright.
  - Structure Failure       - a real, caller-supplied opposite-direction
                            `StructureBreak` appearing while the trade is open.
  - Liquidity Failure       - price has already traded through the stop
                            level (the trade's own invalidation).
  - Session Changes         - moving into an off-session window (from
                            `SessionEngine`) is surfaced as an explicit note.
  - Dynamic Stop            - `TRAIL_STOP` recommends moving the stop to
                            the newest real protected structural level
                            once a same-direction break has occurred -
                            "dynamic" because each call re-evaluates
                            against whatever structure_breaks are current.

Every signal this engine reasons about is either caller-supplied real
market data (current price, fresh `StructureBreak`s, a `SessionContext`)
or an already-built `ExitPlan` - no new detection happens here.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


class ManagementActionType(str, Enum):
    HOLD = "hold"
    PARTIAL_CLOSE = "partial_close"
    MOVE_TO_BREAKEVEN = "move_to_breakeven"
    TRAIL_STOP = "trail_stop"
    REDUCE_RISK = "reduce_risk"
    CLOSE_STRUCTURE_FAILURE = "close_structure_failure"
    CLOSE_LIQUIDITY_FAILURE = "close_liquidity_failure"
    SESSION_CHANGE_NOTE = "session_change_note"


@dataclass(frozen=True)
class ManagementAction:
    action_type: ManagementActionType
    reason: str
    new_stop_loss: Optional[float] = None
    close_fraction: Optional[float] = None


class TradeManagementEngine:
    """Stateless. `evaluate()` called twice with the same inputs returns
    an equivalent list of actions."""

    def evaluate(
        self,
        direction: str,
        entry_price: float,
        stop_loss: float,
        current_price: float,
        structure_breaks: Optional[List] = None,
        exit_plan: Optional[object] = None,
        session_context: Optional[object] = None,
    ) -> List[ManagementAction]:
        actions: List[ManagementAction] = []
        is_long = direction == "LONG"
        risk = abs(entry_price - stop_loss)

        # Liquidity Failure - price has already traded through the stop.
        stopped_out = (current_price <= stop_loss) if is_long else (current_price >= stop_loss)
        if stopped_out:
            actions.append(ManagementAction(
                ManagementActionType.CLOSE_LIQUIDITY_FAILURE,
                reason="Price has traded through the stop level - the original invalidation has occurred.",
            ))
            return actions  # nothing else is actionable once the trade's own invalidation has hit

        if risk > 0:
            favorable = (current_price - entry_price) if is_long else (entry_price - current_price)
            r_multiple = favorable / risk
            if r_multiple >= 1.0:
                actions.append(ManagementAction(
                    ManagementActionType.MOVE_TO_BREAKEVEN,
                    reason=f"Price reached {r_multiple:.2f}R - move stop to breakeven.",
                    new_stop_loss=entry_price,
                ))

        # Partial Exit - any ExitPlan.partial_targets already reached.
        if exit_plan is not None and getattr(exit_plan, "partial_targets", None):
            for level in exit_plan.partial_targets:
                reached = (current_price >= level.price) if is_long else (current_price <= level.price)
                if reached:
                    actions.append(ManagementAction(
                        ManagementActionType.PARTIAL_CLOSE,
                        reason=level.reason, close_fraction=level.size_fraction,
                    ))

        structure_failure = False
        if structure_breaks:
            opposite_direction = "bearish" if is_long else "bullish"
            same_direction = "bullish" if is_long else "bearish"

            opposing = [b for b in structure_breaks if b.direction == opposite_direction]
            if opposing:
                structure_failure = True
                latest_opposing = opposing[-1]
                actions.append(ManagementAction(
                    ManagementActionType.CLOSE_STRUCTURE_FAILURE,
                    reason=f"Opposing {latest_opposing.type} detected at {latest_opposing.level} - structure has failed.",
                ))

            same_dir_breaks = [b for b in structure_breaks if b.direction == same_direction]
            if same_dir_breaks and not structure_failure:
                latest_same = same_dir_breaks[-1]
                new_stop = latest_same.broken_swing.price
                # Only ever trail the stop TOWARD entry/profit, never
                # backward against the position, regardless of what data
                # is supplied - a real safety invariant, not just a
                # convenience.
                improves = (new_stop > stop_loss) if is_long else (new_stop < stop_loss)
                if improves:
                    actions.append(ManagementAction(
                        ManagementActionType.TRAIL_STOP,
                        reason=f"New {latest_same.type} confirms continuation - trail stop to newest protected structure.",
                        new_stop_loss=new_stop,
                    ))

        if structure_failure:
            actions.append(ManagementAction(
                ManagementActionType.REDUCE_RISK,
                reason="Structure failure detected but price has not yet hit the stop - consider reducing size.",
            ))

        if session_context is not None and getattr(session_context, "is_off_session", False):
            actions.append(ManagementAction(
                ManagementActionType.SESSION_CHANGE_NOTE,
                reason="Now in an off-session, low-liquidity window - reduced conviction until a major session resumes.",
            ))

        if not actions:
            actions.append(ManagementAction(ManagementActionType.HOLD, reason="No management action triggered."))

        return actions
