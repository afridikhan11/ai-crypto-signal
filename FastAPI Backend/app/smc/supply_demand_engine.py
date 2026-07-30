"""
Supply & Demand Engine - unified ICT-standard Supply/Demand zone detection,
lifecycle tracking, and Premium/Discount range classification.

STATUS (2026-07-28): PRODUCTION. Replaces `app/smc/supply_demand.py`
(deleted as part of this cutover - see SUPPLY_DEMAND_ENGINE_
IMPLEMENTATION_REPORT.md for the full before/after, files changed, and
verification). This is now the ONLY Supply & Demand implementation in the
project.

WHAT ICT SUPPLY & DEMAND ACTUALLY IS
--------------------------------------------------------------------------
A Demand zone is the last area of consolidation ("base") immediately
before price displaces up hard - the footprint of the last resting buy
orders before smart money pushed price away. A Supply zone is the mirror:
the last base immediately before price displaces down hard. This is the
SAME underlying idea as an ICT Order Block, generalized from a single
candle to a small BASE of one or more low-range consolidation candles -
a tight base is effectively a 1-candle zone (an Order Block), while a
wider base spanning a few candles is a proper Supply/Demand zone. Like an
Order Block, a real zone requires genuine DISPLACEMENT away from the base
(not just any small move), and is strengthened by causing a real
structural break (BOS/CHoCH) and by confluence with an Order Block,
Liquidity pool, or Fair Value Gap in the same area.

Separately, ICT also teaches "Premium/Discount" - classifying where
CURRENT price sits within a recent dealing range (below the 38.2%
retracement = discount / a good place to look for longs, above 61.8% =
premium / a good place to look for shorts, in between = equilibrium).
This is a distinct concept from Supply/Demand zones themselves (it is a
single range-position read, not a zone with its own lifecycle), but this
project's AI Scoring (`app/ai/scorer.py`, out of scope for this
migration) has always consumed exactly this Premium/Discount read under
the key `supply_demand_zone`/`zone_position` - see "BACKWARD-COMPATIBLE
CONTRACT" below.

WHAT WAS WRONG WITH THE OLD IMPLEMENTATION, WITH EVIDENCE
--------------------------------------------------------------------------
Direct reading of the old `app/smc/supply_demand.py` (17 lines) found it
implemented ONLY the Premium/Discount range read - it never detected an
actual Supply or Demand ZONE at all:
  - `calculate_recent_range()` just took the max high / min low of the
    last 50 candles as `range_high`/`range_low` - a single flat range, not
    a base-and-displacement zone.
  - `app/services/ta_dashboard.py`'s "Supply Zones"/"Demand Zones" report
    cards were built directly from this SAME single `range_high`/
    `range_low` pair - i.e. the dashboard's "Supply Zone" was just "the
    highest high of the last 50 candles" and "Demand Zone" was "the
    lowest low of the last 50 candles". There was exactly one supply
    reference point and one demand reference point per call, no list of
    distinct zones, no lifecycle, no strength, no relationship to
    anything else.
  - No Fresh/Tested/Mitigated/Broken lifecycle - `range_high`/`range_low`
    are recomputed from scratch every call with no memory of whether
    price had already traded back into that area.
  - No connection whatsoever to Market Structure, Order Blocks,
    Liquidity, or FVGs.
  - No Multi-Timeframe tagging.

ICT CONCEPTS IMPLEMENTED HERE
--------------------------------------------------------------------------
 1. Demand Zones           - `find_demand_zones()`: base + genuine bullish
                              displacement away from it.
 2. Supply Zones           - `find_supply_zones()`: mirror, bearish
                              displacement.
 3. Fresh Zones            - `Zone.state == ZoneState.FRESH`: never
                              revisited since formation.
 4. Tested Zones           - `ZoneState.TESTED`: price wicked into the
                              zone but CLOSED back outside it - the zone
                              was touched and respected, not consumed.
 5. Mitigated Zones        - `ZoneState.MITIGATED`: a candle CLOSED inside
                              the zone - some of the resting orders there
                              have genuinely been consumed.
 6. Broken Zones           - `ZoneState.BROKEN`: a candle closed fully
                              through the FAR side of the zone - the zone
                              is invalidated, no longer valid supply/
                              demand. Terminal state.
 7. Zone Strength          - `Zone.strength` (STRONG/MODERATE/WEAK): a
                              simple, documented combination of
                              displacement magnitude and BOS/CHoCH
                              causation - see `_classify_strength()`.
 8. Zone Freshness         - IS `Zone.state` - not a separate field. A
                              zone's "freshness" is exactly its lifecycle
                              position (FRESH is fully fresh, TESTED/
                              MITIGATED are progressively less fresh,
                              BROKEN is dead). Same "don't invent a second
                              concept for the same thing" pattern used for
                              Liquidity Engine's "Liquidity Pools".
 9. Zone Invalidations     - `Zone.state == ZoneState.BROKEN` +
                              `Zone.broken_at`: the exact candle where the
                              zone was invalidated, from a real forward
                              scan (not a single-price snapshot).
10. BOS relationship       - `Zone.caused_bos` + `related_break_level`,
                              set when a caller-supplied `structure_breaks`
                              list (from MarketStructureEngine) contains a
                              matching-direction BOS shortly after the
                              zone's displacement leg.
11. CHoCH relationship     - `Zone.caused_choch`, same mechanism.
12. Order Block relationship - `Zone.has_ob_confluence`: a caller-supplied
                              Order Block (from OrderBlockEngine) overlaps
                              this zone's price range.
13. Liquidity relationship - `Zone.has_liquidity_confluence`: a
                              caller-supplied Liquidity level (from
                              LiquidityEngine) sits inside this zone.
14. FVG relationship       - `Zone.has_fvg_confluence`: a caller-supplied
                              Fair Value Gap overlaps this zone.
15. Multi-Timeframe compatibility - timeframe-agnostic by construction;
                              every `Zone` carries an optional `timeframe`
                              tag, same pattern as the other three engines.

BACKWARD-COMPATIBLE CONTRACT (Premium/Discount, unchanged)
--------------------------------------------------------------------------
`app/ai/scorer.py`'s `supply_demand_zone` scoring block (out of scope for
this migration - DO NOT MODIFY) reads exactly two things: a `zone` string
("premium"/"discount"/"equilibrium"/"unknown") and an optional
`zone_position` float (0=range low, 1=range high). `calculate_recent_range
()` and `get_zone()` below reproduce the OLD engine's exact formula
(same 50-candle lookback default, same 38.2%/61.8% fibonacci split) byte-
for-byte, so every caller's existing `zone`/`zone_position` computation -
and therefore AIScorer's actual scoring behavior - is UNCHANGED by this
migration. The new zone-detection methods above are pure additions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

import pandas as pd


class ZoneType(Enum):
    DEMAND = "demand"
    SUPPLY = "supply"


class ZoneState(Enum):
    FRESH = "fresh"
    TESTED = "tested"
    MITIGATED = "mitigated"
    BROKEN = "broken"


class ZoneStrength(Enum):
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"


_STATE_RANK = {ZoneState.FRESH: 0, ZoneState.TESTED: 1, ZoneState.MITIGATED: 2, ZoneState.BROKEN: 3}

DEFAULT_ATR_LOOKBACK = 14
DEFAULT_MAX_BASE_CANDLES = 3
DEFAULT_BASE_BODY_ATR_RATIO = 0.5
DEFAULT_MIN_DISPLACEMENT_ATR = 1.5
DEFAULT_DISPLACEMENT_WINDOW = 3
DEFAULT_RELATED_EVENT_LOOKAHEAD = 20


@dataclass
class Zone:
    top: float
    bottom: float
    type: ZoneType
    origin_index: int
    origin_timestamp: pd.Timestamp
    displacement_ratio: float
    state: ZoneState = ZoneState.FRESH
    strength: ZoneStrength = ZoneStrength.WEAK
    tested_at: Optional[pd.Timestamp] = None
    mitigated_at: Optional[pd.Timestamp] = None
    broken_at: Optional[pd.Timestamp] = None
    caused_bos: bool = False
    caused_choch: bool = False
    related_break_level: Optional[float] = None
    has_ob_confluence: bool = False
    has_liquidity_confluence: bool = False
    has_fvg_confluence: bool = False
    timeframe: Optional[str] = None


class SupplyDemandEngine:
    """
    Stateless, single-window Supply & Demand analysis - calling it twice
    with the same inputs returns identical results, the same design
    philosophy as the other three ICT engines in this project.
    `structure_breaks`/`order_blocks`/`liquidity_levels`/`fvgs` are all
    OPTIONAL: when a caller doesn't supply one, the related field on every
    `Zone` stays at its honest default (`False`/`None`) rather than being
    guessed.
    """

    def __init__(
        self,
        df: pd.DataFrame,
        structure_breaks: Optional[List] = None,
        order_blocks: Optional[List] = None,
        liquidity_levels: Optional[List] = None,
        fvgs: Optional[List] = None,
        atr_lookback: int = DEFAULT_ATR_LOOKBACK,
        max_base_candles: int = DEFAULT_MAX_BASE_CANDLES,
        base_body_atr_ratio: float = DEFAULT_BASE_BODY_ATR_RATIO,
        min_displacement_atr: float = DEFAULT_MIN_DISPLACEMENT_ATR,
        displacement_window: int = DEFAULT_DISPLACEMENT_WINDOW,
        related_event_lookahead: int = DEFAULT_RELATED_EVENT_LOOKAHEAD,
        timeframe: Optional[str] = None,
    ):
        self.df = df
        self.structure_breaks = structure_breaks or []
        self.order_blocks = order_blocks or []
        self.liquidity_levels = liquidity_levels or []
        self.fvgs = fvgs or []
        self.atr_lookback = atr_lookback
        self.max_base_candles = max_base_candles
        self.base_body_atr_ratio = base_body_atr_ratio
        self.min_displacement_atr = min_displacement_atr
        self.displacement_window = displacement_window
        self.related_event_lookahead = related_event_lookahead
        self.timeframe = timeframe

        # Premium/Discount state (backward-compatible contract - see
        # module docstring). Mirrors the old engine's exact attribute
        # names so existing callers barely change.
        self.range_high: Optional[float] = None
        self.range_low: Optional[float] = None

    # ------------------------------------------------------------------
    # BACKWARD-COMPATIBLE Premium/Discount (byte-for-byte identical
    # formula to the deleted app/smc/supply_demand.py).
    # ------------------------------------------------------------------
    def calculate_recent_range(self, lookback: int = 50):
        if len(self.df) < lookback:
            return
        recent = self.df.iloc[-lookback:]
        self.range_high = recent["high"].max()
        self.range_low = recent["low"].min()

    def get_zone(self, price: float) -> str:
        """Identical formula to the old engine: symmetric golden-ratio
        split, discount below the 0.382 retracement, premium above the
        0.618 retracement, equilibrium in between. See app/ai/scorer.py's
        `supply_demand_zone` block for how AIScorer consumes this - that
        formula is unchanged."""
        if self.range_high is None or self.range_low is None:
            return "unknown"
        range_size = self.range_high - self.range_low
        if range_size == 0:
            return "equilibrium"
        fib_0_382 = self.range_low + 0.382 * range_size
        fib_0_618 = self.range_low + 0.618 * range_size
        if price >= fib_0_618:
            return "premium"
        elif price <= fib_0_382:
            return "discount"
        else:
            return "equilibrium"

    # ------------------------------------------------------------------
    # NEW: real ICT Supply/Demand zone detection
    # ------------------------------------------------------------------
    def _local_atr_at(self, idx: int) -> float:
        """Point-in-time ATR estimate using only candles BEFORE `idx` (no
        lookahead) - the same lightweight high-low-range ATR definition
        MarketStructureEngine/OrderBlockEngine use, evaluated at a
        specific historical point rather than only at the end of the
        window, since zone detection scans the whole series."""
        start = max(0, idx - self.atr_lookback)
        window = self.df.iloc[start:idx]
        if len(window) == 0:
            return 0.0
        return float((window["high"] - window["low"]).mean())

    def _find_related_break(self, zone_type: ZoneType, origin_idx: int):
        """A Demand zone's displacement leg is bullish, so the reversal-
        confirming structure break it causes (if any) is also bullish;
        a Supply zone's leg is bearish. Same shape as OrderBlockEngine's
        `_find_related_break()`."""
        if not self.structure_breaks or origin_idx >= len(self.df):
            return None
        wanted_direction = "bullish" if zone_type == ZoneType.DEMAND else "bearish"
        origin_ts = self.df.index[origin_idx]
        window_end_ts = self.df.index[min(origin_idx + self.related_event_lookahead, len(self.df) - 1)]
        candidates = [
            b for b in self.structure_breaks
            if b.direction == wanted_direction and origin_ts <= b.timestamp <= window_end_ts
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda b: b.timestamp)

    def _has_ob_confluence(self, top: float, bottom: float) -> bool:
        return any(ob.low <= top and bottom <= ob.high for ob in self.order_blocks)

    def _has_liquidity_confluence(self, top: float, bottom: float) -> bool:
        return any(bottom <= lvl.price <= top for lvl in self.liquidity_levels)

    def _has_fvg_confluence(self, top: float, bottom: float) -> bool:
        return any(f.bottom <= top and bottom <= f.top for f in self.fvgs)

    @staticmethod
    def _classify_strength(displacement_ratio: float, caused_bos: bool, caused_choch: bool) -> ZoneStrength:
        """Simple, documented, deterministic combination of displacement
        magnitude and structural causation - same "explainable arithmetic"
        philosophy as OrderBlockEngine._classify_strength(), not a
        black-box score."""
        score = 0
        if displacement_ratio >= 3.0:
            score += 2
        elif displacement_ratio >= 1.5:
            score += 1
        if caused_choch:
            score += 2
        elif caused_bos:
            score += 1
        if score >= 3:
            return ZoneStrength.STRONG
        if score >= 1:
            return ZoneStrength.MODERATE
        return ZoneStrength.WEAK

    def _lifecycle(self, zone_type: ZoneType, top: float, bottom: float, origin_idx: int):
        """Real forward scan of the ENTIRE window after the zone's origin
        candle (not a single-price snapshot) - same evidence-based
        pattern OrderBlockEngine and LiquidityEngine both use for their
        own lifecycle/sweep tracking. Progresses monotonically:
        FRESH -> TESTED -> MITIGATED -> BROKEN (BROKEN is terminal)."""
        state = ZoneState.FRESH
        tested_at = mitigated_at = broken_at = None
        n = len(self.df)
        highs = self.df["high"].values
        lows = self.df["low"].values
        closes = self.df["close"].values

        for i in range(origin_idx + 1, n):
            high, low, close = highs[i], lows[i], closes[i]
            touched = low <= top and high >= bottom  # candle's range overlaps the zone at all
            if not touched:
                continue

            if zone_type == ZoneType.DEMAND:
                # BROKEN: closed fully below the zone (through the far/
                # bottom side) - the demand failed to hold.
                if close < bottom:
                    broken_at = self.df.index[i]
                    state = ZoneState.BROKEN
                    break
                # MITIGATED: closed back inside the zone - orders consumed.
                if close <= top:
                    if _STATE_RANK[ZoneState.MITIGATED] > _STATE_RANK[state]:
                        mitigated_at = self.df.index[i]
                        state = ZoneState.MITIGATED
                    continue
                # TESTED: wicked in (touched==True) but closed back above
                # the zone entirely - respected, not consumed.
                if _STATE_RANK[ZoneState.TESTED] > _STATE_RANK[state]:
                    tested_at = self.df.index[i]
                    state = ZoneState.TESTED
            else:  # SUPPLY
                if close > top:
                    broken_at = self.df.index[i]
                    state = ZoneState.BROKEN
                    break
                if close >= bottom:
                    if _STATE_RANK[ZoneState.MITIGATED] > _STATE_RANK[state]:
                        mitigated_at = self.df.index[i]
                        state = ZoneState.MITIGATED
                    continue
                if _STATE_RANK[ZoneState.TESTED] > _STATE_RANK[state]:
                    tested_at = self.df.index[i]
                    state = ZoneState.TESTED

        return state, tested_at, mitigated_at, broken_at

    def _build_zone(self, zone_type: ZoneType, base_start: int, base_end: int, top: float, bottom: float,
                     displacement_ratio: float) -> Zone:
        related_break = self._find_related_break(zone_type, base_end)
        caused_bos = related_break is not None and related_break.type == "BOS"
        caused_choch = related_break is not None and related_break.type == "CHoCH"
        state, tested_at, mitigated_at, broken_at = self._lifecycle(zone_type, top, bottom, base_end)
        return Zone(
            top=top, bottom=bottom, type=zone_type,
            origin_index=base_end, origin_timestamp=self.df.index[base_end],
            displacement_ratio=displacement_ratio,
            state=state, strength=self._classify_strength(displacement_ratio, caused_bos, caused_choch),
            tested_at=tested_at, mitigated_at=mitigated_at, broken_at=broken_at,
            caused_bos=caused_bos, caused_choch=caused_choch,
            related_break_level=(related_break.level if related_break else None),
            has_ob_confluence=self._has_ob_confluence(top, bottom),
            has_liquidity_confluence=self._has_liquidity_confluence(top, bottom),
            has_fvg_confluence=self._has_fvg_confluence(top, bottom),
            timeframe=self.timeframe,
        )

    def _find_zones(self, zone_type: ZoneType) -> List[Zone]:
        zones: List[Zone] = []
        n = len(self.df)
        highs = self.df["high"]
        lows = self.df["low"]
        i = 1
        while i < n - 1:
            found = False
            # Try the LONGEST candidate base first (descending base_len),
            # not the shortest. Combined with the "confirming candle is
            # not itself base-like" guard below, this makes the scanner
            # anchor on the TRUE end of a consolidation run and capture
            # its full width - rather than opportunistically registering
            # a 1-candle "base" using only the FIRST candle of a longer
            # consolidation, whose own "displacement window" would then
            # still contain later consolidation candles. That bug was
            # caught empirically during regression testing: a clean,
            # never-revisited 5x-ATR displacement was being reported as
            # immediately MITIGATED, because the lifecycle scan's first
            # post-formation candle was actually still a base candle
            # (borrowed into the peeking displacement window), not real
            # displacement - see sd_verification/probe_lifecycle_bug.py.
            for base_len in range(self.max_base_candles, 0, -1):
                base_start = i - base_len + 1
                if base_start < 0:
                    continue
                atr = self._local_atr_at(base_start)
                if atr <= 0:
                    continue
                base_ranges = (highs.iloc[base_start:i + 1] - lows.iloc[base_start:i + 1])
                if not (base_ranges <= self.base_body_atr_ratio * atr).all():
                    continue

                disp_start = i + 1
                disp_end = min(i + 1 + self.displacement_window, n)
                if disp_start >= disp_end:
                    continue

                # GUARD: the candle immediately after the candidate base
                # must NOT itself be base-like (tight-range). If it is,
                # `i` is still inside a longer consolidation run, not yet
                # at the true base/displacement boundary - reject this
                # candidate entirely rather than letting a peeking
                # multi-candle displacement window "borrow" it as if it
                # were genuine displacement.
                confirm_range = float(highs.iloc[disp_start] - lows.iloc[disp_start])
                if confirm_range <= self.base_body_atr_ratio * atr:
                    continue

                base_top = float(highs.iloc[base_start:i + 1].max())
                base_bottom = float(lows.iloc[base_start:i + 1].min())

                disp_slice = self.df.iloc[disp_start:disp_end]
                up_move = (float(disp_slice["high"].max()) - base_top) / atr
                down_move = (base_bottom - float(disp_slice["low"].min())) / atr

                if zone_type == ZoneType.DEMAND and up_move >= self.min_displacement_atr and up_move > down_move:
                    zones.append(self._build_zone(ZoneType.DEMAND, base_start, i, base_top, base_bottom, up_move))
                    i = disp_end
                    found = True
                    break
                if zone_type == ZoneType.SUPPLY and down_move >= self.min_displacement_atr and down_move > up_move:
                    zones.append(self._build_zone(ZoneType.SUPPLY, base_start, i, base_top, base_bottom, down_move))
                    i = disp_end
                    found = True
                    break
            if not found:
                i += 1
        return zones

    def find_demand_zones(self) -> List[Zone]:
        return self._find_zones(ZoneType.DEMAND)

    def find_supply_zones(self) -> List[Zone]:
        return self._find_zones(ZoneType.SUPPLY)
