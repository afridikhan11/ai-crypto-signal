"""
Evidence & Reasoning Engine (Phase 4, 2026-07-27).

Requirement 12 from the brief is the load-bearing constraint on this whole
file: "The Evidence Engine is an explanation layer, not another analysis
engine." Concretely, that means:

  - This module NEVER fetches data itself (no HTTP calls, no DB queries, no
    scanner/provider calls). It takes an already-fully-computed
    `AgentDecision` (produced by `app.agent.decision_engine.DecisionEngine`,
    itself a merge of the EXISTING, unmodified Technical/Smart Money/
    Contract Security dashboards - see decision_engine.py's own docstring)
    plus an OPTIONAL, already-fetched `HistoricalEvidence` (the caller does
    the one real DB read via the existing `SignalRepository`, this module
    only formats the result - see `orchestrator.py`'s `_build_evidence`).
  - It NEVER computes a new indicator (Requirement 10). The one genuinely
    new arithmetic operation anywhere in this file is
    `score * weight = contribution_points` inside `_explain_confidence()` -
    and that is not new: it is literally the same per-term multiplication
    `app.ai.scorer.AIScorer.assess()` already performs when it sums
    `confidence = sum(weight[k] * scores[k] for k in scores)`. This module
    only keeps the individual terms of that existing sum instead of only
    the total (see `app/services/market_scorer.py`'s `score_breakdown`/
    `score_weights`, added in this same phase to stop discarding them).
  - Every `EvidenceItem.detail` string is copied VERBATIM from data the
    platform already produced (a dashboard row's own `status`/`detail`
    fields, or a `reasons`/`warnings` entry already on the `AgentDecision`)
    - Requirement 8 ("never invent evidence"). The only thing this module
      adds on top of the verbatim text is a `polarity` tag (positive/
      negative/neutral) from a small, disclosed keyword heuristic (see
      `_classify_polarity()`) - the SAME category of best-effort,
      disclosed labeling this project already does elsewhere (e.g.
      decision_engine.py's `suitable_trading_style`/`risk_category`). If
      the heuristic mislabels a row, the verbatim text is still shown
      unchanged - nothing is hidden or fabricated by a wrong label.
  - When something isn't available, this module says so honestly
    (`HistoricalEvidence(available=False, reason=...)`,
    `EvidenceReport.data_gaps`) rather than guessing - Requirement 8/9.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.agent.decision_engine import AgentDecision

# ---------------------------------------------------------------------
# Category labels for AIScorer's 13 calibratable buckets (see
# app/ai/calibration.py's DEFAULT_WEIGHTS) - human-readable names only,
# the keys/weights/scores themselves are untouched real values.
# ---------------------------------------------------------------------
_SCORE_CATEGORY_LABELS: Dict[str, str] = {
    "market_structure": "Market Structure (BOS/CHoCH)",
    "liquidity_sweep": "Liquidity Sweep",
    "order_block_quality": "Order Block Quality",
    "fvg_presence": "Fair Value Gap",
    "supply_demand_zone": "Supply/Demand Zone",
    "confluence": "Confluence",
    "multi_tf_alignment": "Multi-Timeframe Alignment",
    "trend_filters": "Trend Filters",
    "momentum": "Momentum",
    "volume_order_flow": "Volume & Order Flow",
    "volatility_context": "Volatility Context",
    "pattern_confirmation": "Pattern Confirmation",
    "fundamental_context": "Fundamental Context",
}

# Fallback (Phase 3) sub-score labels - used only when the market-scan
# pipeline's finer-grained score_breakdown/score_weights aren't present
# (on-chain token scans, or the market-scan's own no-clear-structure
# fallback branch - see market_scorer.py). These are the SAME six fields
# decision_engine.py's `confidence_breakdown` already exposes.
_DECISION_SUBSCORE_LABELS: Dict[str, str] = {
    "technical": "Technical", "security": "Security", "liquidity": "Liquidity",
    "smart_money": "Smart Money", "holder": "Holder Concentration", "risk": "Risk Management",
}

# Disclosed, best-effort keyword heuristic - checked negative-phrases-first
# so a negation ("NOT renounced", "cannot sell") isn't misread as positive
# just because it also contains a positive root word ("renounced", "sell").
# See this module's docstring for why a wrong label here never hides or
# changes the underlying verbatim evidence text.
_NEGATIVE_PHRASES = (
    "not available", "no clear", "not confirmed", "against", "opposes", "cannot",
    "critical", "extreme", "very thin", "not renounced", "insufficient", "avoid",
    "no sweep", "falling (distribution)", "sell-dominant", "can be pulled",
    "holders control", "not open source", "no liquidity", "high risk", "honeypot",
    "blacklist", "would not qualify", "unfavorable", "unfavourable", "below",
    "breached", "elevated risk",
)
_POSITIVE_PHRASES = (
    "confirmed", "aligned", "present", "swept", "renounced", "locked",
    "accumulation", "buy-dominant", "rising (accumulation)", "supports",
    "safe", "healthy", "meets the live signal generator", "qualify",
)


def _classify_polarity(text: Optional[str]) -> str:
    if not text:
        return "neutral"
    lowered = text.lower()
    if any(phrase in lowered for phrase in _NEGATIVE_PHRASES):
        return "negative"
    if any(phrase in lowered for phrase in _POSITIVE_PHRASES):
        return "positive"
    return "neutral"


@dataclass
class EvidenceItem:
    label: str      # short name of the factor, e.g. a dashboard row's "name" or a reason's first clause
    detail: str     # VERBATIM text from the existing report/decision - never rephrased or invented
    polarity: str   # "positive" | "negative" | "neutral" - see _classify_polarity's disclosed heuristic
    category: str   # "technical" | "smart_money" | "security" | "risk" | "general"
    source: str     # exactly where this came from, e.g. "Technical Dashboard: Liquidity Sweep"


@dataclass
class ConfidenceContribution:
    category: str
    label: str
    score: Optional[int]                   # 0-100 raw category score, when available
    weight_pct: Optional[float]             # this category's weight in the composite (0-100), when available
    contribution_points: Optional[float]    # score * weight - the exact term AIScorer's own sum already computes
    shortfall_points: Optional[float]       # weight * (100 - score) - how many points THIS category cost vs. a perfect 100
    note: str


@dataclass
class ConfidenceExplanation:
    confidence_pct: int
    summary: str
    granularity: str  # "category_breakdown" (market-scan, AIScorer) | "sub_score_breakdown" (Phase 3 fallback) | "unavailable"
    contributions: List[ConfidenceContribution] = field(default_factory=list)
    top_contributors: List[ConfidenceContribution] = field(default_factory=list)
    top_reducers: List[ConfidenceContribution] = field(default_factory=list)


@dataclass
class HistoricalEvidence:
    available: bool
    reason: Optional[str] = None
    symbol: Optional[str] = None
    sample_size: int = 0
    wins: int = 0
    losses: int = 0
    active: int = 0
    win_rate_pct: Optional[float] = None
    recent: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class EvidenceReport:
    positive_factors: List[EvidenceItem] = field(default_factory=list)
    negative_factors: List[EvidenceItem] = field(default_factory=list)
    technical_evidence: List[EvidenceItem] = field(default_factory=list)
    smart_money_evidence: List[EvidenceItem] = field(default_factory=list)
    security_evidence: List[EvidenceItem] = field(default_factory=list)
    confidence_explanation: Optional[ConfidenceExplanation] = None
    historical_evidence: Optional[HistoricalEvidence] = None
    data_gaps: List[str] = field(default_factory=list)


# Statuses a Signal's `status` (see app/models/signal.py's SignalStatus
# enum: ACTIVE/TP_HIT/STOPPED/CANCELLED) is counted under for the simple
# win/loss tally below - matches app/ai/calibration.py's own
# WIN_STATUSES/LOSS_STATUSES grouping so this module's "win rate" means
# the exact same thing the calibration system already uses that term for.
_WIN_STATUS_VALUES = {"TP_HIT"}
_LOSS_STATUS_VALUES = {"STOPPED"}


def build_historical_evidence(symbol: Optional[str], signals: Optional[List[Any]]) -> HistoricalEvidence:
    """
    Formats an ALREADY-FETCHED list of real `Signal` ORM rows (via the
    existing `SignalRepository.get_signals()` - see orchestrator.py) into
    honest historical evidence. Does no DB access itself - Requirement 12.

    `signals` is None when the caller didn't attempt a lookup at all (e.g.
    the asset is a contract address, which this project has no signal
    history concept for); an empty list means a lookup WAS attempted and
    genuinely found nothing yet. The two cases get different, honest
    `reason` text rather than being conflated.
    """
    if symbol is None:
        return HistoricalEvidence(
            available=False,
            reason="Historical signal evidence only applies to Binance trading-pair symbols this project tracks.",
        )
    if signals is None:
        return HistoricalEvidence(
            available=False, symbol=symbol,
            reason="No historical signal lookup was performed for this query.",
        )
    if not signals:
        return HistoricalEvidence(
            available=False, symbol=symbol,
            reason=f"No past signals recorded for {symbol} yet - this would be its first.",
        )

    wins = losses = active = 0
    recent: List[Dict[str, Any]] = []
    for s in signals:
        status_value = getattr(getattr(s, "status", None), "value", None) or str(getattr(s, "status", ""))
        direction_value = getattr(getattr(s, "direction", None), "value", None) or str(getattr(s, "direction", ""))
        if status_value in _WIN_STATUS_VALUES:
            wins += 1
        elif status_value in _LOSS_STATUS_VALUES:
            losses += 1
        elif status_value == "ACTIVE":
            active += 1
        closed_at = getattr(s, "closed_at", None)
        created_at = getattr(s, "created_at", None)
        recent.append({
            "status": status_value,
            "direction": direction_value,
            "confidence": getattr(s, "confidence", None),
            "risk_reward": getattr(s, "risk_reward", None),
            "created_at": created_at.isoformat() if created_at is not None else None,
            "closed_at": closed_at.isoformat() if closed_at is not None else None,
        })

    decided = wins + losses
    win_rate_pct = round(wins / decided * 100, 1) if decided > 0 else None

    return HistoricalEvidence(
        available=True,
        symbol=symbol,
        sample_size=len(signals),
        wins=wins,
        losses=losses,
        active=active,
        win_rate_pct=win_rate_pct,
        recent=recent,
    )


class EvidenceEngine:
    """Pure formatter: `AgentDecision` (+ optional pre-fetched
    `HistoricalEvidence`) in, `EvidenceReport` out. See module docstring."""

    def build(self, decision: AgentDecision, historical: Optional[HistoricalEvidence] = None) -> EvidenceReport:
        raw = decision.raw_report or {}

        technical_evidence = self._dashboard_evidence(
            (raw.get("technical_analysis") or {}).get("dashboard") or [], category="technical", source_label="Technical Dashboard",
        )
        smart_money_evidence = self._dashboard_evidence(
            (raw.get("smart_money_analysis") or {}).get("dashboard") or [], category="smart_money", source_label="Smart Money Dashboard",
        )
        security_dashboard = (raw.get("contract_security_analysis") or {}).get("dashboard") or []
        security_evidence = self._dashboard_evidence(security_dashboard, category="security", source_label="Contract Security Dashboard")
        if not security_evidence:
            # Falls back to the legacy TokenSecuritySection's own real
            # fields (only populated for on-chain tokens where the newer
            # multi-source dashboard above is unavailable/empty) - never a
            # second, invented security read.
            security_evidence = self._legacy_token_security_evidence(raw.get("token_security") or {})

        general_evidence = self._text_list_evidence(
            decision.reasons, category="general", source_label="Decision reasons",
        )
        warning_evidence = self._text_list_evidence(
            decision.warnings, category="risk", source_label="Risk Manager warning", force_polarity="negative",
        )

        all_items = technical_evidence + smart_money_evidence + security_evidence + general_evidence + warning_evidence
        positive_factors = [i for i in all_items if i.polarity == "positive"]
        negative_factors = [i for i in all_items if i.polarity == "negative"]

        confidence_explanation = self._explain_confidence(decision, raw)

        data_gaps = [
            f"{g.get('field', 'unknown')}: {g.get('reason', 'not disclosed')}"
            for g in (raw.get("unavailable_data") or [])
        ]

        return EvidenceReport(
            positive_factors=positive_factors,
            negative_factors=negative_factors,
            technical_evidence=technical_evidence,
            smart_money_evidence=smart_money_evidence,
            security_evidence=security_evidence,
            confidence_explanation=confidence_explanation,
            historical_evidence=historical or HistoricalEvidence(
                available=False, reason="No historical lookup was performed for this query.",
            ),
            data_gaps=data_gaps,
        )

    # -- dashboard rows (Technical / Smart Money / Security) -------------

    def _dashboard_evidence(self, rows: List[Dict[str, Any]], category: str, source_label: str) -> List[EvidenceItem]:
        items: List[EvidenceItem] = []
        for row in rows:
            name = row.get("name") or "Unnamed indicator"
            status = row.get("status") or ""
            confidence = row.get("confidence")
            detail = row.get("detail") or status
            # confidence==0 means "Not Available" (see ta_dashboard.py's own
            # module docstring) - not real evidence either way, skipped
            # rather than shown as a false positive/negative signal.
            if confidence == 0 or status.strip().lower() == "not available":
                continue
            polarity = _classify_polarity(f"{status} {detail}")
            items.append(EvidenceItem(
                label=name,
                detail=f"{status}" + (f" - {detail}" if detail and detail != status else ""),
                polarity=polarity,
                category=category,
                source=f"{source_label}: {name}",
            ))
        return items

    def _legacy_token_security_evidence(self, token_security: Dict[str, Any]) -> List[EvidenceItem]:
        if not token_security or not token_security.get("available"):
            return []
        items: List[EvidenceItem] = []

        def _add(label: str, condition: Optional[bool], true_text: str, false_text: str, negative_when_true: bool = True):
            if condition is None:
                return
            text = true_text if condition else false_text
            polarity = ("negative" if negative_when_true else "positive") if condition else ("positive" if negative_when_true else "negative")
            items.append(EvidenceItem(label=label, detail=text, polarity=polarity, category="security", source="Legacy Token Security"))

        _add("Honeypot Check", token_security.get("honeypot"), "Flagged as a honeypot (cannot sell).", "Not flagged as a honeypot.")
        _add("Mint Function", token_security.get("mint_function_present"), "Mint function present - supply can be inflated.", "No mint function present.")
        _add("Ownership", token_security.get("ownership_renounced"), "Ownership renounced.", "Ownership NOT renounced.", negative_when_true=False)
        top10 = token_security.get("top_10_holder_pct")
        if top10 is not None:
            polarity = "negative" if top10 >= 50 else ("neutral" if top10 >= 25 else "positive")
            items.append(EvidenceItem(
                label="Holder Concentration", detail=f"Top 10 holders control {top10:.0f}% of supply.",
                polarity=polarity, category="security", source="Legacy Token Security",
            ))
        return items

    # -- flat text lists (decision.reasons / decision.warnings) ----------

    def _text_list_evidence(
        self, texts: List[str], category: str, source_label: str, force_polarity: Optional[str] = None,
    ) -> List[EvidenceItem]:
        items: List[EvidenceItem] = []
        for text in texts:
            if not text:
                continue
            polarity = force_polarity or _classify_polarity(text)
            label = text.split(";")[0].split(".")[0][:80].strip() or text[:80]
            items.append(EvidenceItem(label=label, detail=text, polarity=polarity, category=category, source=source_label))
        return items

    # -- confidence explanation (Requirement 4/5) -------------------------

    def _explain_confidence(self, decision: AgentDecision, raw: Dict[str, Any]) -> ConfidenceExplanation:
        score_breakdown: Optional[Dict[str, float]] = raw.get("score_breakdown")
        score_weights: Optional[Dict[str, float]] = raw.get("score_weights")

        if score_breakdown and score_weights:
            contributions = []
            for key, score in score_breakdown.items():
                weight = score_weights.get(key)
                weight_pct = round(weight * 100, 1) if weight is not None else None
                contribution_points = round(score * weight, 2) if weight is not None else None
                max_points = round(weight * 100, 2) if weight is not None else None
                # shortfall = how many of this category's OWN possible points
                # were left on the table (weight * (100 - score)) - the
                # right metric for "what reduced confidence the most",
                # since a low score in a LOW-weight category can barely
                # move the total, while a merely-mediocre score in a
                # HIGH-weight category (e.g. multi_tf_alignment at 18.4%)
                # costs far more - ranking by raw contribution_points alone
                # would miss that. Still the exact same score*weight
                # arithmetic AIScorer already does, just the complement of it.
                shortfall = round(max_points - contribution_points, 2) if (max_points is not None and contribution_points is not None) else None
                label = _SCORE_CATEGORY_LABELS.get(key, key.replace("_", " ").title())
                note = (
                    f"{label}: {score:.0f}/100, weighted {weight_pct}% of confidence -> "
                    f"contributed {contribution_points} of a possible {max_points} points"
                    + (f" (cost {shortfall} points)." if shortfall and shortfall > 0.05 else ".")
                )
                contributions.append(ConfidenceContribution(
                    category=key, label=label, score=int(round(score)), weight_pct=weight_pct,
                    contribution_points=contribution_points, shortfall_points=shortfall, note=note,
                ))
            top_reducers = sorted(
                (c for c in contributions if c.shortfall_points and c.shortfall_points > 0.05),
                key=lambda c: c.shortfall_points, reverse=True,
            )[:4]
            top_contributors = sorted(
                contributions, key=lambda c: (c.contribution_points if c.contribution_points is not None else 0), reverse=True,
            )[:4]
            summary = (
                f"Confidence {decision.confidence_pct}% is the weighted sum of {len(contributions)} scored "
                "categories from AIScorer (SMC structure, multi-timeframe alignment, trend, volume, momentum, "
                "volatility, pattern confirmation, fundamentals) - see the breakdown below for exactly which "
                "categories helped or held it back."
            )
            return ConfidenceExplanation(
                confidence_pct=decision.confidence_pct, summary=summary, granularity="category_breakdown",
                contributions=contributions, top_contributors=top_contributors, top_reducers=top_reducers,
            )

        if decision.confidence_breakdown and any(v is not None for v in decision.confidence_breakdown.values()):
            contributions = []
            for key, score in decision.confidence_breakdown.items():
                if score is None:
                    continue
                label = _DECISION_SUBSCORE_LABELS.get(key, key.replace("_", " ").title())
                note = f"{label}: {score}/100."
                contributions.append(ConfidenceContribution(
                    category=key, label=label, score=score, weight_pct=None,
                    contribution_points=None, shortfall_points=None, note=note,
                ))
            # No per-category weight is available at this granularity (see
            # summary below), so ranking falls back to the raw 0-100 score
            # itself rather than a weighted shortfall.
            ranked = sorted(contributions, key=lambda c: (c.score if c.score is not None else 0))
            top_reducers = [c for c in ranked if c.score is not None and c.score < 70][:3]
            top_contributors = list(reversed(ranked))[:3]
            summary = (
                f"Confidence {decision.confidence_pct}% - a category-level AIScorer weighted breakdown isn't "
                "available for this report (on-chain token scans don't use AIScorer), so this shows the "
                "coarser Technical/Security/Liquidity/Smart Money/Holder/Risk sub-scores already computed by "
                "the Decision Engine instead."
            )
            return ConfidenceExplanation(
                confidence_pct=decision.confidence_pct, summary=summary, granularity="sub_score_breakdown",
                contributions=contributions, top_contributors=top_contributors, top_reducers=top_reducers,
            )

        return ConfidenceExplanation(
            confidence_pct=decision.confidence_pct,
            summary=f"Confidence {decision.confidence_pct}% - no per-category breakdown was available to explain it further.",
            granularity="unavailable",
        )
