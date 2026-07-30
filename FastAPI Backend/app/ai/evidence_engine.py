"""
ICT Evidence Engine (Foundation) - collects, formats, and structures ICT
evidence from already-computed engine outputs. Explains WHY a trade
candidate exists; does NOT compute confidence.

STATUS (2026-07-29): PRODUCTION (foundation). Phase C of the institutional
ICT migration (see ICT_ARCHITECTURE_AUDIT_AND_MIGRATION_PLAN.md). This
module is intentionally scoped to COLLECTION ONLY - every builder method
here takes an already-computed object from an existing ICT engine
(MarketStructureEngine, LiquidityEngine, InducementEngine, OrderBlockEngine,
FVGDetector, SupplyDemandEngine, SessionEngine) or a plain caller-supplied
dict/string, and turns it into a structured `ICTEvidenceItem`. It performs
NO detection, NO scoring, and NO weighting of its own - the future AI
Scoring rebuild (a later phase, explicitly NOT part of Phase C) is what
will eventually consume `ICTEvidenceReport` to compute a confidence
number. Until then, this is purely "what evidence exists and what does it
suggest," never "how much should we trust it."

WHY THIS IS NOT THE SAME AS `app/agent/evidence_engine.py`
--------------------------------------------------------------------------
That module already exists and already does something that sounds
similar but is a genuinely different responsibility, at a different point
in the pipeline - reusing its name here would create exactly the kind of
duplicate-sounding, confusing module this migration is meant to eliminate,
so it is called out explicitly:

  - `app.agent.evidence_engine.EvidenceEngine` is a POST-HOC explanation
    formatter for the conversational Trading Agent. It runs AFTER a
    decision has already been made (`AgentDecision`, built from the
    CURRENT, retail-mixed `AIScorer`'s output plus dashboard rows) and
    turns that already-computed decision into chat-friendly text. It is
    tightly coupled to `AgentDecision`'s shape and to today's AIScorer
    category keys (see its own `_SCORE_CATEGORY_LABELS`).

  - `ICTEvidenceEngine` (this module) runs BEFORE any confidence or
    decision exists. It is the evidence-COLLECTION layer that sits
    between the raw ICT engines and the future rebuilt AI Scorer (a
    later phase). It knows nothing about `AgentDecision`, nothing about
    the current AIScorer's category weights, and produces a generic,
    engine-agnostic `ICTEvidenceReport` that ANY future consumer -
    the rebuilt AIScorer, the Trading Agent, a dashboard - can read.

These two modules may reasonably both exist long-term (upstream collector
vs. downstream explainer of two different scoring systems), but that
distinction must stay explicit - hence `ICTEvidenceEngine`/
`ICTEvidenceItem`/`ICTEvidenceReport` naming throughout this file, never
the bare `EvidenceEngine`/`EvidenceItem`/`EvidenceReport` names already
taken by `app/agent/evidence_engine.py`.

DESIGN
--------------------------------------------------------------------------
Every `_*_evidence()` builder below:
  - Takes an already-computed object (or None) - never fetches, never
    detects, never re-derives anything an engine already computed.
  - Degrades honestly: a missing/None input produces an empty list, not a
    guess or a fabricated placeholder item.
  - Tags each item with a `polarity` ("bullish"/"bearish"/"neutral") -
    this is a plain, disclosed, DIRECTIONAL LABEL (e.g. a Bullish CHoCH is
    obviously bullish evidence), not a numeric confidence score. No
    weighting, no summation, no "how much" - only "which direction does
    this fact point, if any."
  - Only reads fields that already exist on the real production
    dataclasses (`StructureBreak`, `LiquidityLevel`, `InducementEvent`,
    `OrderBlock`, `FairValueGap`, `Zone`, `SessionContext`) - every field
    name used below was confirmed by reading the real files, not guessed.

`ICTEvidenceEngine.compile()` is the single orchestration entry point: it
takes an `ICTEvidenceInputs` bundle (every field optional, same "honest
degradation when omitted" pattern already used by every ICT engine's
constructor in this project) and returns one `ICTEvidenceReport`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

import pandas as pd


class ICTEvidenceCategory(str, Enum):
    MARKET_STRUCTURE = "market_structure"
    LIQUIDITY = "liquidity"
    INDUCEMENT = "inducement"
    ORDER_BLOCK = "order_block"
    FVG = "fvg"
    SUPPLY_DEMAND = "supply_demand"
    PREMIUM_DISCOUNT = "premium_discount"
    SESSION = "session"
    HTF_BIAS = "htf_bias"
    VOLUME = "volume"
    INSTITUTIONAL_BIAS = "institutional_bias"
    RISK = "risk"
    INVALIDATION = "invalidation"


# Only these three polarity values are ever used - a plain directional
# label, never a numeric score. See module docstring.
_VALID_POLARITIES = ("bullish", "bearish", "neutral")

# The ONLY ConfirmationIndicators.get_latest() keys this module will ever
# read (see `_volume_confirmation_evidence`) - CVD and Volume Profile POC
# are genuine institutional order-flow data (built from Binance's real
# taker-buy-volume field), not retail indicators. Every other key that
# dict may contain (rsi, macd_line, stoch_rsi_k, cci, williams_r, adx,
# supertrend, obv_rising, squeeze_on, donchian_breakout, ...) is retail
# technical analysis and is NEVER read here, even if a caller passes the
# full dict - a defensive guarantee, not just a documentation note.
_ALLOWED_VOLUME_KEYS = ("cvd", "cvd_rising", "poc_price", "relative_volume")


@dataclass(frozen=True)
class ICTEvidenceItem:
    category: ICTEvidenceCategory
    label: str
    detail: str
    polarity: str          # "bullish" | "bearish" | "neutral"
    source: str             # exactly which engine/field this came from - no-fabrication traceability
    timestamp: Optional[pd.Timestamp] = None

    def __post_init__(self) -> None:
        if self.polarity not in _VALID_POLARITIES:
            raise ValueError(f"Invalid polarity {self.polarity!r} - must be one of {_VALID_POLARITIES}")


@dataclass
class ICTEvidenceReport:
    symbol: Optional[str] = None
    timeframe: Optional[str] = None
    as_of: Optional[pd.Timestamp] = None
    items: List[ICTEvidenceItem] = field(default_factory=list)

    @property
    def bullish_evidence(self) -> List[ICTEvidenceItem]:
        return [i for i in self.items if i.polarity == "bullish"]

    @property
    def bearish_evidence(self) -> List[ICTEvidenceItem]:
        return [i for i in self.items if i.polarity == "bearish"]

    @property
    def neutral_evidence(self) -> List[ICTEvidenceItem]:
        return [i for i in self.items if i.polarity == "neutral"]

    @property
    def risk_notes(self) -> List[ICTEvidenceItem]:
        return [i for i in self.items if i.category == ICTEvidenceCategory.RISK]

    @property
    def invalidation(self) -> List[ICTEvidenceItem]:
        return [i for i in self.items if i.category == ICTEvidenceCategory.INVALIDATION]

    def by_category(self, category: ICTEvidenceCategory) -> List[ICTEvidenceItem]:
        return [i for i in self.items if i.category == category]

    def grouped_by_category(self) -> Dict[ICTEvidenceCategory, List[ICTEvidenceItem]]:
        grouped: Dict[ICTEvidenceCategory, List[ICTEvidenceItem]] = {}
        for item in self.items:
            grouped.setdefault(item.category, []).append(item)
        return grouped

    def as_labels(self) -> List[str]:
        """Just the label strings, in collection order - the flat bullet-
        list shape shown in the Phase C example output
        (["Bullish BOS", "Bullish CHoCH", "Liquidity Sweep", ...])."""
        return [i.label for i in self.items]


@dataclass
class ICTEvidenceInputs:
    """Every field optional; omitted fields simply produce no evidence for
    that category (honest degradation), matching every ICT engine's own
    constructor pattern in this project. Nothing here is fetched by this
    module - every value must already be computed by its owning engine
    before being passed in."""

    symbol: Optional[str] = None
    timeframe: Optional[str] = None
    as_of: Optional[pd.Timestamp] = None
    current_price: Optional[float] = None

    # Used only to choose which of protected_high/protected_low is the
    # relevant Invalidation level - a caller-supplied hint, not a
    # confidence calculation performed by this module.
    direction_context: Optional[str] = None  # "bullish" | "bearish" | None

    # Market Structure (LTF) - typically MarketStructureEngine.analyze()
    structure_breaks: Optional[List] = None
    structure_source_label: str = "MarketStructureEngine.external_breaks"
    protected_high: Optional[float] = None
    protected_low: Optional[float] = None

    # Higher-timeframe Market Structure (a second, caller-run
    # MarketStructureEngine over a higher-timeframe df) - HTF Bias.
    htf_structure_alignment: Optional[str] = None
    htf_timeframe_label: str = "HTF"

    liquidity_levels: Optional[List] = None       # already-swept LiquidityLevel list
    inducement_events: Optional[List] = None       # InducementEngine.detect() output
    order_blocks: Optional[List] = None
    fvgs: Optional[List] = None
    supply_demand_zones: Optional[List] = None
    premium_discount_zone: Optional[str] = None       # "premium" | "discount" | "equilibrium" | "unknown"
    premium_discount_position: Optional[float] = None

    session_context: Optional[object] = None       # SessionEngine SessionContext

    # Institutional order-flow data - already computed by
    # ConfirmationIndicators.get_latest(); only the whitelisted keys in
    # `_ALLOWED_VOLUME_KEYS` are ever read from this dict.
    volume_confirmation: Optional[Dict] = None

    # Caller-formatted notes (e.g. from fundamentals_service.py's already-
    # fetched funding rate / OI / long-short ratio / BTC dominance reads).
    # This module does not fetch or interpret raw fundamental numbers
    # itself - see module docstring's "collection only" scope.
    institutional_bias_notes: Optional[List[str]] = None
    risk_notes: Optional[List[str]] = None


class ICTEvidenceEngine:
    """Pure formatter/collector. See module docstring for the full
    "collection only, never detection, never scoring" contract."""

    # ------------------------------------------------------------------
    # Market Structure (BOS / CHoCH)
    # ------------------------------------------------------------------
    def structure_evidence(self, breaks: Optional[List], source_label: str) -> List[ICTEvidenceItem]:
        if not breaks:
            return []
        items = []
        for b in breaks:
            label = f"{b.direction.title()} {b.type}"
            mss_note = " (MSS)" if getattr(b, "is_mss", False) else ""
            detail = (
                f"{b.type}{mss_note} confirmed at {b.level} on {b.timestamp} "
                f"(displacement_ratio={b.displacement_ratio:.2f}, scope={b.scope.value})"
            )
            items.append(ICTEvidenceItem(
                category=ICTEvidenceCategory.MARKET_STRUCTURE, label=label, detail=detail,
                polarity="bullish" if b.direction == "bullish" else "bearish",
                source=source_label, timestamp=b.timestamp,
            ))
        return items

    # ------------------------------------------------------------------
    # Liquidity Sweeps
    # ------------------------------------------------------------------
    def liquidity_sweep_evidence(self, levels: Optional[List]) -> List[ICTEvidenceItem]:
        if not levels:
            return []
        items = []
        for lvl in levels:
            if not lvl.swept:
                continue  # resting liquidity is not, by itself, evidence a trade exists
            outcome = lvl.sweep_outcome.value
            is_buyside = lvl.type.value == "buyside"
            label = f"Liquidity Sweep ({lvl.kind.value.replace('_', ' ').title()}, {outcome.title()})"
            detail = (
                f"{lvl.type.value.title()} liquidity at {lvl.price} swept at "
                f"{lvl.swept_at} ({outcome}, confirmed={lvl.sweep_confirmed})"
            )
            # A grab REJECTS the raid direction (bearish if BSL grabbed,
            # bullish if SSL grabbed); a breakout CONTINUES the raid
            # direction. A plain, disclosed directional label - see
            # module docstring.
            if outcome == "grab":
                polarity = "bearish" if is_buyside else "bullish"
            elif outcome == "breakout":
                polarity = "bullish" if is_buyside else "bearish"
            else:
                polarity = "neutral"
            items.append(ICTEvidenceItem(
                category=ICTEvidenceCategory.LIQUIDITY, label=label, detail=detail,
                polarity=polarity, source="LiquidityEngine.detect_liquidity_sweeps()",
                timestamp=lvl.swept_at,
            ))
        return items

    # ------------------------------------------------------------------
    # Inducement
    # ------------------------------------------------------------------
    def inducement_evidence(self, events: Optional[List]) -> List[ICTEvidenceItem]:
        if not events:
            return []
        items = []
        for e in events:
            detail = (
                f"{e.reversal_break_type} reversal ({e.reversal_direction}) after an engineered "
                f"{e.liquidity_level.kind.value} sweep at {e.swept_liquidity_price} "
                f"(displacement_ratio={e.displacement_ratio:.2f}); conditions met: {', '.join(e.conditions_met)}"
            )
            items.append(ICTEvidenceItem(
                category=ICTEvidenceCategory.INDUCEMENT, label="Valid Inducement", detail=detail,
                polarity="bullish" if e.reversal_direction == "bullish" else "bearish",
                source="InducementEngine.detect()", timestamp=e.reversal_break_timestamp,
            ))
        return items

    # ------------------------------------------------------------------
    # Order Blocks
    # ------------------------------------------------------------------
    def order_block_evidence(self, blocks: Optional[List]) -> List[ICTEvidenceItem]:
        if not blocks:
            return []
        items = []
        for ob in blocks:
            if ob.state.value == "invalidated":
                continue  # no longer valid evidence
            is_bullish = ob.type.value == "bullish_ob"
            strength_label = ob.strength.value.title()
            label = f"{strength_label} Order Block" if ob.strength.value == "strong" else f"Order Block ({strength_label})"
            detail = (
                f"{ob.type.value} [{ob.low}, {ob.high}] state={ob.state.value} "
                f"displacement_ratio={ob.displacement_ratio:.2f} caused_bos={ob.caused_bos} "
                f"caused_choch={ob.caused_choch} has_fvg_confluence={ob.has_fvg_confluence}"
            )
            items.append(ICTEvidenceItem(
                category=ICTEvidenceCategory.ORDER_BLOCK, label=label, detail=detail,
                polarity="bullish" if is_bullish else "bearish",
                source="OrderBlockEngine", timestamp=ob.timestamp,
            ))
        return items

    # ------------------------------------------------------------------
    # Fair Value Gaps
    # ------------------------------------------------------------------
    def fvg_evidence(self, fvgs: Optional[List]) -> List[ICTEvidenceItem]:
        if not fvgs:
            return []
        items = []
        for gap in fvgs:
            is_bullish = gap.type.value == "bullish"
            label = "FVG Filled" if gap.filled else "FVG Present"
            detail = f"{gap.type.value} FVG [{gap.bottom}, {gap.top}] formed {gap.timestamp}, filled={gap.filled}"
            items.append(ICTEvidenceItem(
                category=ICTEvidenceCategory.FVG, label=label, detail=detail,
                polarity="bullish" if is_bullish else "bearish",
                source="FVGDetector", timestamp=gap.timestamp,
            ))
        return items

    # ------------------------------------------------------------------
    # Supply & Demand Zones + Premium/Discount
    # ------------------------------------------------------------------
    def supply_demand_evidence(self, zones: Optional[List]) -> List[ICTEvidenceItem]:
        if not zones:
            return []
        items = []
        for z in zones:
            if z.state.value == "broken":
                continue  # no longer valid evidence
            is_demand = z.type.value == "demand"
            base_label = "Demand Zone" if is_demand else "Supply Zone"
            label = f"{base_label} (Strong)" if z.strength.value == "strong" else base_label
            detail = (
                f"{z.type.value} zone [{z.bottom}, {z.top}] state={z.state.value} "
                f"strength={z.strength.value} displacement_ratio={z.displacement_ratio:.2f} "
                f"caused_bos={z.caused_bos} caused_choch={z.caused_choch} "
                f"has_ob_confluence={z.has_ob_confluence} has_fvg_confluence={z.has_fvg_confluence}"
            )
            items.append(ICTEvidenceItem(
                category=ICTEvidenceCategory.SUPPLY_DEMAND, label=label, detail=detail,
                polarity="bullish" if is_demand else "bearish",
                source="SupplyDemandEngine", timestamp=z.origin_timestamp,
            ))
        return items

    def premium_discount_evidence(
        self, zone: Optional[str], zone_position: Optional[float],
    ) -> List[ICTEvidenceItem]:
        if not zone or zone == "unknown":
            return []
        polarity_map = {"discount": "bullish", "premium": "bearish", "equilibrium": "neutral"}
        polarity = polarity_map.get(zone, "neutral")
        position_note = f" (zone_position={zone_position:.2f})" if zone_position is not None else ""
        favorability = {
            "discount": "a favorable region to look for longs",
            "premium": "a favorable region to look for shorts",
            "equilibrium": "neither a discount nor a premium",
        }.get(zone, "")
        detail = f"Price sits in {zone}{position_note} - {favorability}."
        return [ICTEvidenceItem(
            category=ICTEvidenceCategory.PREMIUM_DISCOUNT, label="Premium Discount", detail=detail,
            polarity=polarity, source="SupplyDemandEngine.get_zone()",
        )]

    # ------------------------------------------------------------------
    # Session / Kill Zone
    # ------------------------------------------------------------------
    def session_evidence(self, ctx: Optional[object]) -> List[ICTEvidenceItem]:
        if ctx is None:
            return []
        items = []
        if ctx.in_kill_zone:
            kz_label = ctx.kill_zone.value.replace("_", " ").title()
            items.append(ICTEvidenceItem(
                category=ICTEvidenceCategory.SESSION, label=kz_label, detail=ctx.label,
                polarity="neutral", source="SessionEngine", timestamp=ctx.timestamp,
            ))
        elif ctx.is_off_session:
            items.append(ICTEvidenceItem(
                category=ICTEvidenceCategory.SESSION, label="Off-Session",
                detail="No major session active - lower-liquidity window, evidence here carries less weight.",
                polarity="neutral", source="SessionEngine", timestamp=ctx.timestamp,
            ))
        else:
            items.append(ICTEvidenceItem(
                category=ICTEvidenceCategory.SESSION, label=ctx.label,
                detail=f"Active session: {ctx.label}, not in a Kill Zone.",
                polarity="neutral", source="SessionEngine", timestamp=ctx.timestamp,
            ))
        return items

    # ------------------------------------------------------------------
    # HTF Bias (higher-timeframe Market Structure, caller-computed)
    # ------------------------------------------------------------------
    def htf_bias_evidence(self, structure_alignment: Optional[str], timeframe_label: str) -> List[ICTEvidenceItem]:
        if not structure_alignment:
            return []
        label_map = {
            "aligned_bullish": f"HTF Bullish ({timeframe_label})",
            "aligned_bearish": f"HTF Bearish ({timeframe_label})",
            "conflicted": f"HTF Conflicted ({timeframe_label})",
            "insufficient_data": f"HTF Insufficient Data ({timeframe_label})",
        }
        polarity_map = {
            "aligned_bullish": "bullish", "aligned_bearish": "bearish",
            "conflicted": "neutral", "insufficient_data": "neutral",
        }
        label = label_map.get(structure_alignment, f"HTF {structure_alignment} ({timeframe_label})")
        return [ICTEvidenceItem(
            category=ICTEvidenceCategory.HTF_BIAS, label=label,
            detail=f"Higher-timeframe ({timeframe_label}) internal/external structure_alignment={structure_alignment}.",
            polarity=polarity_map.get(structure_alignment, "neutral"),
            source="MarketStructureEngine (higher timeframe)",
        )]

    # ------------------------------------------------------------------
    # Volume Confirmation (institutional order-flow only - see module
    # docstring's `_ALLOWED_VOLUME_KEYS` guarantee)
    # ------------------------------------------------------------------
    def volume_confirmation_evidence(self, confirmation: Optional[Dict]) -> List[ICTEvidenceItem]:
        if not confirmation:
            return []
        items = []
        cvd_rising = confirmation.get("cvd_rising")
        cvd = confirmation.get("cvd")
        if cvd_rising is not None:
            items.append(ICTEvidenceItem(
                category=ICTEvidenceCategory.VOLUME, label="Volume Confirmation (CVD)",
                detail=(
                    f"Cumulative Volume Delta is {'rising' if cvd_rising else 'falling'} "
                    f"(net {'buy' if cvd_rising else 'sell'}-side order flow)"
                    + (f", cvd={cvd:.2f}" if cvd is not None else "")
                ),
                polarity="bullish" if cvd_rising else "bearish",
                source="ConfirmationIndicators.get_latest()['cvd_rising']",
            ))
        poc_price = confirmation.get("poc_price")
        if poc_price is not None:
            items.append(ICTEvidenceItem(
                category=ICTEvidenceCategory.VOLUME, label="Volume Confirmation (Point of Control)",
                detail=f"Volume Profile Point of Control at {poc_price}.",
                polarity="neutral", source="ConfirmationIndicators.get_latest()['poc_price']",
            ))
        relative_volume = confirmation.get("relative_volume")
        if relative_volume is not None:
            items.append(ICTEvidenceItem(
                category=ICTEvidenceCategory.VOLUME, label="Volume Confirmation (Relative Volume)",
                detail=f"Current volume is {relative_volume}x its 20-period average.",
                polarity="neutral", source="ConfirmationIndicators.get_latest()['relative_volume']",
            ))
        return items

    # ------------------------------------------------------------------
    # Institutional Bias notes (caller-formatted, e.g. from
    # fundamentals_service.py's already-fetched macro reads)
    # ------------------------------------------------------------------
    def institutional_bias_evidence(self, notes: Optional[List[str]]) -> List[ICTEvidenceItem]:
        return self._notes_to_items(notes, ICTEvidenceCategory.INSTITUTIONAL_BIAS, "Institutional Bias", "caller-supplied fundamentals context")

    def risk_note_evidence(self, notes: Optional[List[str]]) -> List[ICTEvidenceItem]:
        return self._notes_to_items(notes, ICTEvidenceCategory.RISK, "Risk Note", "caller-supplied risk context")

    @staticmethod
    def _notes_to_items(notes: Optional[List[str]], category: ICTEvidenceCategory, label: str, source: str) -> List[ICTEvidenceItem]:
        if not notes:
            return []
        return [
            ICTEvidenceItem(category=category, label=label, detail=text, polarity="neutral", source=source)
            for text in notes if text
        ]

    # ------------------------------------------------------------------
    # Invalidation (from Market Structure's own protected_high/low)
    # ------------------------------------------------------------------
    def invalidation_evidence(
        self, protected_high: Optional[float], protected_low: Optional[float], direction_context: Optional[str],
    ) -> List[ICTEvidenceItem]:
        if direction_context not in ("bullish", "bearish"):
            return []
        if direction_context == "bullish":
            if protected_low is None:
                return []
            detail = f"Break and close below the protected low ({protected_low}) invalidates this bullish bias."
        else:
            if protected_high is None:
                return []
            detail = f"Break and close above the protected high ({protected_high}) invalidates this bearish bias."
        return [ICTEvidenceItem(
            category=ICTEvidenceCategory.INVALIDATION, label="Invalidation", detail=detail,
            polarity="neutral", source="MarketStructureEngine (protected_high/protected_low)",
        )]

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------
    def compile(self, inputs: ICTEvidenceInputs) -> ICTEvidenceReport:
        items: List[ICTEvidenceItem] = []
        items += self.structure_evidence(inputs.structure_breaks, inputs.structure_source_label)
        items += self.liquidity_sweep_evidence(inputs.liquidity_levels)
        items += self.inducement_evidence(inputs.inducement_events)
        items += self.order_block_evidence(inputs.order_blocks)
        items += self.fvg_evidence(inputs.fvgs)
        items += self.supply_demand_evidence(inputs.supply_demand_zones)
        items += self.premium_discount_evidence(inputs.premium_discount_zone, inputs.premium_discount_position)
        items += self.session_evidence(inputs.session_context)
        items += self.htf_bias_evidence(inputs.htf_structure_alignment, inputs.htf_timeframe_label)
        items += self.volume_confirmation_evidence(inputs.volume_confirmation)
        items += self.institutional_bias_evidence(inputs.institutional_bias_notes)
        items += self.risk_note_evidence(inputs.risk_notes)
        items += self.invalidation_evidence(inputs.protected_high, inputs.protected_low, inputs.direction_context)

        return ICTEvidenceReport(
            symbol=inputs.symbol, timeframe=inputs.timeframe, as_of=inputs.as_of, items=items,
        )
