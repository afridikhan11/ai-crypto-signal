"""
ICT Decision Engine - turns confidence + evidence + gate outcomes into ONE
fully-explained trade decision.

STATUS (2026-07-29): PRODUCTION. New module - the explainability layer of
the institutional ICT platform. This is what makes the system non-black-box:
every decision it produces, including every NO-TRADE, carries the complete
reasoning that produced it.

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT
--------------------------------------------------------------------------
This engine performs NO detection and NO scoring. It is the final
interpretation layer:

    ICT engines (app/smc/*)           -> WHAT is on the chart
    ICTEvidenceEngine (app/ai/...)    -> WHICH of those facts are evidence
    AIScorer (app/ai/scorer.py)       -> HOW MUCH confidence that evidence
                                          justifies  (the Confidence Engine)
    ICTDecisionEngine (this module)   -> WHAT WE DO about it, and WHY

`AIScorer` is this platform's Confidence Engine and remains the single
source of the confidence number - this module consumes `confidence` and
`score_breakdown` as inputs and never recomputes, re-weights, or second-
guesses them. Creating a second scoring path here would be exactly the
duplicate-logic problem this migration exists to remove.

WHY THIS IS NOT `app/agent/decision_engine.py`
--------------------------------------------------------------------------
`app.agent.decision_engine.AgentDecision` is the conversational Trading
Agent's own decision object - it answers "what should the chat agent tell
the user about their portfolio right now", is built from dashboard rows
and portfolio state, and is consumed by the agent's chat surface. This
module answers a different question at a different point in the pipeline:
"given this symbol's ICT evidence on this candle, do we take a trade, and
what is the complete justification either way". The two are kept
explicitly distinct - `ICTDecisionEngine`/`TradeDecision` here, never the
bare `DecisionEngine`/`AgentDecision` names already taken - the same
naming discipline already applied between `ICTEvidenceEngine` and
`app.agent.evidence_engine.EvidenceEngine`.

WHAT EVERY DECISION EXPLAINS
--------------------------------------------------------------------------
  - `decision`          : LONG / SHORT / NO_TRADE
  - `confidence`        : AIScorer's number, carried through unchanged
  - `why_long`          : the bull case, built from real bullish evidence
  - `why_short`         : the bear case, built from real bearish evidence
  - `why_no_trade`      : every gate that blocked the trade, with the
                          actual measured value vs. the threshold
  - `evidence`          : every collected ICT evidence label
  - `risk`              : risk:reward, risk level, and risk notes
  - `invalidation`      : the exact price level that proves this wrong
  - `missing_evidence`  : which ICT components were NOT present, and why
                          each one would have mattered

Both `why_long` and `why_short` are ALWAYS populated when the underlying
evidence exists - including on a trade we took. A LONG decision that still
lists three bearish evidence items under `why_short` is showing the real
counter-case, not hiding it. That is deliberate: a one-sided explanation
that only ever justifies the action taken is a rationalization, not an
explanation.

HONEST DEGRADATION
--------------------------------------------------------------------------
Every input is optional. A missing input produces an empty section or a
`MISSING` entry in `missing_evidence` - never an invented reason, never a
fabricated confidence. If this engine cannot explain something, it says so
explicitly rather than filling the gap with plausible-sounding text.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from app.ai.evidence_engine import ICTEvidenceCategory, ICTEvidenceReport


class DecisionType(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NO_TRADE = "NO_TRADE"


class RejectionGate(str, Enum):
    """Every reason this platform can decline to trade. Each maps 1:1 to a
    real gate in `SignalGenerator.generate()` - this enum exists so a
    rejection is a structured, queryable fact rather than a log string."""

    INSUFFICIENT_DATA = "insufficient_data"
    NO_STRUCTURE_BREAK = "no_structure_break"
    INDICATOR_NAN = "indicator_nan"
    INVALID_ATR = "invalid_atr"
    MIN_CONFIDENCE = "min_confidence"
    NO_CONFIRMATION = "no_confirmation"
    HTF_OPPOSITION = "htf_opposition"
    MIN_RISK_REWARD = "min_risk_reward"
    ENTRY_VALIDATION = "entry_validation"
    # Asset-Profile-driven gates (2026-07-30). These do not introduce a
    # different way of finding a trade - they let a market's own profile
    # make the SINGLE shared pipeline stricter for that market. See
    # app/assets/asset_profile.py's ICTFilters.
    OFF_SESSION = "off_session"
    KILL_ZONE_REQUIRED = "kill_zone_required"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


# The canonical ICT component checklist used to build `missing_evidence`.
# Each entry is (component_key, human_label, why_it_matters). This list is
# the single source of truth for "what a complete ICT setup looks like" -
# `missing_evidence` is literally this list minus whatever was actually
# found, so a component can never be silently dropped from the check by
# being forgotten at a call site.
ICT_COMPONENT_CHECKLIST = (
    ("market_structure", "Market Structure (BOS/CHoCH)",
     "Without a confirmed break of structure there is no evidence the trend has shifted or continued."),
    ("liquidity", "Liquidity Sweep",
     "A sweep of resting liquidity is what funds an institutional move; without one the move may be unsupported."),
    ("inducement", "Inducement",
     "Inducement confirms the move was engineered rather than incidental - its absence weakens the institutional read."),
    ("order_block", "Order Block",
     "An order block marks where institutional orders were placed and gives the entry a real anchor and invalidation."),
    ("fvg", "Fair Value Gap",
     "An unfilled FVG marks an imbalance price tends to revisit - useful as both an entry zone and a target."),
    ("supply_demand", "Supply/Demand Zone",
     "A fresh zone marks where price previously reacted with real displacement."),
    ("premium_discount", "Premium/Discount",
     "Buying in premium or selling in discount is entering at the worst available price in the range."),
    ("ote", "Optimal Trade Entry",
     "OTE marks the highest-quality retracement entry; without it the entry price is less favourable."),
    ("institutional_bias", "Institutional Bias",
     "Higher-timeframe bias is the top-down context that says whether this trade swims with or against the market."),
    ("htf_alignment", "HTF Structure Alignment",
     "Alignment across Weekly/Daily/4H structure is what separates a continuation trade from a counter-trend gamble."),
    ("session_killzone", "Session / Kill Zone",
     "Kill Zone timing is when institutional participation is highest; outside it, moves are lower-conviction."),
    ("volume_confirmation", "Volume Confirmation",
     "Real order-flow (CVD / forced liquidations) confirms the move has genuine participation behind it."),
)

# Score at/above which a category counts as a genuine supporting factor,
# and at/below which it counts as a genuine detracting one. Between the
# two, a category is simply "unremarkable" and is not cited either way -
# citing a 52/100 as a "strength" would be noise dressed up as reasoning.
STRONG_SCORE_THRESHOLD = 65.0
WEAK_SCORE_THRESHOLD = 40.0

# Risk:Reward boundaries for the coarse LOW/MEDIUM/HIGH label. These
# describe the RISK of the trade, so the mapping is inverted relative to
# RR: a high RR trade is a LOW risk trade for the same stop distance.
RR_LOW_RISK_THRESHOLD = 2.5
RR_MEDIUM_RISK_THRESHOLD = 1.5


@dataclass(frozen=True)
class MissingEvidenceItem:
    component: str
    label: str
    why_it_matters: str

    def to_dict(self) -> Dict[str, str]:
        return {"component": self.component, "label": self.label, "why_it_matters": self.why_it_matters}


@dataclass(frozen=True)
class BlockingReason:
    """One gate that blocked the trade, with the real measured value that
    failed it - never a bare 'rejected' with no number behind it."""

    gate: RejectionGate
    detail: str
    measured: Optional[float] = None
    threshold: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gate": self.gate.value,
            "detail": self.detail,
            "measured": self.measured,
            "threshold": self.threshold,
        }


@dataclass(frozen=True)
class RiskSummary:
    level: RiskLevel
    risk_reward: Optional[float] = None
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level.value,
            "risk_reward": self.risk_reward,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class TradeDecision:
    """The complete, self-explaining output of the platform's AI pipeline.

    A `NO_TRADE` decision is a first-class result here, not an absence of
    one: it carries the same evidence, the same missing-evidence audit,
    and the specific blocking reasons - so "why didn't it trade?" is
    always answerable from the object itself.
    """

    symbol: str
    decision: DecisionType
    confidence: int
    why_long: List[str] = field(default_factory=list)
    why_short: List[str] = field(default_factory=list)
    why_no_trade: List[BlockingReason] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    supporting_factors: List[str] = field(default_factory=list)
    detracting_factors: List[str] = field(default_factory=list)
    missing_evidence: List[MissingEvidenceItem] = field(default_factory=list)
    risk: Optional[RiskSummary] = None
    invalidation: Optional[str] = None
    invalidation_level: Optional[float] = None
    timeframe: Optional[str] = None
    session: Optional[str] = None
    institutional_bias: Optional[str] = None

    @property
    def is_tradeable(self) -> bool:
        return self.decision is not DecisionType.NO_TRADE

    @property
    def blocking_gates(self) -> List[str]:
        return [r.gate.value for r in self.why_no_trade]

    def to_dict(self) -> Dict[str, Any]:
        """Plain JSON-serializable form - this object is attached to the
        signal dict published over Redis (`json.dumps(..., default=str)`)
        and returned by the API, so every nested value here is a builtin."""
        return {
            "symbol": self.symbol,
            "decision": self.decision.value,
            "confidence": self.confidence,
            "why_long": list(self.why_long),
            "why_short": list(self.why_short),
            "why_no_trade": [r.to_dict() for r in self.why_no_trade],
            "evidence": list(self.evidence),
            "supporting_factors": list(self.supporting_factors),
            "detracting_factors": list(self.detracting_factors),
            "missing_evidence": [m.to_dict() for m in self.missing_evidence],
            "risk": self.risk.to_dict() if self.risk else None,
            "invalidation": self.invalidation,
            "invalidation_level": self.invalidation_level,
            "timeframe": self.timeframe,
            "session": self.session,
            "institutional_bias": self.institutional_bias,
        }

    def summary(self) -> str:
        """One-line human summary, e.g. for logs or a chat surface."""
        if self.decision is DecisionType.NO_TRADE:
            gates = ", ".join(self.blocking_gates) or "no qualifying setup"
            return f"{self.symbol}: NO TRADE (confidence {self.confidence}) - blocked by: {gates}"
        rr = f", RR {self.risk.risk_reward}:1" if self.risk and self.risk.risk_reward else ""
        return f"{self.symbol}: {self.decision.value} (confidence {self.confidence}{rr})"


@dataclass
class DecisionInputs:
    """Every field optional - an omitted field produces an empty section or
    a `missing_evidence` entry, never a guess. Same honest-degradation
    contract as `ICTEvidenceInputs`."""

    symbol: str
    direction: Optional[str] = None            # "LONG" | "SHORT" - the candidate direction, if one was formed
    confidence: int = 0                        # AIScorer's number, carried through unchanged
    score_breakdown: Optional[Dict[str, float]] = None
    evidence_report: Optional[ICTEvidenceReport] = None
    blocking_reasons: Optional[List[BlockingReason]] = None

    # Presence flags for the ICT component checklist. Each is a plain bool
    # the caller already knows from its own detection pass - this engine
    # never re-derives them from the dataframe.
    has_market_structure: bool = False
    has_liquidity: bool = False
    has_inducement: bool = False
    has_order_block: bool = False
    has_fvg: bool = False
    has_supply_demand: bool = False
    has_premium_discount: bool = False
    has_ote: bool = False
    has_institutional_bias: bool = False
    has_htf_alignment: bool = False
    has_session_killzone: bool = False
    has_volume_confirmation: bool = False

    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    risk_reward: Optional[float] = None
    risk_notes: Optional[List[str]] = None

    timeframe: Optional[str] = None
    session_label: Optional[str] = None
    institutional_bias: Optional[str] = None


class ICTDecisionEngine:
    """Stateless. `decide()` called twice with the same inputs returns an
    equivalent `TradeDecision` - same determinism contract as every other
    engine in this project."""

    # Evidence categories that map onto a directional case. SESSION,
    # VOLUME (POC/relative volume), RISK and INVALIDATION items are
    # deliberately excluded from the bull/bear cases: they are context and
    # consequence, not directional argument.
    _DIRECTIONAL_CATEGORIES = (
        ICTEvidenceCategory.MARKET_STRUCTURE,
        ICTEvidenceCategory.LIQUIDITY,
        ICTEvidenceCategory.INDUCEMENT,
        ICTEvidenceCategory.ORDER_BLOCK,
        ICTEvidenceCategory.FVG,
        ICTEvidenceCategory.SUPPLY_DEMAND,
        ICTEvidenceCategory.PREMIUM_DISCOUNT,
        ICTEvidenceCategory.HTF_BIAS,
        ICTEvidenceCategory.INSTITUTIONAL_BIAS,
        ICTEvidenceCategory.VOLUME,
    )

    def decide(self, inputs: DecisionInputs) -> TradeDecision:
        blocking = list(inputs.blocking_reasons or [])

        if blocking or inputs.direction not in ("LONG", "SHORT"):
            decision = DecisionType.NO_TRADE
            if not blocking and inputs.direction not in ("LONG", "SHORT"):
                blocking.append(BlockingReason(
                    gate=RejectionGate.NO_STRUCTURE_BREAK,
                    detail="No directional candidate was formed - no confirmed structure break to trade from.",
                ))
        else:
            decision = DecisionType(inputs.direction)

        why_long, why_short = self._directional_cases(inputs.evidence_report)
        supporting, detracting = self._score_factors(inputs.score_breakdown)
        missing = self._missing_evidence(inputs)
        risk = self._risk_summary(inputs)
        invalidation_text, invalidation_level = self._invalidation(inputs)

        return TradeDecision(
            symbol=inputs.symbol,
            decision=decision,
            confidence=inputs.confidence,
            why_long=why_long,
            why_short=why_short,
            why_no_trade=blocking,
            evidence=inputs.evidence_report.as_labels() if inputs.evidence_report else [],
            supporting_factors=supporting,
            detracting_factors=detracting,
            missing_evidence=missing,
            risk=risk,
            invalidation=invalidation_text,
            invalidation_level=invalidation_level,
            timeframe=inputs.timeframe,
            session=inputs.session_label,
            institutional_bias=inputs.institutional_bias,
        )

    # ------------------------------------------------------------------
    # Why LONG / Why SHORT - both always built from real evidence, so the
    # counter-case to whatever we did is always visible. See module
    # docstring.
    # ------------------------------------------------------------------
    def _directional_cases(self, report: Optional[ICTEvidenceReport]):
        if report is None:
            return [], []
        why_long: List[str] = []
        why_short: List[str] = []
        for item in report.items:
            if item.category not in self._DIRECTIONAL_CATEGORIES:
                continue
            line = f"{item.label}: {item.detail}"
            if item.polarity == "bullish":
                why_long.append(line)
            elif item.polarity == "bearish":
                why_short.append(line)
        return why_long, why_short

    # ------------------------------------------------------------------
    # Which scored categories actually carried the confidence number, and
    # which dragged on it. Reads AIScorer's own per-category output - no
    # re-scoring. See module docstring.
    # ------------------------------------------------------------------
    @staticmethod
    def _score_factors(score_breakdown: Optional[Dict[str, float]]):
        if not score_breakdown:
            return [], []
        strong = sorted(
            ((k, v) for k, v in score_breakdown.items() if v >= STRONG_SCORE_THRESHOLD),
            key=lambda kv: kv[1], reverse=True,
        )
        weak = sorted(
            ((k, v) for k, v in score_breakdown.items() if v <= WEAK_SCORE_THRESHOLD),
            key=lambda kv: kv[1],
        )
        fmt = lambda k, v: f"{k.replace('_', ' ').title()} scored {v:.0f}/100"
        return [fmt(k, v) for k, v in strong], [fmt(k, v) for k, v in weak]

    # ------------------------------------------------------------------
    # Missing Evidence - the ICT component checklist minus what was found.
    # ------------------------------------------------------------------
    @staticmethod
    def _missing_evidence(inputs: DecisionInputs) -> List[MissingEvidenceItem]:
        missing: List[MissingEvidenceItem] = []
        for component, label, why in ICT_COMPONENT_CHECKLIST:
            if not getattr(inputs, f"has_{component}", False):
                missing.append(MissingEvidenceItem(component=component, label=label, why_it_matters=why))
        return missing

    # ------------------------------------------------------------------
    # Risk
    # ------------------------------------------------------------------
    @staticmethod
    def _risk_summary(inputs: DecisionInputs) -> RiskSummary:
        rr = inputs.risk_reward
        if rr is None:
            level = RiskLevel.UNKNOWN
        elif rr >= RR_LOW_RISK_THRESHOLD:
            level = RiskLevel.LOW
        elif rr >= RR_MEDIUM_RISK_THRESHOLD:
            level = RiskLevel.MEDIUM
        else:
            level = RiskLevel.HIGH

        notes = list(inputs.risk_notes or [])
        if inputs.evidence_report is not None:
            notes.extend(item.detail for item in inputs.evidence_report.risk_notes)

        return RiskSummary(
            level=level,
            risk_reward=rr,
            entry_price=inputs.entry_price,
            stop_loss=inputs.stop_loss,
            take_profit=inputs.take_profit,
            notes=notes,
        )

    # ------------------------------------------------------------------
    # Invalidation - the price that proves this idea wrong. Prefers the
    # Evidence Engine's own invalidation item (built from Market
    # Structure's protected high/low); falls back to the stop loss, which
    # is itself structure-anchored in SignalGenerator.
    # ------------------------------------------------------------------
    @staticmethod
    def _invalidation(inputs: DecisionInputs):
        if inputs.evidence_report is not None:
            items = inputs.evidence_report.invalidation
            if items:
                return items[0].detail, inputs.stop_loss
        if inputs.stop_loss is not None and inputs.direction in ("LONG", "SHORT"):
            side = "below" if inputs.direction == "LONG" else "above"
            return (
                f"A close {side} {inputs.stop_loss} invalidates this {inputs.direction} idea.",
                inputs.stop_loss,
            )
        return None, inputs.stop_loss
