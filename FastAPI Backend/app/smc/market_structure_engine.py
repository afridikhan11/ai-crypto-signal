"""
Market Structure Engine - unified ICT-standard swing / BOS / CHoCH engine.

STATUS (2026-07-28): PRODUCTION. This is now the ONLY Market Structure
implementation in the project. It was built and verified standalone first
(see MARKET_STRUCTURE_ENGINE_IMPLEMENTATION_REPORT.md), then cut over into
every real caller - SignalGenerator, Market Scorer, Token Scorer, the
Dashboard endpoint, and the Technical Dashboard - once that verification
was approved (see MARKET_STRUCTURE_CUTOVER_REPORT.md for the cutover
itself: files changed, regression results, and confirmation that the old
`app/smc/market_structure.py` has been deleted, not left as dead code).

WHY ONE ENGINE, NOT SEPARATE BOS/CHoCH MODULES
--------------------------------------------------------------------------
BOS and CHoCH share the same swing detection, the same classification, and
the same "find where this specific swing was first broken" logic - the
only difference is CHoCH requires an extra precondition (two consecutive
same-type swings confirming an established trend) before the break
qualifies. Splitting them into separate modules would either duplicate
that shared logic or force one to depend awkwardly on internals of the
other. This file treats both as two outputs of one `_scan_breaks()` method.

WHAT THIS ENGINE FIXES, WITH EVIDENCE
--------------------------------------------------------------------------
`BOS_Duplicate_Investigation_Report.md` proved, by executing the real,
now-retired `market_structure.py` against synthetic data, that the same swing
could be reported as "just broken" on 35 consecutive candles, because
`StructureBreak.timestamp` was always set to the CURRENT candle rather
than the candle where the break actually first happened, and because
`detect_bos_choch()` always re-scanned only the last 3 swings with no
memory of what was already reported.

This engine fixes that at the root, in the stateless read itself: instead
of asking "does the LAST candle satisfy the break condition," it scans
FORWARD from each candidate swing's formation to find the TRUE first
candle whose close satisfies the condition, and reports THAT candle's
timestamp. No persisted state is required for this fix - it's a
correctness fix to a single, self-contained window read. A separate,
OPT-IN `MarketStructureStateTracker` is also provided for callers that
scan the same symbol repeatedly over time (e.g. a live scanner) and need
to know "have I already acted on this exact break," but the core
Engine.analyze() call remains a pure, stateless function - this keeps
one-shot readers (Dashboard, Market Scan, Token Scan) working the same
honest way they always have: "here is what structure looks like right
now," not an event stream.

ICT CONCEPTS IMPLEMENTED HERE
--------------------------------------------------------------------------
 1. Swing Detection Engine       - symmetric pivot swings (unchanged
                                    definition from the old module - not
                                    evidenced as broken, so preserved)
 2. Strong Swing Detection       - a swing whose formation displacement
                                    (in local-ATR terms) meets/exceeds a
                                    threshold
 3. Weak Swing Detection         - below that threshold
 4. Protected High               - the most recent swing high that has not
                                    been closed above within the window
 5. Protected Low                - the most recent swing low that has not
                                    been closed below within the window
 6. Internal Structure           - swings/breaks from a finer pivot tier
                                    (default window=3)
 7. External Structure           - swings/breaks from a coarser pivot tier
                                    (default window=8), plus an explicit
                                    `structure_alignment` relating the two
 8. BOS                          - close beyond the MOST RECENT
                                    counter-trend swing (not "any of the
                                    last 3", see docstring on _scan_breaks)
 9. CHoCH                        - BOS logic + the 2-swing established-
                                    trend precondition
10. MSS                          - NOT implemented as a separate detection
                                    rule. Evidence search (this project's
                                    own prior audits) found no consistent,
                                    universally-agreed distinct rule set
                                    for MSS versus CHoCH - most ICT
                                    teaching treats it as a synonym or
                                    sub-case. Implemented honestly as a
                                    label (`StructureBreak.is_mss`, True
                                    for every CHoCH), not invented logic.
11. Proper Event Identity        - `StructureBreak.timestamp` now means
                                    "the candle where this break first
                                    occurred", not "the current candle"
12. First-break Detection        - the forward-scan described above
13. State Management             - `MarketStructureStateTracker`, opt-in,
                                    for callers that persist across scans
14. Body-close Confirmation      - preserved unchanged: compares real
                                    close price, not wick, to the level
15. Displacement Confirmation    - optional, OFF by default
                                    (`min_displacement_atr=0.0` reproduces
                                    "any close beyond qualifies", matching
                                    today's behavior); when set above zero,
                                    a break must travel at least that many
                                    local-ATRs past the level to qualify
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

import pandas as pd


class SwingType(Enum):
    HH = "Higher High"
    HL = "Higher Low"
    LH = "Lower High"
    LL = "Lower Low"


class SwingStrength(Enum):
    STRONG = "strong"
    WEAK = "weak"


class StructureScope(Enum):
    INTERNAL = "internal"
    EXTERNAL = "external"


# Default Strong/Weak swing threshold, in local-ATR terms. Not wired to
# CalibrationProfile (Calibration is explicitly out of scope for this
# change) - a plain, documented constant here, deliberately conservative,
# pending future asset-class-specific tuning once real outcome data exists
# under this engine's behavior.
DEFAULT_STRONG_SWING_DISPLACEMENT_ATR = 1.0


@dataclass
class SwingPoint:
    timestamp: pd.Timestamp
    price: float
    type: SwingType
    index: int
    strength: SwingStrength
    displacement_ratio: float  # magnitude of the move into this swing's formation, in local-ATR terms


@dataclass
class StructureBreak:
    timestamp: pd.Timestamp     # the candle where this break FIRST occurred (see module docstring, item 11/12)
    type: str                    # "BOS" or "CHoCH"
    direction: str                 # "bullish" or "bearish"
    broken_swing: SwingPoint
    level: float
    scope: StructureScope
    displacement_ratio: float      # how far the confirming close travelled beyond `level`, in local-ATR terms
    is_mss: bool                   # True for every CHoCH-type break - see module docstring, item 10


@dataclass
class MarketStructureSnapshot:
    swing_highs: List[SwingPoint]
    swing_lows: List[SwingPoint]
    internal_breaks: List[StructureBreak]
    external_breaks: List[StructureBreak]
    protected_high: Optional[float]
    protected_low: Optional[float]
    structure_alignment: str  # "aligned_bullish" / "aligned_bearish" / "conflicted" / "insufficient_data"
    # 2026-07-28 (Liquidity Engine integration - see LIQUIDITY_ENGINE_
    # IMPLEMENTATION_REPORT.md): `swing_highs`/`swing_lows` above have
    # always been the EXTERNAL tier only (see analyze()). The internal
    # tier's own swings were already computed every call but discarded -
    # exposing them here is purely additive (new fields with safe
    # defaults, appended at the end of the dataclass; `analyze()` already
    # constructs this dataclass with keyword arguments only, so no
    # existing caller is affected) and lets LiquidityEngine distinguish
    # real Internal vs External Liquidity without reimplementing swing
    # detection itself.
    internal_swing_highs: List[SwingPoint] = field(default_factory=list)
    internal_swing_lows: List[SwingPoint] = field(default_factory=list)


def _local_atr(df: pd.DataFrame, lookback: int = 14) -> float:
    """
    Same lightweight high-low-range estimate already used elsewhere in this
    codebase for the identical purpose (scripts/analyze_smc_frequency.py's
    `atr_estimate`) - deliberately reused rather than inventing a second
    ATR definition or importing ConfirmationIndicators, keeping this engine
    self-contained.
    """
    if len(df) == 0:
        return 0.0
    return float((df["high"] - df["low"]).tail(lookback).mean())


class MarketStructureEngine:
    """
    Stateless, single-window Market Structure analysis. `analyze()` is a
    pure function of `df` - calling it twice with the same data returns
    identical results, and it holds no memory of any previous call. This
    is intentional: one-shot readers (Dashboard, Market Scan, Token Scan)
    correctly want "what does structure look like right now," not an
    event stream. See `MarketStructureStateTracker` for the opt-in,
    caller-held state needed to additionally answer "have I already acted
    on this exact break."
    """

    def __init__(
        self,
        df: pd.DataFrame,
        internal_pivot_window: int = 3,
        external_pivot_window: int = 8,
        min_displacement_atr: float = 0.0,
        strong_swing_displacement_atr: float = DEFAULT_STRONG_SWING_DISPLACEMENT_ATR,
    ):
        self.df = df
        self.internal_pivot_window = internal_pivot_window
        self.external_pivot_window = external_pivot_window
        self.min_displacement_atr = min_displacement_atr
        self.strong_swing_displacement_atr = strong_swing_displacement_atr
        self._atr = _local_atr(df)

    # ------------------------------------------------------------------
    # Swing Detection Engine (items 1-3): pivot swings + strong/weak
    # ------------------------------------------------------------------
    def _swing_displacement_ratio(self, index: int, pivot_window: int) -> float:
        """
        How large the move into this pivot was, in local-ATR terms:
        the price change from `pivot_window` candles before the pivot to
        the pivot itself. A simplification (not full impulse-leg
        detection) - see MARKET_STRUCTURE_ENGINE_IMPLEMENTATION_REPORT.md's
        Known Limitations for what a fuller treatment would require.
        """
        if self._atr <= 0:
            return 0.0
        closes = self.df["close"].values
        start = max(0, index - pivot_window)
        return abs(closes[index] - closes[start]) / self._atr

    def _detect_swings(self, pivot_window: int) -> "tuple[List[SwingPoint], List[SwingPoint]]":
        highs = self.df["high"].values
        lows = self.df["low"].values
        n = len(self.df)
        swing_highs: List[SwingPoint] = []
        swing_lows: List[SwingPoint] = []
        if n < 2 * pivot_window + 1:
            return swing_highs, swing_lows

        for i in range(pivot_window, n - pivot_window):
            if highs[i] == max(highs[i - pivot_window:i + pivot_window + 1]):
                disp = self._swing_displacement_ratio(i, pivot_window)
                swing_highs.append(SwingPoint(
                    timestamp=self.df.index[i], price=highs[i], type=SwingType.HH, index=i,
                    strength=SwingStrength.STRONG if disp >= self.strong_swing_displacement_atr else SwingStrength.WEAK,
                    displacement_ratio=disp,
                ))
            if lows[i] == min(lows[i - pivot_window:i + pivot_window + 1]):
                disp = self._swing_displacement_ratio(i, pivot_window)
                swing_lows.append(SwingPoint(
                    timestamp=self.df.index[i], price=lows[i], type=SwingType.HL, index=i,
                    strength=SwingStrength.STRONG if disp >= self.strong_swing_displacement_atr else SwingStrength.WEAK,
                    displacement_ratio=disp,
                ))

        self._classify_highs(swing_highs)
        self._classify_lows(swing_lows)
        return swing_highs, swing_lows

    @staticmethod
    def _classify_highs(swing_highs: List[SwingPoint]) -> None:
        """Unchanged from the existing module: each high is HH if above the
        previous swing high, else LH. The first swing high defaults HH."""
        prev = None
        for sw in swing_highs:
            sw.type = (SwingType.HH if prev is None else (SwingType.HH if sw.price > prev.price else SwingType.LH))
            prev = sw

    @staticmethod
    def _classify_lows(swing_lows: List[SwingPoint]) -> None:
        """Unchanged from the existing module: each low is HL if above the
        previous swing low, else LL. The first swing low defaults HL."""
        prev = None
        for sw in swing_lows:
            sw.type = (SwingType.HL if prev is None else (SwingType.HL if sw.price > prev.price else SwingType.LL))
            prev = sw

    # ------------------------------------------------------------------
    # First-break Detection (item 12) + Event Identity (item 11)
    # ------------------------------------------------------------------
    def _find_first_break_after(self, swing: SwingPoint, direction: str) -> Optional[int]:
        """
        Scans FORWARD from `swing.index` for the first candle whose CLOSE
        (body-close confirmation, item 14 - never the wick) satisfies the
        break condition. Returns that candle's index, or None if the swing
        is never broken within this window. This is what makes
        `StructureBreak.timestamp` mean "when this actually first broke"
        instead of "whatever candle we happened to be looking at."
        """
        closes = self.df["close"].values
        for j in range(swing.index + 1, len(self.df)):
            if direction == "bullish" and closes[j] > swing.price:
                return j
            if direction == "bearish" and closes[j] < swing.price:
                return j
        return None

    # ------------------------------------------------------------------
    # BOS (item 8) + CHoCH (item 9) - one shared scan, per scope
    # ------------------------------------------------------------------
    def _scan_breaks(
        self, swing_highs: List[SwingPoint], swing_lows: List[SwingPoint], scope: StructureScope
    ) -> List[StructureBreak]:
        """
        BOS is evaluated against the SINGLE most recent counter-trend
        swing (the most recently formed LH for a bullish break, most
        recently formed HL for a bearish break) - not a rolling scan of
        "the last 3, whichever is found first" the way the existing
        module works. By the time a newer counter-trend swing has formed,
        an older one's break (if any) already happened and was already
        reported at that time; the only currently-live question is
        whether the MOST RECENT one has broken. This is both simpler and
        more evidence-consistent with ICT's own definition (structure is
        read relative to the swing that currently defines the leg).

        CHoCH keeps the existing module's precondition unchanged: the
        last 2 highs must both be HH and the last 2 lows must both be HL
        (established uptrend) before a break of the most recent HL counts
        as a bearish CHoCH, and the mirror image for bullish CHoCH.
        """
        breaks: List[StructureBreak] = []

        most_recent_lh = next((sw for sw in reversed(swing_highs) if sw.type == SwingType.LH), None)
        if most_recent_lh is not None:
            j = self._find_first_break_after(most_recent_lh, "bullish")
            if j is not None:
                disp = abs(self.df["close"].values[j] - most_recent_lh.price) / self._atr if self._atr > 0 else 0.0
                if disp >= self.min_displacement_atr:
                    breaks.append(StructureBreak(
                        timestamp=self.df.index[j], type="BOS", direction="bullish",
                        broken_swing=most_recent_lh, level=most_recent_lh.price, scope=scope,
                        displacement_ratio=disp, is_mss=False,
                    ))

        most_recent_hl = next((sw for sw in reversed(swing_lows) if sw.type == SwingType.HL), None)
        if most_recent_hl is not None:
            j = self._find_first_break_after(most_recent_hl, "bearish")
            if j is not None:
                disp = abs(self.df["close"].values[j] - most_recent_hl.price) / self._atr if self._atr > 0 else 0.0
                if disp >= self.min_displacement_atr:
                    breaks.append(StructureBreak(
                        timestamp=self.df.index[j], type="BOS", direction="bearish",
                        broken_swing=most_recent_hl, level=most_recent_hl.price, scope=scope,
                        displacement_ratio=disp, is_mss=False,
                    ))

        if len(swing_lows) >= 2 and len(swing_highs) >= 2:
            last_low, prev_low = swing_lows[-1], swing_lows[-2]
            last_high, prev_high = swing_highs[-1], swing_highs[-2]

            established_uptrend = (
                prev_low.type == SwingType.HL and last_low.type == SwingType.HL
                and prev_high.type == SwingType.HH and last_high.type == SwingType.HH
            )
            if established_uptrend:
                j = self._find_first_break_after(last_low, "bearish")
                if j is not None:
                    disp = abs(self.df["close"].values[j] - last_low.price) / self._atr if self._atr > 0 else 0.0
                    if disp >= self.min_displacement_atr:
                        breaks.append(StructureBreak(
                            timestamp=self.df.index[j], type="CHoCH", direction="bearish",
                            broken_swing=last_low, level=last_low.price, scope=scope,
                            displacement_ratio=disp, is_mss=True,
                        ))

            established_downtrend = (
                prev_high.type == SwingType.LH and last_high.type == SwingType.LH
                and prev_low.type == SwingType.LL and last_low.type == SwingType.LL
            )
            if established_downtrend:
                j = self._find_first_break_after(last_high, "bullish")
                if j is not None:
                    disp = abs(self.df["close"].values[j] - last_high.price) / self._atr if self._atr > 0 else 0.0
                    if disp >= self.min_displacement_atr:
                        breaks.append(StructureBreak(
                            timestamp=self.df.index[j], type="CHoCH", direction="bullish",
                            broken_swing=last_high, level=last_high.price, scope=scope,
                            displacement_ratio=disp, is_mss=True,
                        ))

        # De-duplicate: in an established trend the BOS scan and the CHoCH
        # scan can land on the SAME swing/candle (e.g. breaking the last HL in
        # an uptrend), emitting one price event as both a continuation BOS and
        # a reversal CHoCH — contradictory labels. A CHoCH (change of
        # character) is the stronger, correct read, so drop any BOS that shares
        # its timestamp/direction/level.
        choch_keys = {
            (b.timestamp, b.direction, b.level) for b in breaks if b.type == "CHoCH"
        }
        if choch_keys:
            breaks = [
                b for b in breaks
                if not (b.type == "BOS" and (b.timestamp, b.direction, b.level) in choch_keys)
            ]

        return breaks

    # ------------------------------------------------------------------
    # Protected High / Low (items 4-5)
    # ------------------------------------------------------------------
    def _protected_levels(
        self, swing_highs: List[SwingPoint], swing_lows: List[SwingPoint]
    ) -> "tuple[Optional[float], Optional[float]]":
        """
        The most recent swing high/low that has NOT been closed beyond
        anywhere in this window - the standing reference level the
        current bias depends on. Computed from a single stateless window
        (no persisted history needed for this definition); a fuller ICT
        treatment that tracks a protected level's history ACROSS regime
        changes (e.g. re-anchoring only on a confirmed CHoCH) would build
        on top of `MarketStructureStateTracker` in a future phase - see
        Known Limitations in the implementation report.
        """
        protected_high = None
        for sw in reversed(swing_highs):
            if self._find_first_break_after(sw, "bullish") is None:
                protected_high = sw.price
                break

        protected_low = None
        for sw in reversed(swing_lows):
            if self._find_first_break_after(sw, "bearish") is None:
                protected_low = sw.price
                break

        return protected_high, protected_low

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def analyze(self) -> MarketStructureSnapshot:
        internal_highs, internal_lows = self._detect_swings(self.internal_pivot_window)
        external_highs, external_lows = self._detect_swings(self.external_pivot_window)

        internal_breaks = self._scan_breaks(internal_highs, internal_lows, StructureScope.INTERNAL)
        external_breaks = self._scan_breaks(external_highs, external_lows, StructureScope.EXTERNAL)

        protected_high, protected_low = self._protected_levels(external_highs, external_lows)

        def _latest_bias(breaks: List[StructureBreak]) -> Optional[str]:
            # Bias is the direction of the chronologically most-recent break.
            # breaks are appended in fixed code order (BOS then CHoCH), NOT in
            # time order, so breaks[-1] could be an older event — pick by
            # timestamp instead.
            if not breaks:
                return None
            return max(breaks, key=lambda b: b.timestamp).direction

        internal_bias = _latest_bias(internal_breaks)
        external_bias = _latest_bias(external_breaks)
        if internal_bias is None or external_bias is None:
            alignment = "insufficient_data"
        elif internal_bias == external_bias:
            alignment = f"aligned_{internal_bias}"
        else:
            alignment = "conflicted"

        return MarketStructureSnapshot(
            swing_highs=external_highs,
            swing_lows=external_lows,
            internal_breaks=internal_breaks,
            external_breaks=external_breaks,
            protected_high=protected_high,
            protected_low=protected_low,
            structure_alignment=alignment,
            internal_swing_highs=internal_highs,
            internal_swing_lows=internal_lows,
        )


class MarketStructureStateTracker:
    """
    Opt-in, per-caller session state for Event Identity / First-break
    Detection ACROSS REPEATED calls over time (item 13 - State
    Management). `MarketStructureEngine.analyze()` itself stays fully
    stateless (see its docstring); this tracker is for the one kind of
    caller that needs to know "have I already acted on this exact break
    before" across many scans of the same symbol - e.g. a live scanner
    that re-analyzes every 15 minutes and must not treat the same,
    still-active break as a brand-new signal each cycle.

    NOT currently wired into any live caller (e.g. SignalGenerator) - the
    class exists and is unit-tested as part of this engine's complete
    scope, ready for a future, separately-approved cutover.
    """

    def __init__(self):
        self._last_seen_timestamp: Dict[str, pd.Timestamp] = {}

    def _key(self, brk: StructureBreak) -> str:
        return f"{brk.scope.value}:{brk.type}:{brk.direction}"

    def is_new(self, brk: StructureBreak) -> bool:
        """
        Returns True if this exact break (identified by its own
        first-break timestamp, not "now") has not already been observed
        for this scope/type/direction combination. Updates internal state
        as a side effect - call once per real observation.
        """
        key = self._key(brk)
        previous = self._last_seen_timestamp.get(key)
        is_new = previous is None or brk.timestamp != previous
        self._last_seen_timestamp[key] = brk.timestamp
        return is_new
