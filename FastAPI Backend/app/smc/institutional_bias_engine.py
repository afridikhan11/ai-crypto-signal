"""
Institutional Bias Engine - a single directional bias derived ONLY from
higher-timeframe ICT structure.

STATUS (2026-07-29): PRODUCTION. New module - final phase of the
institutional ICT migration. Never reads, imports, or computes EMA, RSI,
MACD, Supertrend, Bollinger Bands, or any other retail indicator -
confirmed by this file's own import list, which touches nothing outside
`app.smc.htf_structure_engine`.

WHAT FEEDS INSTITUTIONAL BIAS (per this phase's explicit requirement list)
--------------------------------------------------------------------------
  - Weekly Structure   -> `HTFSnapshot.get("1w").structure_alignment`
  - Daily Structure    -> `HTFSnapshot.get("1d").structure_alignment`
  - 4H Structure       -> `HTFSnapshot.get("4h").structure_alignment`
  - Protected High/Low -> exposed on `InstitutionalBias` for the caller's
    own Invalidation evidence (this engine does not act on them directly -
    that is Entry Validation's job, see `entry_validation_engine.py`)
  - Major Liquidity / Major Order Blocks / Major Supply-Demand
                        -> optional caller-supplied lists (e.g. detected on
                           the Daily/4H dataframe the same way the primary
                           15m ones already are) - simple presence/count
                           confluence, not a duplicate scoring system
  - Premium/Discount    -> the same `SupplyDemandEngine.get_zone()` read
                           already used elsewhere in this project

1H structure is deliberately EXCLUDED from Institutional Bias itself (it
is still available on the `HTFSnapshot` for the `htf_alignment` AI-scoring
category, which legitimately wants a faster-reacting timeframe) - ICT
"Institutional Bias" is a top-down, HIGHER-timeframe concept; folding in
1H would dilute it toward the same noisy timeframe entry timing already
uses, defeating the point of having a separate, slower-moving bias read.

METHOD
--------------------------------------------------------------------------
Each supplied timeframe's `structure_alignment` contributes its
timeframe's weight (Weekly heaviest) toward "bullish" or "bearish"; a
timeframe reporting "conflicted"/"insufficient_data" contributes nothing
either way (honest, matches the weighted-abstention pattern already used
elsewhere in this project, e.g. the old multi_tf_alignment's neutral
handling). Premium/Discount adds a smaller tilt in the same direction ICT
teaching already assigns it (discount = bullish-leaning, premium =
bearish-leaning). The final label is "bullish"/"bearish" when one side
clearly leads, "conflicted" when both sides have real, comparable weight,
or "neutral" when nothing at all was supplied.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.smc.htf_structure_engine import HTFSnapshot

# Weekly heaviest ("macro bias"), Daily next, 4H least - standard ICT
# top-down weighting. 1H is intentionally not a member of this dict - see
# module docstring.
DEFAULT_TIMEFRAME_WEIGHTS: Dict[str, float] = {"1w": 1.5, "1d": 1.2, "4h": 1.0}

# Premium/Discount's own tilt weight, relative to the structure weights
# above - deliberately smaller than any single structure timeframe's
# weight, since it is one read (current range position), not a trend.
PREMIUM_DISCOUNT_WEIGHT = 0.5

# If bullish and bearish weight are both present and within this fraction
# of the total contributing weight, the picture is genuinely mixed -
# labeled "conflicted" rather than arbitrarily picking whichever side is
# marginally larger.
CONFLICT_BAND_FRACTION = 0.3


@dataclass(frozen=True)
class InstitutionalBias:
    direction: str                       # "bullish" | "bearish" | "neutral" | "conflicted"
    confluence_count: int                # count of major (HTF) Liquidity/OB/Zone evidence supplied
    supporting_evidence: List[str] = field(default_factory=list)
    weekly_alignment: Optional[str] = None
    daily_alignment: Optional[str] = None
    h4_alignment: Optional[str] = None
    protected_high: Optional[float] = None   # from Daily, when available - for the caller's own Invalidation evidence
    protected_low: Optional[float] = None


class InstitutionalBiasEngine:
    """Stateless. `assess()` called twice with the same inputs returns an
    equivalent `InstitutionalBias`."""

    def __init__(self, timeframe_weights: Optional[Dict[str, float]] = None) -> None:
        self.timeframe_weights = dict(timeframe_weights) if timeframe_weights else dict(DEFAULT_TIMEFRAME_WEIGHTS)

    def assess(
        self,
        htf_snapshot: Optional[HTFSnapshot],
        premium_discount_zone: Optional[str] = None,
        major_liquidity: Optional[List] = None,
        major_order_blocks: Optional[List] = None,
        major_zones: Optional[List] = None,
    ) -> InstitutionalBias:
        if htf_snapshot is None or htf_snapshot.is_empty():
            return InstitutionalBias(
                direction="neutral", confluence_count=0,
                supporting_evidence=["No higher-timeframe structure data supplied - bias unavailable."],
            )

        bull_weight = bear_weight = total_weight = 0.0
        evidence: List[str] = []
        alignments: Dict[str, Optional[str]] = {}

        for tf, weight in self.timeframe_weights.items():
            ts = htf_snapshot.get(tf)
            alignments[tf] = ts.structure_alignment if ts else None
            if ts is None:
                continue
            if ts.structure_alignment == "aligned_bullish":
                bull_weight += weight
                total_weight += weight
                evidence.append(f"{tf.upper()} structure aligned bullish")
            elif ts.structure_alignment == "aligned_bearish":
                bear_weight += weight
                total_weight += weight
                evidence.append(f"{tf.upper()} structure aligned bearish")
            else:
                total_weight += weight  # counted for total (abstains cleanly), no directional credit

        if premium_discount_zone == "discount":
            bull_weight += PREMIUM_DISCOUNT_WEIGHT
            total_weight += PREMIUM_DISCOUNT_WEIGHT
            evidence.append("Price in HTF discount (bullish-leaning)")
        elif premium_discount_zone == "premium":
            bear_weight += PREMIUM_DISCOUNT_WEIGHT
            total_weight += PREMIUM_DISCOUNT_WEIGHT
            evidence.append("Price in HTF premium (bearish-leaning)")

        confluence_count = 0
        if major_liquidity:
            swept = sum(1 for lvl in major_liquidity if getattr(lvl, "swept", False))
            confluence_count += swept
            if swept:
                evidence.append(f"{swept} major liquidity level(s) swept")
        if major_order_blocks:
            confluence_count += len(major_order_blocks)
            evidence.append(f"{len(major_order_blocks)} major order block(s) present")
        if major_zones:
            confluence_count += len(major_zones)
            evidence.append(f"{len(major_zones)} major supply/demand zone(s) present")

        if total_weight <= 0:
            direction = "neutral"
        elif bull_weight > 0 and bear_weight > 0 and abs(bull_weight - bear_weight) < CONFLICT_BAND_FRACTION * total_weight:
            direction = "conflicted"
        elif bull_weight > bear_weight:
            direction = "bullish"
        elif bear_weight > bull_weight:
            direction = "bearish"
        else:
            direction = "neutral"

        if not evidence:
            evidence.append("No higher-timeframe structure alignment available (all supplied timeframes conflicted or had insufficient data).")

        daily_ts = htf_snapshot.get("1d")

        return InstitutionalBias(
            direction=direction,
            confluence_count=confluence_count,
            supporting_evidence=evidence,
            weekly_alignment=alignments.get("1w"),
            daily_alignment=alignments.get("1d"),
            h4_alignment=alignments.get("4h"),
            protected_high=daily_ts.protected_high if daily_ts else None,
            protected_low=daily_ts.protected_low if daily_ts else None,
        )
