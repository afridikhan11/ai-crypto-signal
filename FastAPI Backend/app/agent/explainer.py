"""
Explanation Engine (Phase 2, 2026-07-27).

Turns an `AgentDecision` (already fully computed by the Decision Engine,
itself just a merge of an EXISTING report - see decision_engine.py) into
the natural-language narrative format the Trading Agent is specified to
produce. Every line below is built from real fields already present on the
decision/report - this module does no scoring of its own, it only
formats/selects what to say and in what order per intent type.

PHASE 3 ADDITION (2026-07-27): `_header()` now also surfaces the five new
Decision Engine presentation fields (Recommendation Level, Risk Category,
Expected Holding Time, Suitable Trading Style, Confidence Breakdown) - pure
formatting of fields decision_engine.py already computed, nothing new is
derived in this file.

PHASE 4 ADDITION (2026-07-27): `explain()` takes an optional `evidence`
(an `app.agent.evidence_engine.EvidenceReport`, built by the Orchestrator
AFTER the decision above - see orchestrator.py's `_build_evidence`) and, if
given, appends `_evidence_block()` - again pure formatting of an
already-built object, no new evidence is gathered or invented in this file.

PHASE 5 ADDITION (2026-07-27): `explain()` also takes an optional
`research` (an `app.agent.research_engine.ResearchNote`) and, if given and
applicable, appends `_research_block()` - clearly labeled "Market
Intelligence (supporting context)" and always placed AFTER the Decision/
Evidence sections, never blended into them, so it reads as supplementary
context rather than part of the decision itself.

PHASE 6 ADDITION (2026-07-27): `explain()` also takes an optional
`coach_advice` (an `app.agent.trading_coach.CoachAdvice`) and, if given,
appends `_coach_block()` - the AI Trading Coach's direct answer to one of
its 7 practical questions. Inserted AFTER the existing intent-specific text
(so the base Decision narrative - including the original, unmodified
should_wait/should_buy/should_exit one-liners below, kept exactly as they
were - is never removed or altered) and BEFORE the Evidence/Research
sections, so the reading order is: quick verdict -> Trading Coach's fuller
reasoning -> supporting Evidence -> supporting Market Intelligence.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from app.agent.decision_engine import AgentDecision
from app.agent.evidence_engine import EvidenceReport
from app.agent.intent_parser import AgentIntent, IntentType
from app.agent.research_engine import ResearchNote
from app.agent.trading_coach import CoachAdvice

_BREAKDOWN_LABELS = [
    ("technical", "Technical"), ("security", "Security"), ("liquidity", "Liquidity"),
    ("smart_money", "Smart Money"), ("holder", "Holder"), ("risk", "Risk"),
]


def _format_confidence_breakdown(breakdown: Dict[str, Optional[int]]) -> str:
    parts = [f"{label} {breakdown.get(key)}" if breakdown.get(key) is not None else f"{label} n/a" for key, label in _BREAKDOWN_LABELS]
    return " | ".join(parts)


class Explainer:
    def explain(
        self, decision: AgentDecision, intent: AgentIntent,
        evidence: Optional[EvidenceReport] = None, research: Optional[ResearchNote] = None,
        coach_advice: Optional[CoachAdvice] = None,
    ) -> str:
        if intent.intent_type in (IntentType.WHY_FAILED, IntentType.WHY_CONFIDENCE, IntentType.WHAT_BLOCKING):
            text = self._explain_blocking(decision)
        elif intent.intent_type == IntentType.SHOULD_WAIT:
            text = self._explain_should_wait(decision)
        elif intent.intent_type == IntentType.SHOULD_BUY:
            text = self._explain_should_buy(decision)
        elif intent.intent_type == IntentType.SHOULD_EXIT:
            text = self._explain_should_exit(decision)
        else:
            text = self._explain_general(decision)

        if coach_advice is not None:
            text = f"{text}\n\n{self._coach_block(coach_advice)}"
        if evidence is not None:
            text = f"{text}\n\n{self._evidence_block(evidence)}"
        if research is not None and research.applicable:
            text = f"{text}\n\n{self._research_block(research)}"
        return text

    def _header(self, decision: AgentDecision) -> List[str]:
        lines = [
            f"Decision: {decision.final_decision}",
            f"Confidence: {decision.confidence_pct}%",
        ]
        if decision.overall_score is not None:
            lines.append(f"Overall Score: {decision.overall_score}/100")
        if decision.recommendation_level:
            lines.append(f"Recommendation: {decision.recommendation_level}")
        if decision.risk_category:
            lines.append(f"Risk Category: {decision.risk_category}")
        if decision.expected_holding_time:
            lines.append(f"Expected Holding Time: {decision.expected_holding_time}")
        if decision.suitable_trading_style:
            lines.append(f"Suitable Trading Style: {decision.suitable_trading_style}")
        if decision.confidence_breakdown:
            lines.append(f"Confidence Breakdown: {_format_confidence_breakdown(decision.confidence_breakdown)}")
        if decision.style_applied:
            lines.append(f"Style: {decision.style_applied} (levels rescaled from the base intraday read)")
        return lines

    def _reasons_block(self, decision: AgentDecision) -> List[str]:
        lines = ["Reason:"]
        for r in decision.reasons:
            lines.append(f"- {r}")
        if decision.warnings:
            lines.append("Risk notes:")
            for w in decision.warnings:
                lines.append(f"- {w}")
        return lines

    def _levels_block(self, decision: AgentDecision) -> List[str]:
        if decision.entry is None:
            return []
        lines = [f"Entry: {decision.entry}"]
        if decision.stop_loss is not None:
            lines.append(f"Stop Loss: {decision.stop_loss}")
        if decision.take_profit_1 is not None:
            lines.append(f"Take Profit 1: {decision.take_profit_1}")
        if decision.take_profit_2 is not None:
            lines.append(f"Take Profit 2: {decision.take_profit_2}")
        if decision.take_profit_3 is not None:
            lines.append(f"Take Profit 3: {decision.take_profit_3}")
        if decision.risk_reward is not None:
            lines.append(f"Risk/Reward: 1:{decision.risk_reward}")
        return lines

    def _explain_general(self, decision: AgentDecision) -> str:
        return "\n".join(self._header(decision) + self._levels_block(decision) + self._reasons_block(decision))

    def _explain_blocking(self, decision: AgentDecision) -> str:
        blocking = [r for r in decision.reasons if "would not qualify" in r.lower() or "not qualify" in r.lower()]
        lines = self._header(decision)
        if blocking:
            lines.append("")
            lines.append("What's blocking this trade right now:")
            for r in blocking:
                lines.append(f"- {r}")
        else:
            lines.append("")
            lines.append("Nothing is currently blocking this setup - it already meets the live Signal Generator's bar.")
        if decision.warnings:
            lines.append("")
            lines.extend(["Also note:"] + [f"- {w}" for w in decision.warnings])
        return "\n".join(lines)

    def _explain_should_wait(self, decision: AgentDecision) -> str:
        if decision.final_decision in ("STRONG BUY", "STRONG SELL"):
            verdict = "No - this setup already meets the live Signal Generator's institutional-grade bar; waiting risks missing the entry."
        elif decision.final_decision == "HOLD":
            verdict = "Yes - nothing decisive yet (NEUTRAL trend/confidence). Worth waiting for a clearer read."
        else:
            verdict = "Possibly - a directional lean exists but it hasn't cleared the live signal bar yet (see reasons below)."
        return "\n".join([verdict, ""] + self._header(decision) + self._reasons_block(decision))

    def _explain_should_buy(self, decision: AgentDecision) -> str:
        if decision.final_decision in ("STRONG BUY", "BUY"):
            verdict = f"Leaning yes ({decision.final_decision})."
        elif decision.final_decision == "HOLD":
            verdict = "Not yet - the setup is NEUTRAL right now."
        else:
            verdict = f"No - the current read is {decision.final_decision}, not a buy."
        return "\n".join([verdict, ""] + self._header(decision) + self._levels_block(decision) + self._reasons_block(decision))

    def _explain_should_exit(self, decision: AgentDecision) -> str:
        if decision.final_decision in ("SELL", "STRONG SELL"):
            verdict = f"Leaning yes ({decision.final_decision}) - the current read has turned against a long position."
        else:
            verdict = f"Not based on this read - current decision is {decision.final_decision}."
        return "\n".join([verdict, ""] + self._header(decision) + self._reasons_block(decision))

    def _evidence_block(self, evidence: EvidenceReport) -> str:
        """
        Formats an already-built `EvidenceReport` (see evidence_engine.py -
        every value here was computed there, this method only lays it out
        as text). Lists are capped for readability, not filtered for
        relevance - the underlying `EvidenceReport` object (returned
        alongside the narrative via `AgentResponse.evidence`) always has
        the full, uncapped set for a caller that wants it structured.
        """
        lines = ["Evidence:"]

        if evidence.positive_factors:
            lines.append("Positive factors:")
            for item in evidence.positive_factors[:6]:
                lines.append(f"+ {item.detail} ({item.source})")
        if evidence.negative_factors:
            lines.append("Negative factors:")
            for item in evidence.negative_factors[:6]:
                lines.append(f"- {item.detail} ({item.source})")

        ce = evidence.confidence_explanation
        if ce is not None:
            lines.append("")
            lines.append(f"Confidence explained: {ce.summary}")
            if ce.top_reducers:
                lines.append("What reduced confidence most:")
                for c in ce.top_reducers:
                    lines.append(f"- {c.note}")
            if ce.top_contributors:
                lines.append("What contributed most:")
                for c in ce.top_contributors[:3]:
                    lines.append(f"+ {c.note}")

        if evidence.technical_evidence:
            lines.append("")
            lines.append("Technical evidence:")
            for item in evidence.technical_evidence[:6]:
                lines.append(f"- {item.label}: {item.detail}")
        if evidence.smart_money_evidence:
            lines.append("")
            lines.append("Smart Money evidence:")
            for item in evidence.smart_money_evidence[:6]:
                lines.append(f"- {item.label}: {item.detail}")
        if evidence.security_evidence:
            lines.append("")
            lines.append("Security evidence:")
            for item in evidence.security_evidence[:6]:
                lines.append(f"- {item.label}: {item.detail}")

        he = evidence.historical_evidence
        lines.append("")
        if he is not None and he.available:
            win_rate = f", {he.win_rate_pct}% win rate" if he.win_rate_pct is not None else ""
            lines.append(
                f"Historical evidence ({he.symbol}): {he.wins}W/{he.losses}L over the last "
                f"{he.sample_size} recorded signal(s){win_rate}, {he.active} currently active."
            )
        elif he is not None:
            lines.append(f"Historical evidence: not available - {he.reason}")

        if evidence.data_gaps:
            lines.append("")
            lines.append("Data gaps (honestly disclosed, not guessed):")
            for gap in evidence.data_gaps[:5]:
                lines.append(f"- {gap}")

        return "\n".join(lines)

    def _coach_block(self, advice: CoachAdvice) -> str:
        """Formats an already-built `CoachAdvice` (see trading_coach.py -
        every value here was computed there, this method only lays it out
        as text, same as `_evidence_block`/`_research_block` above)."""
        lines = [f"Trading Coach — {advice.question_label}", advice.verdict]
        if advice.reasoning:
            lines.append("")
            lines.append("Reasoning:")
            for r in advice.reasoning:
                lines.append(f"- {r}")
        if advice.caution_flags:
            lines.append("")
            lines.append("Caution flags:")
            for c in advice.caution_flags:
                lines.append(f"- {c}")
        if advice.disclaimer:
            lines.append("")
            lines.append(advice.disclaimer)
        return "\n".join(lines)

    def _research_block(self, research: ResearchNote) -> str:
        """Formats an already-built `ResearchNote` (see research_engine.py -
        every value here was computed there). Always clearly labeled as
        supporting context, never merged into the Decision/Evidence
        sections above - see this file's own Phase 5 docstring note."""
        lines = ["Market Intelligence (supporting context, not a trading signal):", research.summary]
        for s in research.signals:
            lines.append(f"- {s.label}: {s.value} — {s.interpretation}")
        if research.disclaimer:
            lines.append("")
            lines.append(research.disclaimer)
        return "\n".join(lines)

    def explain_comparison(
        self, decisions: List[AgentDecision], symbols: List[str],
        evidence_by_symbol: Optional[Dict[str, EvidenceReport]] = None,
        research_by_symbol: Optional[Dict[str, ResearchNote]] = None,
    ) -> str:
        lines = ["Comparison:"]
        for sym, d in zip(symbols, decisions):
            lines.append(
                f"- {sym}: {d.final_decision}, confidence {d.confidence_pct}%, "
                f"trend {d.trend}, RR {('1:' + str(d.risk_reward)) if d.risk_reward else 'n/a'}"
            )
        best = max(decisions, key=lambda d: d.confidence_pct)
        best_symbol = symbols[decisions.index(best)]
        lines.append("")
        lines.append(f"Higher confidence: {best_symbol} ({best.confidence_pct}%).")

        if evidence_by_symbol:
            for sym in symbols:
                ev = evidence_by_symbol.get(sym)
                if ev is None:
                    continue
                lines.append("")
                lines.append(f"--- Evidence for {sym} ---")
                lines.append(self._evidence_block(ev))

        if research_by_symbol:
            for sym in symbols:
                rn = research_by_symbol.get(sym)
                if rn is None or not rn.applicable:
                    continue
                lines.append("")
                lines.append(f"--- Market Intelligence for {sym} ---")
                lines.append(self._research_block(rn))

        return "\n".join(lines)

    def explain_ranked(self, title: str, rows: List[dict]) -> str:
        if not rows:
            return f"{title}: no currently ACTIVE signals match - nothing fabricated, this is a real 'none right now' result."
        lines = [f"{title}:"]
        for r in rows:
            lines.append(
                f"- {r['symbol']}: {r['direction']}, confidence {r['confidence']}%, "
                f"RR 1:{r['risk_reward']}" + (f", {r['note']}" if r.get("note") else "")
            )
        return "\n".join(lines)
