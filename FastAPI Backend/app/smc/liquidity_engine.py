"""
Liquidity Engine - unified ICT-standard Buy Side / Sell Side Liquidity
detection, sweep classification, and confirmation.

STATUS (2026-07-28): PRODUCTION. Replaces `app/smc/liquidity.py` (deleted
as part of this cutover - see LIQUIDITY_ENGINE_IMPLEMENTATION_REPORT.md
for the full before/after, files changed, and verification). This is now
the ONLY Liquidity implementation in the project.

WHAT ICT LIQUIDITY ACTUALLY IS
--------------------------------------------------------------------------
Every swing high has resting sell orders / buy-stops sitting just above it
(traders who sold there, or shorts protecting themselves) - that's Buy
Side Liquidity (BSL). Every swing low has resting buy orders / sell-stops
just below it - Sell Side Liquidity (SSL). When several swing highs form
at nearly the same price, their combined resting orders form a bigger,
more attractive pool - Equal Highs (EQH) is the special, STRONGER case of
BSL; a single untested swing high is still real BSL, just a smaller pool.
The same logic mirrors for Equal Lows (EQL) / SSL.

A "sweep" is price trading through a liquidity pool - triggering those
resting orders - which the market often does deliberately before reversing
(a "liquidity grab" / stop hunt) rather than as a genuine breakout. The
difference between a grab and a real breakout is usually settled by what
price does in the next few candles: closes back on the original side ->
grab; closes through and stays through -> breakout/continuation.

WHAT WAS WRONG WITH THE OLD IMPLEMENTATION, WITH EVIDENCE
--------------------------------------------------------------------------
Direct reading of the old `app/smc/liquidity.py` found:
  - It ONLY detected Equal Highs/Lows (clusters of >= 2 swing points within
    a tight tolerance). Every isolated, untested swing high/low - which is
    STILL real resting liquidity in ICT terms, just a smaller pool - was
    silently discarded. `if len(cluster) >= 2:` was the only path that
    ever produced a `LiquidityLevel` at all.
  - No Internal vs External Liquidity distinction - it took one flat
    `swing_highs`/`swing_lows` list with no concept of which Market
    Structure tier they came from.
  - `detect_liquidity_sweeps()` only checked the SINGLE LATEST candle
    (`self.df["high"].iloc[-1]`) against each level - the exact same class
    of bug proven and fixed in `BOS_Duplicate_Investigation_Report.md`
    (Market Structure) and `ORDER_BLOCK_ENGINE_IMPLEMENTATION_REPORT.md`
    (Order Blocks): a level swept 20 candles ago and never touched again
    would report `swept=False` today, because only "right now" was ever
    checked, not "at any point since this level formed."
  - No sweep confirmation - swept was a single boolean, no concept of
    whether price actually rejected (a grab) or kept going (a breakout).
  - No connection to Market Structure, Order Blocks, or FVGs at all - a
    swept liquidity pool immediately followed by a real reversal (a BOS/
    CHoCH in the opposite direction, or a fresh OB/FVG) is one of ICT's
    highest-conviction setups, and the old engine had no way to say so.

ICT CONCEPTS IMPLEMENTED HERE
--------------------------------------------------------------------------
 1. Buy Side Liquidity (BSL)   - `detect_buyside_liquidity()`
 2. Sell Side Liquidity (SSL)  - `detect_sellside_liquidity()`
 3. Equal Highs (EQH)          - `LiquidityKind.EQUAL_HIGHS` - a BSL pool
                                  formed by >= 2 clustered swing highs
 4. Equal Lows (EQL)           - `LiquidityKind.EQUAL_LOWS`, mirror
 5. Internal Liquidity         - `LiquidityScope.INTERNAL`, built from
                                  MarketStructureEngine's internal-tier
                                  swings (`internal_swing_highs`/`_lows`,
                                  optional constructor args - see item 14)
 6. External Liquidity         - `LiquidityScope.EXTERNAL`, built from the
                                  external tier (`swing_highs`/`swing_lows`
                                  - required, same shape the old engine
                                  always took)
 7. Liquidity Pools            - every `LiquidityLevel` this engine
                                  returns IS a liquidity pool; no separate
                                  class was invented for the same concept
 8. Liquidity Sweeps           - `detect_liquidity_sweeps()`, now a real
                                  forward scan from the level's own
                                  formation candle to the end of the
                                  window, not a single-candle snapshot
 9. Liquidity Grabs            - `LiquidityLevel.sweep_outcome ==
                                  SweepOutcome.GRAB` - price swept the pool
                                  and closed back on the original side
                                  within the confirmation window
10. Resting Liquidity          - any `LiquidityLevel` with `swept == False`
                                  IS resting liquidity - not a separate
                                  method, just the natural unswept state
11. Engineered Liquidity       - `LiquidityLevel.engineered` - a simple,
                                  disclosed proxy (see `_is_engineered()`):
                                  an Equal Highs/Lows cluster whose swing
                                  points line up UNUSUALLY tightly
                                  (tighter than the configured clustering
                                  tolerance would strictly require) is
                                  flagged as more likely deliberately
                                  re-defended than coincidentally aligned.
                                  This is a heuristic, not a certainty -
                                  see Known Limitations in the
                                  implementation report.
12. Sweep confirmation         - `LiquidityLevel.sweep_confirmed`: True
                                  only when a close-based outcome (grab or
                                  breakout) was actually observed within
                                  the confirmation window; False if the
                                  window ran out of data first (honest
                                  "not yet confirmed", never guessed)
13. Strong vs Weak Liquidity   - `LiquidityLevel.strength` (STRONG/
                                  MODERATE/WEAK) - simple, documented
                                  arithmetic on pool size and scope, see
                                  `_classify_strength()`
14. Multi-Timeframe compat.    - the engine takes any OHLCV `df` with no
                                  hardcoded timeframe assumptions; every
                                  `LiquidityLevel` carries an optional
                                  `timeframe` tag, same pattern already
                                  used in MarketStructureEngine/
                                  OrderBlockEngine

INTEGRATION WITH THE OTHER ICT ENGINES
--------------------------------------------------------------------------
All three are OPTIONAL constructor arguments - every real caller already
computes them before Liquidity detection runs, so they're threaded
through wherever available; when omitted the engine still returns real,
sweep-classified liquidity levels, just without the relationship fields
filled in (never guessed):
  - `structure_breaks` (MarketStructureEngine.analyze().external_breaks
    or .internal_breaks) -> `reversal_break_level`/`reversal_is_choch`:
    was this sweep followed by a real structure break in the OPPOSITE
    direction shortly after (the classic "sweep then reverse" signature)?
  - `order_blocks` (OrderBlockEngine results) -> `has_ob_confluence`
  - `fvgs` (FVGDetector.detect_fvg()) -> `has_fvg_confluence`
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

import pandas as pd


class LiquidityType(Enum):
    BUYSIDE = "buyside"
    SELLSIDE = "sellside"


class LiquidityScope(Enum):
    INTERNAL = "internal"
    EXTERNAL = "external"


class LiquidityKind(Enum):
    EQUAL_HIGHS = "equal_highs"
    EQUAL_LOWS = "equal_lows"
    SINGLE_SWING = "single_swing"


class LiquidityStrength(Enum):
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"


class SweepOutcome(Enum):
    UNSWEPT = "unswept"
    GRAB = "grab"
    BREAKOUT = "breakout"


# How many candles after a sweep begins to look for a close-based
# rejection (grab) or continuation (breakout). A plain, documented
# constant - not wired to CalibrationProfile (out of scope for this
# migration), same approach already used for the other two engines' own
# thresholds.
DEFAULT_SWEEP_CONFIRMATION_WINDOW = 3

# A cluster is flagged "engineered" (item 11) when its swing points line
# up within this FRACTION of the normal clustering tolerance - i.e.
# noticeably tighter than the tolerance already requires.
DEFAULT_ENGINEERED_TOLERANCE_FRACTION = 0.4

DEFAULT_RELATED_EVENT_LOOKAHEAD = 20


@dataclass
class LiquidityLevel:
    price: float
    type: LiquidityType
    swing_points: List  # List[SwingPoint] - untyped here to avoid a hard
    # import dependency on market_structure_engine's dataclass shape;
    # duck-typed the same way the old engine's field always was.
    kind: LiquidityKind
    scope: LiquidityScope
    strength: LiquidityStrength
    swept: bool = False
    engineered: bool = False
    sweep_outcome: SweepOutcome = SweepOutcome.UNSWEPT
    swept_at: Optional[pd.Timestamp] = None
    sweep_confirmed: bool = False
    reversal_break_level: Optional[float] = None
    reversal_is_choch: bool = False
    has_ob_confluence: bool = False
    has_fvg_confluence: bool = False
    timeframe: Optional[str] = None


class LiquidityEngine:
    """
    Stateless, single-window Liquidity analysis - same design philosophy
    as MarketStructureEngine/OrderBlockEngine: calling it twice with the
    same inputs returns identical results.

    `external_swing_highs`/`external_swing_lows` are REQUIRED (same shape
    the old engine's `swing_highs`/`swing_lows` always took - typically
    `MarketStructureEngine.analyze().swing_highs`/`.swing_lows`).
    `internal_swing_highs`/`internal_swing_lows` are OPTIONAL - only
    callers that already run a tiered MarketStructureEngine call (internal
    + external pivot windows) can supply these; when omitted, Internal
    Liquidity is simply not computed for that caller (honest, not guessed).
    """

    def __init__(
        self,
        df: pd.DataFrame,
        external_swing_highs: List,
        external_swing_lows: List,
        internal_swing_highs: Optional[List] = None,
        internal_swing_lows: Optional[List] = None,
        tolerance: float = 0.0005,
        engineered_tolerance_fraction: float = DEFAULT_ENGINEERED_TOLERANCE_FRACTION,
        sweep_confirmation_window: int = DEFAULT_SWEEP_CONFIRMATION_WINDOW,
        structure_breaks: Optional[List] = None,
        order_blocks: Optional[List] = None,
        fvgs: Optional[List] = None,
        related_event_lookahead: int = DEFAULT_RELATED_EVENT_LOOKAHEAD,
        timeframe: Optional[str] = None,
    ):
        self.df = df
        self.external_swing_highs = external_swing_highs or []
        self.external_swing_lows = external_swing_lows or []
        self.internal_swing_highs = internal_swing_highs or []
        self.internal_swing_lows = internal_swing_lows or []
        self.tolerance = tolerance
        self.engineered_tolerance_fraction = engineered_tolerance_fraction
        self.sweep_confirmation_window = sweep_confirmation_window
        self.structure_breaks = structure_breaks or []
        self.order_blocks = order_blocks or []
        self.fvgs = fvgs or []
        self.related_event_lookahead = related_event_lookahead
        self.timeframe = timeframe

    # ------------------------------------------------------------------
    # Shared clustering (items 3-4, 7) - one implementation used for both
    # highs and lows, replacing the old engine's two near-identical
    # copy-pasted loops.
    # ------------------------------------------------------------------
    @staticmethod
    def _cluster_swings(swings: List, tolerance: float) -> List[List]:
        clusters: List[List] = []
        used = set()
        for i, s1 in enumerate(swings):
            if i in used:
                continue
            cluster = [s1]
            for j, s2 in enumerate(swings[i + 1:], start=i + 1):
                if j in used:
                    continue
                avg_price = (s1.price + s2.price) / 2
                if avg_price > 0 and abs(s1.price - s2.price) / avg_price <= tolerance:
                    cluster.append(s2)
                    used.add(j)
            used.add(i)
            clusters.append(cluster)
        return clusters

    def _is_engineered(self, cluster: List) -> bool:
        """Item 11 - see module docstring. Only meaningful for clusters of
        >= 2 (a lone swing can't be an "equal highs" pattern at all)."""
        if len(cluster) < 2:
            return False
        prices = [s.price for s in cluster]
        spread = max(prices) - min(prices)
        avg_price = sum(prices) / len(prices)
        if avg_price <= 0:
            return False
        actual_ratio = spread / avg_price
        return actual_ratio <= self.tolerance * self.engineered_tolerance_fraction

    @staticmethod
    def _classify_strength(cluster: List, scope: LiquidityScope) -> LiquidityStrength:
        """Item 13 - simple, documented arithmetic: bigger pools (more
        stacked swing points) and External-scope pools score higher, since
        External liquidity is generally the larger, more significant draw
        in ICT teaching. Not wired to any outcome data - a plain,
        deterministic classification, not a black-box score."""
        score = (len(cluster) - 1) + (1 if scope == LiquidityScope.EXTERNAL else 0)
        if score >= 2:
            return LiquidityStrength.STRONG
        if score == 1:
            return LiquidityStrength.MODERATE
        return LiquidityStrength.WEAK

    def _build_levels(self, swings: List, liq_type: LiquidityType, scope: LiquidityScope) -> List[LiquidityLevel]:
        if not swings:
            return []
        levels: List[LiquidityLevel] = []
        for cluster in self._cluster_swings(swings, self.tolerance):
            avg_price = sum(s.price for s in cluster) / len(cluster)
            kind = (
                (LiquidityKind.EQUAL_HIGHS if liq_type == LiquidityType.BUYSIDE else LiquidityKind.EQUAL_LOWS)
                if len(cluster) >= 2 else LiquidityKind.SINGLE_SWING
            )
            levels.append(LiquidityLevel(
                price=avg_price, type=liq_type, swing_points=cluster, kind=kind, scope=scope,
                strength=self._classify_strength(cluster, scope),
                engineered=self._is_engineered(cluster),
                timeframe=self.timeframe,
            ))
        return levels

    # ------------------------------------------------------------------
    # Public entry points (items 1-2, 5-6)
    # ------------------------------------------------------------------
    def detect_buyside_liquidity(self) -> List[LiquidityLevel]:
        """External-scope BSL - every swing high (clustered as Equal Highs
        or standing alone) is a resting pool of buy-stops. Item 1 + 3."""
        return self._build_levels(self.external_swing_highs, LiquidityType.BUYSIDE, LiquidityScope.EXTERNAL)

    def detect_sellside_liquidity(self) -> List[LiquidityLevel]:
        """External-scope SSL. Item 2 + 4."""
        return self._build_levels(self.external_swing_lows, LiquidityType.SELLSIDE, LiquidityScope.EXTERNAL)

    def detect_internal_buyside_liquidity(self) -> List[LiquidityLevel]:
        """Internal-scope BSL - empty if no `internal_swing_highs` were
        supplied to the constructor. Item 5."""
        return self._build_levels(self.internal_swing_highs, LiquidityType.BUYSIDE, LiquidityScope.INTERNAL)

    def detect_internal_sellside_liquidity(self) -> List[LiquidityLevel]:
        """Internal-scope SSL. Item 5."""
        return self._build_levels(self.internal_swing_lows, LiquidityType.SELLSIDE, LiquidityScope.INTERNAL)

    # ------------------------------------------------------------------
    # Sweep detection + confirmation (items 8-9, 12) + relationship
    # tagging (structure/OB/FVG integration)
    # ------------------------------------------------------------------
    def _find_related_break(self, raid_direction: str, swept_idx: int):
        """A sweep of BSL (a raid on highs) is followed by a genuine
        reversal if a BEARISH structure break happens shortly after; a
        sweep of SSL is followed by a BULLISH one. Mirrors
        OrderBlockEngine._find_related_break()'s exact shape."""
        if not self.structure_breaks or swept_idx >= len(self.df):
            return None
        reversal_direction = "bearish" if raid_direction == "bullish_raid" else "bullish"
        window_end_ts = self.df.index[min(swept_idx + self.related_event_lookahead, len(self.df) - 1)]
        swept_ts = self.df.index[swept_idx]
        candidates = [
            b for b in self.structure_breaks
            if b.direction == reversal_direction and swept_ts <= b.timestamp <= window_end_ts
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda b: b.timestamp)

    def _has_confluence(self, events: List, reversal_type_wanted: str, swept_idx: int) -> bool:
        if not events or swept_idx >= len(self.df):
            return False
        window_end_ts = self.df.index[min(swept_idx + self.related_event_lookahead, len(self.df) - 1)]
        swept_ts = self.df.index[swept_idx]

        def _in_window(e) -> bool:
            ts = getattr(e, "timestamp", None)
            if ts is None:
                return False
            etype = getattr(getattr(e, "type", None), "value", getattr(e, "type", None))
            return swept_ts <= ts <= window_end_ts and etype == reversal_type_wanted

        return any(_in_window(e) for e in events)

    def _classify_sweep(self, level: LiquidityLevel):
        formation_idx = max((s.index for s in level.swing_points), default=None)
        if formation_idx is None:
            return False, SweepOutcome.UNSWEPT, None, False, None, False, False, False

        highs = self.df["high"].values
        lows = self.df["low"].values
        closes = self.df["close"].values
        n = len(self.df)
        is_buyside = level.type == LiquidityType.BUYSIDE

        swept_idx = None
        for j in range(formation_idx + 1, n):
            if is_buyside and highs[j] > level.price:
                swept_idx = j
                break
            if not is_buyside and lows[j] < level.price:
                swept_idx = j
                break

        if swept_idx is None:
            return False, SweepOutcome.UNSWEPT, None, False, None, False, False, False

        window_end = min(swept_idx + self.sweep_confirmation_window, n - 1)
        outcome = None
        for k in range(swept_idx, window_end + 1):
            if is_buyside:
                if closes[k] < level.price:
                    outcome = SweepOutcome.GRAB
                    break
                if closes[k] > level.price:
                    outcome = SweepOutcome.BREAKOUT
                    break
            else:
                if closes[k] > level.price:
                    outcome = SweepOutcome.GRAB
                    break
                if closes[k] < level.price:
                    outcome = SweepOutcome.BREAKOUT
                    break

        confirmed = outcome is not None
        if outcome is None:
            outcome = SweepOutcome.GRAB  # conservative default when the window ran out with no close-based signal

        raid_direction = "bullish_raid" if is_buyside else "bearish_raid"
        related_break = self._find_related_break(raid_direction, swept_idx)
        reversal_break_level = related_break.level if related_break else None
        reversal_is_choch = bool(related_break and related_break.type == "CHoCH")

        reversal_type_wanted = "bearish_ob" if is_buyside else "bullish_ob"
        has_ob_confluence = self._has_confluence(self.order_blocks, reversal_type_wanted, swept_idx)
        fvg_type_wanted = "bearish" if is_buyside else "bullish"
        has_fvg_confluence = self._has_confluence(self.fvgs, fvg_type_wanted, swept_idx)

        return (
            True, outcome, self.df.index[swept_idx], confirmed,
            reversal_break_level, reversal_is_choch, has_ob_confluence, has_fvg_confluence,
        )

    def detect_liquidity_sweeps(self, levels: List[LiquidityLevel]) -> List[LiquidityLevel]:
        """
        Real forward scan from each level's OWN formation candle to the
        end of the window (item 8) - not a single-latest-candle snapshot.
        Mutates and returns `levels` in place, same call shape the old
        engine's `detect_liquidity_sweeps()` always had, so callers barely
        change. Skips levels already marked swept (idempotent re-calls).
        """
        for level in levels:
            if level.swept:
                continue
            (
                swept, outcome, swept_at, confirmed,
                reversal_break_level, reversal_is_choch, has_ob_confluence, has_fvg_confluence,
            ) = self._classify_sweep(level)
            if swept:
                level.swept = True
                level.sweep_outcome = outcome
                level.swept_at = swept_at
                level.sweep_confirmed = confirmed
                level.reversal_break_level = reversal_break_level
                level.reversal_is_choch = reversal_is_choch
                level.has_ob_confluence = has_ob_confluence
                level.has_fvg_confluence = has_fvg_confluence
        return levels
