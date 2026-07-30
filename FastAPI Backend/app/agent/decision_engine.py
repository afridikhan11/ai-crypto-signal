"""
Decision Engine (Phase 2, 2026-07-27).

Merges an ALREADY-COMPUTED report (the exact dict shape
`app.schemas.token_scan.TokenScanReport` uses - produced by the existing,
unmodified `build_market_scan_report()` for a Binance trading pair or
`build_token_scan_report()` for an on-chain token; both already power the
Token Scanner today) with a Risk Manager assessment and a Strategy Profile
into ONE `AgentDecision`. This file performs NO indicator/SMC/security
recalculation of its own - every score it reads was already computed by
the reused report. The only new arithmetic here is clearly-disclosed
RESHAPING of existing values into the merged schema (see each field's
comment below), never a new market read.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.agent.risk_manager import AgentRiskAssessment, RiskManager
from app.agent.strategy_profile_manager import ResolvedStrategyProfile, get_default_style_profile

_RR_NUMBER_RE = re.compile(r"1\s*:\s*([\d.]+)")

# --- Phase 3 additions (2026-07-27) -----------------------------------
# Five new, PRESENTATION-ONLY fields requested for the Decision Engine's
# output: Confidence Breakdown, Recommendation Level, Expected Holding
# Time, Risk Category, Suitable Trading Style. Every one of them is built
# ONLY from scores/fields this file already computed in Phase 2 (or from
# the Strategy Profile Manager's existing catalog) - none of them reads a
# new indicator, calls a new provider, or changes any Phase 2 number. Each
# helper below documents its exact, fixed, disclosed derivation so nothing
# here is fabricated.

_HIGH_CONVICTION_CONFIDENCE_THRESHOLD = 80  # disclosed cutoff for "High Conviction" wording below

_RECOMMENDATION_LABELS = {
    "STRONG BUY": "Strong Buy", "BUY": "Buy", "HOLD": "Hold",
    "SELL": "Sell", "STRONG SELL": "Strong Sell",
}

# Disclosed, fixed bands over the EXISTING composite risk_score (see
# RiskManager.composite_risk_score) - a labels-only bucketing, not a new
# risk calculation.
_RISK_CATEGORY_BANDS = [(80, "Low Risk"), (60, "Moderate Risk"), (40, "Elevated Risk")]


def _recommendation_level(final_decision: str, confidence_pct: int) -> str:
    """
    Combines two fields this file already produces (final_decision,
    confidence_pct) into one human-readable line. HOLD gets its own
    wording ("wait for confirmation" vs "insufficient confluence") since
    "conviction" doesn't read naturally for a non-directional call.
    """
    label = _RECOMMENDATION_LABELS.get(final_decision, final_decision.title())
    high_confidence = confidence_pct >= _HIGH_CONVICTION_CONFIDENCE_THRESHOLD
    if final_decision == "HOLD":
        qualifier = "Wait for Confirmation" if high_confidence else "Insufficient Confluence"
    else:
        qualifier = "High Conviction" if high_confidence else "Moderate Conviction"
    return f"{label} — {qualifier}"


def _risk_category(risk_score: Optional[int]) -> str:
    """Buckets the EXISTING composite risk_score (0-100, may be None when
    there isn't enough real risk data - see RiskManager.composite_risk_score's
    own docstring) into a readable label. Never guesses a category when the
    underlying score itself is None."""
    if risk_score is None:
        return "Unknown (insufficient risk data - no linked account/portfolio read)"
    for floor, label in _RISK_CATEGORY_BANDS:
        if risk_score >= floor:
            return label
    return "High Risk"


def _suggest_trading_style(risk_reward: Optional[float], technical_verdict: Optional[str]) -> str:
    """
    Heuristic PRESENTATION label only, reusing two fields this file already
    has (the report's own risk_reward and the reused Technical Dashboard's
    own verdict string) - no new indicator or timeframe read. A wide RR
    with a strong trend read suits a longer hold; a tight RR suits a fast
    in/out. This is a suggestion, not personalized financial advice - the
    trader still explicitly chooses their own style via `style` on
    /agent/query (see strategy_profile_manager.py), which is what actually
    rescales the SL/TP levels.
    """
    if risk_reward is None:
        return "Not enough data to suggest a style"
    strong_trend = (technical_verdict or "").strip().upper() == "STRONG"
    if risk_reward >= 3.0 or (strong_trend and risk_reward >= 2.0):
        trend_note = " and a strong trend read" if strong_trend else ""
        return f"Swing / Position (wide risk-reward{trend_note} support a longer hold)"
    if risk_reward < 1.5:
        return "Scalping (tight risk-reward suits a fast in/out)"
    return "Intraday (balanced risk-reward)"

# Disclosed, fixed mapping from the reused report's own final_decision
# vocabulary (STRONG BUY/BUY/NEUTRAL/SELL/STRONG SELL from
# market_scorer.py, plus AVOID from token_scorer.py's security-driven
# verdict) to the Trading Agent's own 5-way scale.
_FINAL_DECISION_MAP = {
    "STRONG BUY": "STRONG BUY",
    "BUY": "BUY",
    "NEUTRAL": "HOLD",
    "SELL": "SELL",
    "STRONG SELL": "STRONG SELL",
    "AVOID": "STRONG SELL",
}

# Disclosed, fixed numeric scale for the report's existing categorical
# liquidity label (see market_scorer.py's own _liquidity_label thresholds:
# >=$100M Healthy, >=$20M Moderate, >=$5M Thin, else Very Thin) - assigns a
# representative score to each EXISTING category rather than inventing a
# new liquidity metric.
_LIQUIDITY_LABEL_SCORE = {
    "healthy": 90, "moderate": 60, "thin": 30, "very thin": 10, "unknown": None,
}


@dataclass
class AgentDecision:
    symbol_or_address: str
    chain: str
    generated_at: str

    overall_score: int
    confidence_pct: int
    technical_score: Optional[int]
    security_score: Optional[int]
    liquidity_score: Optional[int]
    smart_money_score: Optional[int]
    holder_score: Optional[int]
    risk_score: Optional[int]

    trend: str
    momentum: Optional[str]

    entry: Optional[float]
    stop_loss: Optional[float]
    take_profit_1: Optional[float]
    take_profit_2: Optional[float]
    take_profit_3: Optional[float]
    risk_reward: Optional[float]

    final_decision: str
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    style_applied: Optional[str] = None

    # Phase 3 additions - see the helper functions above this class for each
    # field's exact, disclosed derivation from scores already computed below.
    confidence_breakdown: Dict[str, Optional[int]] = field(default_factory=dict)
    recommendation_level: str = ""
    expected_holding_time: str = ""
    risk_category: str = ""
    suitable_trading_style: str = ""

    raw_report: Dict[str, Any] = field(default_factory=dict)


def _parse_rr(rr_str: Optional[str]) -> Optional[float]:
    if not rr_str:
        return None
    m = _RR_NUMBER_RE.search(rr_str)
    return float(m.group(1)) if m else None


class DecisionEngine:
    def merge(
        self,
        report: Dict[str, Any],
        risk: Optional[AgentRiskAssessment] = None,
        resolved_profile: Optional[ResolvedStrategyProfile] = None,
    ) -> AgentDecision:
        ta = report.get("technical_analysis") or {}
        ta_summary = ta.get("ai_summary") or {}
        sec = report.get("contract_security_analysis") or {}
        sec_summary = sec.get("ai_summary") or {}
        sm = report.get("smart_money_analysis") or {}
        sm_summary = sm.get("ai_summary") or {}
        token_security_legacy = report.get("token_security") or {}

        technical_score = ta_summary.get("technical_score")
        security_score = sec_summary.get("security_score")
        smart_money_score = sm_summary.get("smart_money_score")

        liquidity_label = (report.get("liquidity") or "unknown").strip().lower()
        liquidity_score = _LIQUIDITY_LABEL_SCORE.get(liquidity_label)

        # holder_score: only computed when the legacy TokenSecuritySection's
        # real top_10_holder_pct is available (on-chain tokens only) - a
        # simple, disclosed inverse of concentration (lower % held by the
        # top 10 wallets = higher score), never guessed when missing.
        top10_pct = token_security_legacy.get("top_10_holder_pct")
        holder_score = round(max(0.0, 100.0 - float(top10_pct))) if top10_pct is not None else None

        risk_reward = _parse_rr(report.get("risk_reward"))

        entry = report.get("best_entry")
        stop_loss = report.get("stop_loss")
        take_profit_1 = report.get("take_profit_1")
        take_profit_2 = report.get("take_profit_2")
        take_profit_3 = report.get("take_profit_3")
        style_applied = None

        # Style rescaling - see StrategyProfileManager/ResolvedStrategyProfile
        # docstrings: reuses the SAME entry/SL/TP the report already
        # computed, only rescales the DISTANCE using the requested style's
        # ratio. INTRADAY (the default) is a no-op (ratio 1.0/1.0).
        trend_direction = "LONG" if (report.get("trend") or "").lower() == "bullish" else "SHORT"
        if (
            resolved_profile is not None
            and resolved_profile.style_profile.style != "intraday"
            and entry is not None and stop_loss is not None and take_profit_1 is not None
        ):
            new_sl, new_tp = resolved_profile.rescale_levels(
                entry_price=float(entry), direction=trend_direction,
                base_stop_loss=float(stop_loss), base_take_profit=float(take_profit_1),
            )
            stop_loss, take_profit_1 = round(new_sl, 8), round(new_tp, 8)
            take_profit_2 = take_profit_3 = None  # only TP1 is meaningfully rescaled; 2/3 (rare) not re-derived
            risk = abs(entry - stop_loss)
            reward = abs(take_profit_1 - entry)
            risk_reward = round(reward / risk, 2) if risk > 0 else risk_reward
            style_applied = resolved_profile.style_profile.label

        risk_score = None
        warnings: List[str] = []
        if risk is not None:
            risk_score = RiskManager.composite_risk_score(
                risk_reward_ok=risk.risk_reward_ok,
                daily_loss=risk.daily_loss,
                portfolio_risk=risk.portfolio_risk,
                correlation_warning=risk.correlation_warning,
            )
            warnings.extend(risk.warnings)

        # overall_score: documented blend of whichever REAL sub-scores this
        # report actually has (never averages in a None) - technical +
        # confidence always available; security/smart-money/risk only for
        # on-chain tokens / when a risk assessment was supplied.
        parts = [p for p in [technical_score, report.get("confidence_pct"), security_score, smart_money_score, risk_score] if p is not None]
        overall_score = round(sum(parts) / len(parts)) if parts else report.get("confidence_pct", 0)

        raw_final = report.get("final_decision", "NEUTRAL")
        final_decision = _FINAL_DECISION_MAP.get(raw_final, "HOLD")

        reasons = list(report.get("reasons") or [])

        confidence_pct = report.get("confidence_pct", 0)

        # Phase 3 additions - see the module-level helpers' docstrings.
        # confidence_breakdown simply re-exposes the sub-scores this method
        # already computed above, grouped under one field for readability -
        # no new number is introduced here.
        confidence_breakdown = {
            "technical": technical_score,
            "security": security_score,
            "liquidity": liquidity_score,
            "smart_money": smart_money_score,
            "holder": holder_score,
            "risk": risk_score,
        }
        recommendation_level = _recommendation_level(final_decision, confidence_pct)
        expected_holding_time = (
            resolved_profile.style_profile.expected_holding_period
            if resolved_profile is not None
            else get_default_style_profile().expected_holding_period
        )
        risk_cat = _risk_category(risk_score)
        suitable_style = _suggest_trading_style(risk_reward, ta_summary.get("verdict"))

        return AgentDecision(
            symbol_or_address=report.get("token_address", ""),
            chain=report.get("chain", ""),
            generated_at=report.get("generated_at", ""),
            overall_score=overall_score,
            confidence_pct=confidence_pct,
            technical_score=technical_score,
            security_score=security_score,
            liquidity_score=liquidity_score,
            smart_money_score=smart_money_score,
            holder_score=holder_score,
            risk_score=risk_score,
            trend=report.get("trend", "Unknown"),
            momentum=ta_summary.get("momentum"),
            entry=entry,
            stop_loss=stop_loss,
            take_profit_1=take_profit_1,
            take_profit_2=take_profit_2,
            take_profit_3=take_profit_3,
            risk_reward=risk_reward,
            final_decision=final_decision,
            reasons=reasons,
            warnings=warnings,
            style_applied=style_applied,
            confidence_breakdown=confidence_breakdown,
            recommendation_level=recommendation_level,
            expected_holding_time=expected_holding_time,
            risk_category=risk_cat,
            suitable_trading_style=suitable_style,
            raw_report=report,
        )
