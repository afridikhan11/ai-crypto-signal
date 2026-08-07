"""
Order Block Engine - unified ICT-standard Order Block detection.

STATUS (2026-07-28): PRODUCTION. Replaces `app/smc/order_blocks.py`
(deleted as part of this cutover - see ORDER_BLOCK_ENGINE_IMPLEMENTATION_
REPORT.md for the full before/after, files changed, and verification).
This is now the ONLY Order Block implementation in the project - the
separate, duplicate breaker-block detector that used to live inside
`app/services/ta_dashboard.py` (`_find_broken_order_block()`) has also
been removed; its job is now done by this engine's own breaker-tracking.

WHAT AN ICT ORDER BLOCK ACTUALLY IS
--------------------------------------------------------------------------
An Order Block is the last opposite-direction candle immediately before an
impulsive ("displacement") move - the footprint of the last position smart
money took on the wrong side before price moved away hard. A Bullish OB is
the last bearish (down-close) candle before a strong bullish push; a
Bearish OB is the last bullish (up-close) candle before a strong bearish
push. Three things separate a real ICT Order Block from "any two-candle
reversal pattern":
  1. DISPLACEMENT - the move away must be genuinely impulsive (large
     range relative to recent volatility), not an ordinary candle.
  2. A STRUCTURAL CONSEQUENCE - the impulsive leg should actually break a
     prior swing (BOS) or reverse the trend (CHoCH). An OB with no
     structural consequence behind it is a much weaker signal.
  3. OPTIONAL FVG CONFLUENCE - many ICT practitioners treat an Order
     Block that leaves a Fair Value Gap behind in the same leg as
     higher-probability than one that doesn't.

WHAT WAS WRONG WITH THE OLD IMPLEMENTATION, WITH EVIDENCE
--------------------------------------------------------------------------
Direct reading of the old `app/smc/order_blocks.py` (see
ORDER_BLOCK_ENGINE_IMPLEMENTATION_REPORT.md section 2 for the full
before/after table) found:
  - Zero displacement validation beyond a single candle's body/range
    ratio >= 0.6 - no measure of how large the move actually was.
  - Zero connection to Market Structure at all. `find_bullish_order_block
    ()`/`find_bearish_order_block()` took no BOS/CHoCH context whatsoever
    - direction was decided entirely by the CALLER, and the method just
    hunted for the nearest matching two-candle pattern, regardless of
    whether that specific pattern actually caused any structural break.
  - `check_mitigation(ob, current_price)` only ever compared a SINGLE
    current price against the OB's range - it had no memory of whether
    price had already touched the zone at some earlier point and moved
    on. Called again later with a different price, an OB that was
    genuinely mitigated could incorrectly report "not mitigated".
  - `detect_breaker_block(ob, current_price)` was a redundant re-check of
    the same "has price closed beyond this boundary" logic already
    computed internally by `_is_ob_broken()` - not a real "find me the
    breaker zones" feature.
  - `app/services/ta_dashboard.py` had ALSO built its own, completely
    separate breaker-block detector (`_find_broken_order_block()`) with
    its own copy of the momentum-candle check - genuine duplicate logic,
    already flagged in `SMC_ICT_MASTER_MIGRATION_PLAN.md`'s Phase 2.
  - `BlockType` had `BREAKER` and `MITIGATION` enum members that were
    NEVER actually assigned anywhere in the codebase - dead code.

ICT CONCEPTS IMPLEMENTED HERE
--------------------------------------------------------------------------
 1. Bullish Order Blocks   - `find_bullish_order_block()`
 2. Bearish Order Blocks   - `find_bearish_order_block()`
 3. Valid displacement     - `OrderBlock.displacement_ratio`: the
                              confirming candle's move away from the OB's
                              edge, in local-ATR terms. Optional
                              `min_displacement_atr` gate (default 0.0 -
                              off, reproduces the old engine's "any
                              qualifying candle pair counts" behavior).
 4. BOS relationship       - `OrderBlock.caused_bos` + `related_break_
                              level`, set when a caller-supplied
                              `structure_breaks` list (from
                              MarketStructureEngine) contains a matching-
                              direction BOS shortly after this OB formed.
 5. CHoCH relationship     - `OrderBlock.caused_choch`, same mechanism.
 6. Mitigation detection   - a real forward scan of the ENTIRE window
                              after the OB formed (not a single-price
                              snapshot) - `mitigated_at` records the
                              first candle whose close ever traded back
                              into the zone.
 7. Breaker Blocks         - `find_bullish_breaker_block()` /
                              `find_bearish_breaker_block()`: a former OB
                              that has been violated and flipped role
                              (see docstrings on those methods for the
                              exact direction convention - it is easy to
                              get backwards).
 8. Invalidated Order Blocks - `OrderBlock.state == OBState.INVALIDATED`:
                              a breaker that was ITSELF later broken again
                              (price reclaimed back through it) - fully
                              dead, no longer a zone of interest.
 9. Fresh vs Mitigated OBs - `OrderBlock.state` (FRESH / MITIGATED /
                              BREAKER / INVALIDATED) - a real lifecycle,
                              not a single boolean re-derived every call.
10. Order Block strength   - `OrderBlock.strength` (STRONG / MODERATE /
                              WEAK), a simple, documented, deterministic
                              combination of displacement magnitude,
                              BOS/CHoCH causation, and FVG confluence -
                              see `_classify_strength()`. Not a black-box
                              score, consistent with this project's
                              existing "explainable arithmetic" scoring
                              philosophy (see app/services/ta_dashboard.py).
11. Multi-Timeframe compatibility - the engine takes any OHLCV `df` with
                              no hardcoded timeframe/candle-count
                              assumptions (identical to the old engine in
                              this respect), and every `OrderBlock` now
                              carries an optional `timeframe` tag (set via
                              the constructor's `timeframe` parameter) so
                              a future caller could run this engine once
                              per timeframe and compare/merge results with
                              clear provenance. No caller currently needs
                              that orchestration, so it isn't built here -
                              see Known Limitations in the implementation
                              report.

BACKWARD-COMPATIBLE FIELDS
--------------------------------------------------------------------------
`OrderBlock.type` is still a `BlockType` enum with the EXACT same two
string values the old engine used (`"bullish_ob"` / `"bearish_ob"`) -
`app/ai/scorer.py` (AI Scoring, out of scope for this migration) reads
these two exact strings off `ob.get("type")` and must not need to change.
`OrderBlock.mitigated` is still a plain bool (`True` whenever `state !=
FRESH`) for the same reason - every caller's existing `"mitigated": ...`
dict entry keeps working unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

import pandas as pd


class BlockType(Enum):
    BULLISH_OB = "bullish_ob"
    BEARISH_OB = "bearish_ob"


class OBState(Enum):
    FRESH = "fresh"
    MITIGATED = "mitigated"
    BREAKER = "breaker"
    INVALIDATED = "invalidated"


class OBStrength(Enum):
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"


# Displacement-ratio thresholds (local-ATR terms) for strength
# classification - see `_classify_strength()`. Plain, documented
# constants, deliberately NOT wired to CalibrationProfile (Calibration is
# out of scope for this migration), same approach already used for
# MarketStructureEngine's own strong/weak swing threshold.
STRONG_STRENGTH_SCORE = 1.5
MODERATE_STRENGTH_SCORE = 0.75

# How many candles after an Order Block forms to look for a causally
# related structure break / FVG. A genuine causal relationship should be
# close in time to the OB - a "BOS" 200 candles later has nothing to do
# with this specific candle pair.
DEFAULT_RELATED_EVENT_LOOKAHEAD = 20


@dataclass
class OrderBlock:
    timestamp: pd.Timestamp
    high: float
    low: float
    type: BlockType
    state: OBState
    mitigated: bool                        # True whenever state != FRESH
    displacement_ratio: float
    caused_bos: bool
    caused_choch: bool
    related_break_level: Optional[float]
    has_fvg_confluence: bool
    strength: OBStrength
    mitigated_at: Optional[pd.Timestamp]
    breaker_at: Optional[pd.Timestamp]
    invalidated_at: Optional[pd.Timestamp]
    timeframe: Optional[str] = None


def _local_atr(df: pd.DataFrame, lookback: int = 14) -> float:
    """Same lightweight high-low-range ATR estimate MarketStructureEngine
    uses, reused here rather than inventing a second definition."""
    if len(df) == 0:
        return 0.0
    return float((df["high"] - df["low"]).tail(lookback).mean())


class OrderBlockEngine:
    """
    Stateless, single-window Order Block analysis - calling it twice with
    the same `df`/`structure_breaks`/`fvgs` returns identical results, the
    same design philosophy as `MarketStructureEngine`. `structure_breaks`
    and `fvgs` are OPTIONAL: every caller today already computes Market
    Structure breaks before Order Block detection (this engine's BOS/CHoCH
    relationship needs that data), and most already compute FVGs
    somewhere in the same function (confluence needs that data too) - see
    ORDER_BLOCK_ENGINE_IMPLEMENTATION_REPORT.md for exactly which callers
    supply which. When omitted, the engine still finds real Order Blocks
    (displacement-validated, lifecycle-tracked) - it just can't say
    whether a specific one caused a structural break or has FVG
    confluence, and reports that honestly (`caused_bos=False`,
    `caused_choch=False`, `has_fvg_confluence=False`) rather than
    guessing.
    """

    def __init__(
        self,
        df: pd.DataFrame,
        body_ratio_threshold: float = 0.6,
        structure_breaks: Optional[List] = None,
        fvgs: Optional[List] = None,
        min_displacement_atr: float = 0.0,
        related_event_lookahead: int = DEFAULT_RELATED_EVENT_LOOKAHEAD,
        timeframe: Optional[str] = None,
    ):
        self.df = df
        self.body_ratio_threshold = body_ratio_threshold
        self.structure_breaks = structure_breaks or []
        self.fvgs = fvgs or []
        self.min_displacement_atr = min_displacement_atr
        self.related_event_lookahead = related_event_lookahead
        self.timeframe = timeframe
        self._atr = _local_atr(df)

        # PERFORMANCE (2026-07-30): OHLC extracted ONCE as flat float64
        # arrays. `_is_momentum_candle()` and `_lifecycle()` are both hot
        # scan loops that previously did `self.df.iloc[...]` per candle,
        # constructing a fresh pandas Series on every iteration. Reading
        # these arrays instead is a pure mechanical change - identical
        # values, identical comparisons, identical results (verified
        # bit-for-bit against the previous implementation across randomized
        # frames; this module's full regression suite also passes
        # unchanged).
        self._open = df["open"].to_numpy(dtype=float)
        self._high = df["high"].to_numpy(dtype=float)
        self._low = df["low"].to_numpy(dtype=float)
        self._close = df["close"].to_numpy(dtype=float)

    # ------------------------------------------------------------------
    # Base candle-pair detection (unchanged definition from the old
    # engine - not evidenced as broken, so preserved: the last opposite-
    # direction momentum candle immediately before a confirming momentum
    # candle in the other direction).
    # ------------------------------------------------------------------
    def _is_momentum_candle(self, idx: int, direction: str) -> bool:
        if idx < 0 or idx >= len(self.df):
            return False
        open_, close = self._open[idx], self._close[idx]
        body = abs(close - open_)
        total_range = self._high[idx] - self._low[idx]
        if total_range == 0 or body / total_range < self.body_ratio_threshold:
            return False
        return close > open_ if direction == "up" else close < open_

    # ------------------------------------------------------------------
    # Valid displacement (item 3)
    # ------------------------------------------------------------------
    def _displacement_ratio(self, ob_idx: int, confirm_idx: int, direction: str) -> float:
        """How far the confirming candle's close travelled past the OB
        candle's invalidation edge, in local-ATR terms."""
        # `not (atr > 0)` also catches NaN (NaN <= 0 is False, so a NaN ATR
        # would otherwise slip through and propagate NaN into the gate).
        if not (self._atr > 0):
            return 0.0
        confirm_close = self._close[confirm_idx]
        edge = self._low[ob_idx] if direction == "bullish" else self._high[ob_idx]
        return abs(confirm_close - edge) / self._atr

    # ------------------------------------------------------------------
    # BOS / CHoCH relationship (items 4-5)
    # ------------------------------------------------------------------
    def _find_related_break(self, direction: str, confirm_idx: int):
        """
        Earliest caller-supplied StructureBreak of matching direction
        whose own first-break candle falls within
        [confirm_idx, confirm_idx + related_event_lookahead] - i.e. a
        structure break that plausibly belongs to THIS OB's impulsive
        leg, not an unrelated later event. Returns None if no
        `structure_breaks` were supplied or none qualify.
        """
        if not self.structure_breaks or confirm_idx >= len(self.df):
            return None
        window_end_ts = self.df.index[min(confirm_idx + self.related_event_lookahead, len(self.df) - 1)]
        confirm_ts = self.df.index[confirm_idx]
        candidates = [
            b for b in self.structure_breaks
            if b.direction == direction and confirm_ts <= b.timestamp <= window_end_ts
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda b: b.timestamp)

    # ------------------------------------------------------------------
    # FVG confluence (item 3 / strength input)
    # ------------------------------------------------------------------
    def _has_fvg_confluence(self, direction: str, ob_idx: int, confirm_idx: int) -> bool:
        """Any caller-supplied FVG of matching direction whose own
        timestamp falls within the same displacement leg as this OB."""
        if not self.fvgs or confirm_idx >= len(self.df):
            return False
        window_end_ts = self.df.index[min(confirm_idx + self.related_event_lookahead, len(self.df) - 1)]
        ob_ts = self.df.index[ob_idx]
        wanted_type = "bullish" if direction == "bullish" else "bearish"
        return any(
            ob_ts <= f.timestamp <= window_end_ts and getattr(f.type, "value", f.type) == wanted_type
            for f in self.fvgs
        )

    # ------------------------------------------------------------------
    # Order Block strength (item 10)
    # ------------------------------------------------------------------
    @staticmethod
    def _classify_strength(displacement_ratio: float, caused_bos: bool, caused_choch: bool, has_fvg_confluence: bool) -> OBStrength:
        score = displacement_ratio
        if caused_choch:
            score += 1.0
        elif caused_bos:
            score += 0.5
        if has_fvg_confluence:
            score += 0.5
        if score >= STRONG_STRENGTH_SCORE:
            return OBStrength.STRONG
        if score >= MODERATE_STRENGTH_SCORE:
            return OBStrength.MODERATE
        return OBStrength.WEAK

    # ------------------------------------------------------------------
    # Fresh / Mitigated / Breaker / Invalidated lifecycle (items 6-9)
    # ------------------------------------------------------------------
    def _lifecycle(self, ob_idx: int, low: float, high: float, direction: str):
        """
        Forward, body-close-only scan (never wicks - same confirmation
        standard MarketStructureEngine uses) from the candle after the OB
        formed to the end of the window:
          - MITIGATED the first time price closes back inside [low, high].
          - BREAKER the first time price closes fully beyond the OB's
            invalidating edge (below `low` for a bullish OB, above `high`
            for a bearish OB) - the zone has flipped role.
          - INVALIDATED if, AFTER becoming a breaker, price closes back
            through the breaker in the ORIGINAL direction again (fully
            reclaiming the level) - the breaker itself is now dead.
        Once INVALIDATED is reached the scan stops; mitigation is not
        tracked separately after a breaker forms since MITIGATED/BREAKER/
        INVALIDATED are mutually exclusive lifecycle stages, not flags.
        """
        mitigated_at = breaker_at = invalidated_at = None
        index = self.df.index
        for j in range(ob_idx + 1, len(self._close)):
            close = self._close[j]
            ts = index[j]
            if breaker_at is None:
                if direction == "bullish" and close < low:
                    breaker_at = ts
                    continue
                if direction == "bearish" and close > high:
                    breaker_at = ts
                    continue
                if mitigated_at is None and low <= close <= high:
                    mitigated_at = ts
            else:
                if direction == "bullish" and close > high:
                    invalidated_at = ts
                    break
                if direction == "bearish" and close < low:
                    invalidated_at = ts
                    break

        if invalidated_at is not None:
            state = OBState.INVALIDATED
        elif breaker_at is not None:
            state = OBState.BREAKER
        elif mitigated_at is not None:
            state = OBState.MITIGATED
        else:
            state = OBState.FRESH
        return state, mitigated_at, breaker_at, invalidated_at

    # ------------------------------------------------------------------
    # Build one OrderBlock from a confirmed candle pair
    # ------------------------------------------------------------------
    def _build(self, ob_idx: int, confirm_idx: int, block_type: BlockType) -> OrderBlock:
        direction = "bullish" if block_type == BlockType.BULLISH_OB else "bearish"
        low, high = float(self._low[ob_idx]), float(self._high[ob_idx])

        displacement_ratio = self._displacement_ratio(ob_idx, confirm_idx, direction)
        related_break = self._find_related_break(direction, confirm_idx)
        caused_bos = bool(related_break and related_break.type == "BOS")
        caused_choch = bool(related_break and related_break.type == "CHoCH")
        has_fvg_confluence = self._has_fvg_confluence(direction, ob_idx, confirm_idx)
        strength = self._classify_strength(displacement_ratio, caused_bos, caused_choch, has_fvg_confluence)
        state, mitigated_at, breaker_at, invalidated_at = self._lifecycle(ob_idx, low, high, direction)

        return OrderBlock(
            timestamp=self.df.index[ob_idx], high=high, low=low, type=block_type,
            state=state, mitigated=(state != OBState.FRESH),
            displacement_ratio=displacement_ratio, caused_bos=caused_bos, caused_choch=caused_choch,
            related_break_level=(related_break.level if related_break else None),
            has_fvg_confluence=has_fvg_confluence, strength=strength,
            mitigated_at=mitigated_at, breaker_at=breaker_at, invalidated_at=invalidated_at,
            timeframe=self.timeframe,
        )

    # ------------------------------------------------------------------
    # Public entry points (items 1-2)
    # ------------------------------------------------------------------
    def find_bullish_order_block(self) -> Optional[OrderBlock]:
        """Most recent bullish Order Block that is still acting in its
        original (support) role - skips ones that have already flipped
        to BREAKER or died as INVALIDATED (use `find_bearish_breaker_
        block()` to find a bullish OB that flipped to bearish role)."""
        for i in range(len(self.df) - 2, 1, -1):
            if self._is_momentum_candle(i, "down") and self._is_momentum_candle(i + 1, "up"):
                ob = self._build(i, i + 1, BlockType.BULLISH_OB)
                if ob.displacement_ratio < self.min_displacement_atr:
                    continue
                if ob.state in (OBState.BREAKER, OBState.INVALIDATED):
                    continue
                return ob
        return None

    def find_bearish_order_block(self) -> Optional[OrderBlock]:
        """Mirror of `find_bullish_order_block()` for bearish OBs."""
        for i in range(len(self.df) - 2, 1, -1):
            if self._is_momentum_candle(i, "up") and self._is_momentum_candle(i + 1, "down"):
                ob = self._build(i, i + 1, BlockType.BEARISH_OB)
                if ob.displacement_ratio < self.min_displacement_atr:
                    continue
                if ob.state in (OBState.BREAKER, OBState.INVALIDATED):
                    continue
                return ob
        return None

    # ------------------------------------------------------------------
    # Breaker Blocks (item 7) - direction convention, spelled out
    # explicitly because it is easy to get backwards: a Breaker Block's
    # CURRENT trading role is the OPPOSITE of its original `type`. A
    # "bullish breaker" is a former BEARISH Order Block that was violated
    # and now acts as SUPPORT (bullish role); a "bearish breaker" is a
    # former BULLISH Order Block that was violated and now acts as
    # RESISTANCE (bearish role).
    # ------------------------------------------------------------------
    def find_bullish_breaker_block(self) -> Optional[OrderBlock]:
        """Most recent former BEARISH Order Block that has been violated
        and flipped to a bullish (support) role. Returns None if it has
        since been INVALIDATED (broken back through again)."""
        for i in range(len(self.df) - 2, 1, -1):
            if self._is_momentum_candle(i, "up") and self._is_momentum_candle(i + 1, "down"):
                ob = self._build(i, i + 1, BlockType.BEARISH_OB)
                if ob.state == OBState.BREAKER:
                    return ob
        return None

    def find_bearish_breaker_block(self) -> Optional[OrderBlock]:
        """Most recent former BULLISH Order Block that has been violated
        and flipped to a bearish (resistance) role. Returns None if it
        has since been INVALIDATED (broken back through again)."""
        for i in range(len(self.df) - 2, 1, -1):
            if self._is_momentum_candle(i, "down") and self._is_momentum_candle(i + 1, "up"):
                ob = self._build(i, i + 1, BlockType.BULLISH_OB)
                if ob.state == OBState.BREAKER:
                    return ob
        return None
