"""
Inducement Engine - ICT-standard Inducement detection.

STATUS (2026-07-29): PRODUCTION. New module - Phase C of the institutional
ICT migration (see ICT_ARCHITECTURE_AUDIT_AND_MIGRATION_PLAN.md). Fills a
confirmed gap: the audit found no Inducement detection anywhere in the
project. This engine is a pure, additive INTERPRETATION LAYER on top of
`LiquidityEngine`'s and `MarketStructureEngine`'s already-computed,
already-tested outputs - it does not re-detect sweeps, does not
re-implement BOS/CHoCH scanning, and does not modify either engine. Both
engines' full regression suites (20 + 17 tests) continue to pass unchanged
against their real, untouched production files - verified as part of this
phase (see the accompanying implementation report).

WHAT ICT INDUCEMENT ACTUALLY IS
--------------------------------------------------------------------------
Inducement is a DELIBERATE, engineered liquidity trap: smart money builds
up an obvious pool of resting orders (equal highs/lows retail traders can
see and place stops/entries around), sweeps it to trigger those orders,
and then makes the REAL move in the opposite direction - one that goes on
to produce a genuine structural break and continues toward its objective.
Not every liquidity sweep is Inducement. A sweep that turns into a real
breakout/continuation is not a trap at all. A sweep of a single, lonely
swing point (no real "pool" of orders) is thin evidence at best. A sweep
that reverses but never produces a confirmed structural break, or reverses
and immediately gets round-tripped back through the swept level, is not
the high-conviction "the market needed that liquidity to fund its real
move" signature ICT teaching describes. This engine's `detect()` therefore
requires ALL of the following to hold before it will ever emit an
`InducementEvent` - by design, to keep false positives low, per this
phase's explicit mandate:

  1. Liquidity build-up / Equal Highs or Equal Lows
     -> `LiquidityLevel.kind in (EQUAL_HIGHS, EQUAL_LOWS)` (a lone
        `SINGLE_SWING` pool is real resting liquidity, but by definition
        shows no "build-up" of multiple participants - excluded here on
        purpose, a deliberate strictness choice, not an oversight).
  2. Engineered liquidity
     -> `LiquidityLevel.engineered is True` (LiquidityEngine's own
        unusually-tight-cluster heuristic - reused, not recomputed).
  3. Retail trap (the sweep actually rejected, not a genuine breakout)
     -> `LiquidityLevel.sweep_outcome == SweepOutcome.GRAB`.
  4. Liquidity sweep, confirmed (not left ambiguous by a short window)
     -> `LiquidityLevel.swept is True and LiquidityLevel.sweep_confirmed
        is True`.
  5. BOS or CHoCH confirmation (a real structural break followed the
     sweep in the opposite direction)
     -> `LiquidityLevel.reversal_break_level is not None` - this field is
        ONLY ever set by LiquidityEngine when a real, caller-supplied
        `StructureBreak` in the reversal direction was found - reused
        verbatim, never re-derived.
  6. Strong displacement (the reversal break itself was a real impulsive
     move, not a marginal poke)
     -> the matched `StructureBreak.displacement_ratio >=
        min_displacement_ratio` (looked up from the caller-supplied
        `structure_breaks` list by level + type + timestamp - the exact
        same list LiquidityEngine itself was given; this engine performs
        NO new structure detection, only a lookup of a value
        MarketStructureEngine already computed).
  7. Continuation toward the institutional objective (the reversal wasn't
     immediately negated)
     -> within `continuation_lookahead` candles after the reversal break:
        (a) price makes further progress beyond the broken level in the
            reversal direction, AND
        (b) price never closes back through the ORIGINAL swept liquidity
            price (which would mean the whole reversal thesis round-
            tripped and failed).
        This is a new, disclosed measurement over the OHLCV window this
        engine already has access to - not a duplicate of any existing
        engine's detection logic. If there isn't enough data left in the
        window to evaluate continuation at all, the event is honestly
        NOT emitted (no speculative/unconfirmable inducement) rather than
        guessed.

If ANY condition is missing, `detect()` emits nothing for that liquidity
level - matching this phase's explicit instruction: "If these conditions
are missing, DO NOT generate inducement."

INTEGRATION
--------------------------------------------------------------------------
`InducementEngine` takes the SAME `structure_breaks` list already passed
to `LiquidityEngine` (typically `MarketStructureEngine.analyze()
.external_breaks`) and the list of `LiquidityLevel`s already returned by
`LiquidityEngine.detect_liquidity_sweeps()`. It requires the OHLCV `df`
only for the continuation check (item 7) - no swing/BOS/CHoCH/sweep
detection is repeated anywhere in this file.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import pandas as pd

from app.smc.liquidity_engine import LiquidityLevel, LiquidityKind, LiquidityType, SweepOutcome

# Minimum local-ATR-normalized displacement the REVERSAL structure break
# itself must show (item 6) - deliberately the same default magnitude
# already used as the "real displacement" bar elsewhere in this project
# (e.g. SupplyDemandEngine's own `min_displacement_atr` default of 1.5,
# MarketStructureEngine's Strong-swing default of 1.0). Set slightly above
# MarketStructureEngine's own Strong-swing bar since Inducement is meant to
# be a HIGH-conviction subset of sweeps, not merely "a sweep near a
# somewhat-strong break".
DEFAULT_MIN_DISPLACEMENT_RATIO = 1.2

# How many candles after the reversal structure break to look for genuine
# continuation (item 7) before giving up and NOT emitting the event.
DEFAULT_CONTINUATION_LOOKAHEAD = 5

# How far back from a sweep to search the supplied `structure_breaks` list
# for the specific break that produced `reversal_break_level` (item 6's
# lookup). Matches LiquidityEngine's own `DEFAULT_RELATED_EVENT_LOOKAHEAD`
# so the same reversal window is being reasoned about on both sides.
DEFAULT_STRUCTURE_LOOKAHEAD = 20


@dataclass(frozen=True)
class InducementEvent:
    """One confirmed Inducement pattern - every field is either copied
    directly from an already-computed `LiquidityLevel`/`StructureBreak`,
    or a plain fact this engine measured directly from the OHLCV window
    (continuation). Nothing here is a score or a guess."""

    liquidity_level: LiquidityLevel
    reversal_direction: str            # "bullish" or "bearish" - the REAL move's direction, copied from the matched StructureBreak.direction
    reversal_break_type: str           # "BOS" or "CHoCH", copied from the matched StructureBreak.type
    reversal_break_timestamp: pd.Timestamp
    displacement_ratio: float          # the matched StructureBreak's own displacement_ratio
    continuation_confirmed: bool
    conditions_met: List[str]          # human-readable audit trail of every gate that passed - feeds the Evidence Engine
    timeframe: Optional[str] = None

    @property
    def swept_liquidity_price(self) -> float:
        return self.liquidity_level.price

    @property
    def swept_at(self) -> Optional[pd.Timestamp]:
        return self.liquidity_level.swept_at


class InducementEngine:
    """Stateless interpretation layer over already-computed LiquidityEngine
    (+ MarketStructureEngine) output. Calling `detect()` twice with the
    same inputs returns identical results - same design philosophy as
    every other engine in `app/smc/`."""

    def __init__(
        self,
        df: pd.DataFrame,
        structure_breaks: Optional[List] = None,
        min_displacement_ratio: float = DEFAULT_MIN_DISPLACEMENT_RATIO,
        continuation_lookahead: int = DEFAULT_CONTINUATION_LOOKAHEAD,
        structure_lookahead: int = DEFAULT_STRUCTURE_LOOKAHEAD,
        timeframe: Optional[str] = None,
    ) -> None:
        self.df = df
        self.structure_breaks = structure_breaks or []
        self.min_displacement_ratio = min_displacement_ratio
        self.continuation_lookahead = continuation_lookahead
        self.structure_lookahead = structure_lookahead
        self.timeframe = timeframe

    # ------------------------------------------------------------------
    # Item 6 - lookup only, no BOS/CHoCH recomputation.
    # ------------------------------------------------------------------
    def _find_matching_break(self, level: LiquidityLevel):
        """Find the specific `StructureBreak` object that produced
        `level.reversal_break_level`/`level.reversal_is_choch`, by
        matching on level value + break type + a timestamp window after
        the sweep - the same window shape LiquidityEngine's own
        `_find_related_break` already used to produce these fields in the
        first place. Returns None if it can't be found (honest - no
        guessing), which fails the gate."""
        if level.reversal_break_level is None or level.swept_at is None:
            return None
        wanted_type = "CHoCH" if level.reversal_is_choch else "BOS"
        try:
            swept_pos = self.df.index.get_loc(level.swept_at)
        except KeyError:
            return None
        window_end_ts = self.df.index[min(swept_pos + self.structure_lookahead, len(self.df) - 1)]

        candidates = [
            b for b in self.structure_breaks
            if b.type == wanted_type
            and b.level == level.reversal_break_level
            and level.swept_at <= b.timestamp <= window_end_ts
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda b: b.timestamp)

    # ------------------------------------------------------------------
    # Item 7 - a new, disclosed measurement over the OHLCV window this
    # engine already holds. Not a duplicate of any existing detector.
    # ------------------------------------------------------------------
    def _continuation_confirmed(self, matched_break, level: LiquidityLevel) -> bool:
        try:
            break_pos = self.df.index.get_loc(matched_break.timestamp)
        except KeyError:
            return False

        window_end = min(break_pos + self.continuation_lookahead, len(self.df) - 1)
        if window_end <= break_pos:
            # No room left in the data to evaluate continuation at all -
            # honestly unconfirmable, not guessed either way.
            return False

        closes = self.df["close"].values
        made_progress = False
        round_tripped = False

        for k in range(break_pos + 1, window_end + 1):
            if matched_break.direction == "bearish":
                if closes[k] < matched_break.level:
                    made_progress = True
                if closes[k] > level.price:
                    round_tripped = True
            else:  # "bullish"
                if closes[k] > matched_break.level:
                    made_progress = True
                if closes[k] < level.price:
                    round_tripped = True

        return made_progress and not round_tripped

    # ------------------------------------------------------------------
    # Public entry point.
    # ------------------------------------------------------------------
    def detect(self, swept_levels: List[LiquidityLevel]) -> List[InducementEvent]:
        """Evaluate every already-swept `LiquidityLevel` against the full
        AND-gate described in the module docstring. Levels that are not
        swept at all, or fail ANY single condition, produce nothing -
        false positives are minimized by design, per this phase's
        explicit mandate."""
        events: List[InducementEvent] = []

        for level in swept_levels:
            conditions_met: List[str] = []

            if not (level.swept and level.sweep_confirmed):
                continue
            conditions_met.append("Liquidity sweep confirmed")

            if level.kind not in (LiquidityKind.EQUAL_HIGHS, LiquidityKind.EQUAL_LOWS):
                continue
            conditions_met.append(f"Liquidity build-up ({level.kind.value})")

            if not level.engineered:
                continue
            conditions_met.append("Engineered liquidity")

            if level.sweep_outcome != SweepOutcome.GRAB:
                continue
            conditions_met.append("Retail trap (grab, not breakout)")

            if level.reversal_break_level is None:
                continue

            matched_break = self._find_matching_break(level)
            if matched_break is None:
                # reversal_break_level/reversal_is_choch say a break
                # exists, but this engine could not independently locate
                # the specific StructureBreak object (e.g. a different
                # structure_breaks list was supplied than the one
                # LiquidityEngine used) - honestly skip rather than guess
                # its displacement or direction.
                continue
            conditions_met.append(f"{matched_break.type} confirmation ({matched_break.direction})")

            if matched_break.displacement_ratio < self.min_displacement_ratio:
                continue
            conditions_met.append(f"Strong displacement (ratio={matched_break.displacement_ratio:.2f})")

            if not self._continuation_confirmed(matched_break, level):
                continue
            conditions_met.append("Continuation toward institutional objective confirmed")

            events.append(InducementEvent(
                liquidity_level=level,
                reversal_direction=matched_break.direction,
                reversal_break_type=matched_break.type,
                reversal_break_timestamp=matched_break.timestamp,
                displacement_ratio=matched_break.displacement_ratio,
                continuation_confirmed=True,
                conditions_met=conditions_met,
                timeframe=self.timeframe,
            ))

        return events
