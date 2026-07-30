"""
AI Trading Coach (Phase 6, 2026-07-27).

An ADVISORY layer that answers practical, position-management-style
trading questions - "Should I enter now?", "Should I wait?", "Should I move
my stop loss?", "Should I take partial profit?", "Is it too late to
enter?", "Should I scale in?", "Should I exit?" - Requirement 3.

The load-bearing constraint (Requirement 1/2/7) is the same one every prior
phase has held to: this module generates NO market analysis of its own. It
is a pure reasoning/formatting layer over three objects the Orchestrator
has ALREADY built before this module ever sees them:

  - `app.agent.decision_engine.AgentDecision` - the Decision Engine's own,
    unmodified output (entry/stop_loss/take_profit_1, final_decision,
    confidence_pct, risk_reward, risk_category, etc).
  - `app.agent.evidence_engine.EvidenceReport` - the Evidence Engine's own,
    unmodified output (positive/negative factors, confidence explanation,
    historical win/loss record).
  - `app.agent.research_engine.ResearchNote` - the Research Engine's own,
    unmodified output (macro/sentiment supporting signals).

`TradingCoach.advise()` never calls a provider, a service, the database, or
any scoring/calibration code - Requirement 2 ("must reuse the existing
Decision Engine, Evidence Engine and Research Engine", nothing else). No
new indicator is computed anywhere in this file. The ONE arithmetic
operation this module adds - an "R-multiple" (how many multiples of the
ORIGINAL computed risk distance, i.e. entry-to-stop-loss, the price has
since moved) - is built ONLY from three fields the Decision Engine already
produced (`entry`, `stop_loss`, and `raw_report["current_price"]`, which
`app.services.market_scorer.build_market_scan_report()` has always computed
and returned but which `decision_engine.py` had not previously read onto
`AgentDecision` - see `evidence_engine.py`'s `score_breakdown` precedent
from Phase 4 for the same "expose an already-computed but previously
unread value" pattern). This is disclosed reshaping of existing numbers,
not a new market read, and it degrades to an honest "not available" when
`current_price` isn't present (on-chain token scans don't compute it - see
`app.services.token_scorer.py` - so this module says so rather than
guessing).

Requirement 6 ("never override the Decision Engine") is honored by
construction: `CoachAdvice` carries no score, confidence, or decision field
of its own that could be confused with `AgentDecision`'s. Every verdict
below is explicitly traced back to the SAME `final_decision`/
`confidence_pct`/`risk_reward`/evidence/research fields the rest of the
narrative already shows, in plain language, addressed at the exact
practical question asked.

Requirement 5 ("never invent market conditions") - three question types
(move stop loss / take partial profit / scale in) inherently relate to an
ALREADY-OPEN position: its actual fill price, size, and live P&L. This
Coach has no access to any of that (no live position/account feed is in
its reuse list - see Requirement 2's exact three engines), so rather than
guess at a P&L this module doesn't know, every one of those three answers
explicitly discloses that boundary and reasons only from the CURRENT
platform read (as if a position were open in the direction the platform's
own analysis currently favors) - never a fabricated "you are up/down X".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional

from app.agent.decision_engine import AgentDecision
from app.agent.evidence_engine import EvidenceReport
from app.agent.intent_parser import IntentType
from app.agent.research_engine import ResearchNote

_LONG_DECISIONS = {"STRONG BUY", "BUY"}
_SHORT_DECISIONS = {"STRONG SELL", "SELL"}

_HIGH_CONFIDENCE_THRESHOLD = 80  # same disclosed cutoff decision_engine.py already uses for "High Conviction"
_ELEVATED_RISK_LABELS = {"Elevated Risk", "High Risk"}

_COACH_DISCLAIMER = (
    "Advisory only - reasoned entirely from the platform's existing Decision Engine, Evidence Engine "
    "and Research Engine output already shown above/below, never a new market analysis and never a "
    "substitute for the Decision Engine's own verdict. This Coach has no access to your actual filled "
    "entry price, live position size, or account P&L (no live position/account feed is reused here - "
    "only the three named engines) - where a question depends on that, the gap is disclosed explicitly "
    "rather than guessed."
)

_POSITION_DATA_GAP_NOTE = (
    "This Coach cannot see your actual open position (fill price, size, or live P&L) - it only reasons "
    "from the platform's CURRENT read for this asset, as if a position were open in the direction the "
    "Decision Engine currently favors."
)


class CoachQuestion(str, Enum):
    ENTER_NOW = "enter_now"
    WAIT = "wait"
    MOVE_STOP_LOSS = "move_stop_loss"
    TAKE_PARTIAL_PROFIT = "take_partial_profit"
    TOO_LATE_TO_ENTER = "too_late_to_enter"
    SCALE_IN = "scale_in"
    EXIT = "exit"


_QUESTION_LABELS: Dict[CoachQuestion, str] = {
    CoachQuestion.ENTER_NOW: "Should I enter now?",
    CoachQuestion.WAIT: "Should I wait?",
    CoachQuestion.MOVE_STOP_LOSS: "Should I move my stop loss?",
    CoachQuestion.TAKE_PARTIAL_PROFIT: "Should I take partial profit?",
    CoachQuestion.TOO_LATE_TO_ENTER: "Is it too late to enter?",
    CoachQuestion.SCALE_IN: "Should I scale in?",
    CoachQuestion.EXIT: "Should I exit?",
}

# Maps the Intent Parser's existing/new intent types onto the 7 coaching
# questions above - the ONLY place this mapping lives, so the Orchestrator
# stays a thin router (Requirement 7: advisory layer only, not a new
# routing concept). SHOULD_WAIT/SHOULD_BUY/SHOULD_EXIT already existed
# (Phase 2); the other four are new, additive IntentType members (see
# intent_parser.py's Phase 6 additions).
COACH_QUESTION_BY_INTENT: Dict[IntentType, CoachQuestion] = {
    IntentType.SHOULD_BUY: CoachQuestion.ENTER_NOW,
    IntentType.SHOULD_WAIT: CoachQuestion.WAIT,
    IntentType.SHOULD_EXIT: CoachQuestion.EXIT,
    IntentType.MOVE_STOP_LOSS: CoachQuestion.MOVE_STOP_LOSS,
    IntentType.TAKE_PARTIAL_PROFIT: CoachQuestion.TAKE_PARTIAL_PROFIT,
    IntentType.TOO_LATE_TO_ENTER: CoachQuestion.TOO_LATE_TO_ENTER,
    IntentType.SCALE_IN: CoachQuestion.SCALE_IN,
}


@dataclass
class CoachAdvice:
    """Deliberately has no score/confidence/decision field - see module
    docstring's Requirement 6 note. `question`/`question_label` identify
    which of the 7 practical questions this answers; `verdict` is the short
    direct answer; `reasoning` and `caution_flags` are each a list of
    plain-language lines, every one of them traceable to a real field on
    the `AgentDecision`/`EvidenceReport`/`ResearchNote` passed in."""
    question: str
    question_label: str
    verdict: str
    reasoning: List[str] = field(default_factory=list)
    caution_flags: List[str] = field(default_factory=list)
    disclaimer: str = _COACH_DISCLAIMER


def _current_price(decision: AgentDecision) -> Optional[float]:
    """Reads `current_price` off the ALREADY-COMPUTED raw report
    (`build_market_scan_report()` has always returned this key - see
    market_scorer.py - it was simply never read onto `AgentDecision` until
    now). Returns None honestly for on-chain token scans, which don't
    surface this key at the report's top level (see token_scorer.py)."""
    raw = decision.raw_report or {}
    cp = raw.get("current_price")
    return float(cp) if isinstance(cp, (int, float)) else None


def _is_long_bias(decision: AgentDecision) -> Optional[bool]:
    """True/False for a clear directional read, None when NEUTRAL/HOLD -
    reuses ONLY `final_decision` (already computed) and, as a fallback,
    the entry-vs-stop-loss relationship (already computed) when
    final_decision itself is HOLD but levels still exist."""
    if decision.final_decision in _LONG_DECISIONS:
        return True
    if decision.final_decision in _SHORT_DECISIONS:
        return False
    if decision.entry is not None and decision.stop_loss is not None:
        return decision.stop_loss < decision.entry
    return None


def _risk_distance(decision: AgentDecision) -> Optional[float]:
    if decision.entry is None or decision.stop_loss is None:
        return None
    dist = abs(decision.entry - decision.stop_loss)
    return dist if dist > 0 else None


def _r_multiple(decision: AgentDecision) -> Optional[float]:
    """How many multiples of the ORIGINAL risk distance (entry-to-stop,
    both already computed by the Decision Engine) the current price has
    since moved in the position's favor. A standard, disclosed trading
    concept (not a new indicator) - positive means favorable movement,
    negative means adverse movement. Returns None whenever any of the
    three already-existing inputs (current_price/entry/stop_loss) is
    unavailable, rather than guessing."""
    cp = _current_price(decision)
    risk = _risk_distance(decision)
    is_long = _is_long_bias(decision)
    if cp is None or risk is None or is_long is None or decision.entry is None:
        return None
    moved = (cp - decision.entry) if is_long else (decision.entry - cp)
    return round(moved / risk, 2)


def _stop_breached(decision: AgentDecision) -> Optional[bool]:
    cp = _current_price(decision)
    is_long = _is_long_bias(decision)
    if cp is None or is_long is None or decision.stop_loss is None:
        return None
    return cp <= decision.stop_loss if is_long else cp >= decision.stop_loss


def _target_reached(decision: AgentDecision) -> Optional[bool]:
    cp = _current_price(decision)
    is_long = _is_long_bias(decision)
    if cp is None or is_long is None or decision.take_profit_1 is None:
        return None
    return cp >= decision.take_profit_1 if is_long else cp <= decision.take_profit_1


def _price_context_lines(decision: AgentDecision) -> List[str]:
    """Plain-language restatement of current_price vs. entry/stop/target -
    every value already existed on the report/decision before this file
    read them; nothing here is a new market read."""
    cp = _current_price(decision)
    if cp is None:
        return [
            "Current live price isn't included in this report (on-chain token scans don't compute one - "
            "see the Evidence section's Technical evidence for the closest available price context "
            "instead), so distance-from-entry can't be assessed here without guessing."
        ]
    lines = [f"Current price: {cp}."]
    if decision.entry is not None:
        lines.append(f"Computed entry: {decision.entry}, stop-loss: {decision.stop_loss}, take-profit 1: {decision.take_profit_1}.")
        r = _r_multiple(decision)
        if r is not None:
            lines.append(f"That is roughly {r}R from entry (R = the original entry-to-stop risk distance).")
    return lines


def _evidence_balance_line(evidence: Optional[EvidenceReport]) -> Optional[str]:
    if evidence is None:
        return None
    pos, neg = len(evidence.positive_factors), len(evidence.negative_factors)
    return f"Evidence on record: {pos} positive factor(s) vs {neg} negative factor(s) (see Evidence section above/below)."


def _evidence_caution_flags(evidence: Optional[EvidenceReport]) -> List[str]:
    if evidence is None:
        return []
    flags: List[str] = []
    ce = evidence.confidence_explanation
    if ce is not None and ce.top_reducers:
        flags.append(f"Confidence was reduced most by: {ce.top_reducers[0].label} ({ce.top_reducers[0].note})")
    if evidence.data_gaps:
        flags.append(f"{len(evidence.data_gaps)} data gap(s) disclosed in Evidence (see 'Data gaps' above/below).")
    return flags


def _research_caution_flags(research: Optional[ResearchNote]) -> List[str]:
    if research is None or not research.applicable:
        return []
    flags: List[str] = []
    for s in research.signals:
        low = s.interpretation.lower()
        if any(k in low for k in ("contrarian", "headwind", "extra caution", "squeeze")):
            flags.append(f"{s.label}: {s.value} — {s.interpretation}")
    return flags


class TradingCoach:
    """Pure reasoning/formatting layer - see module docstring. `advise()`
    is the only public entry point; every private `_advise_*` method below
    takes the SAME three already-built objects and returns a `CoachAdvice`,
    never fetching or computing a new market read."""

    def advise(
        self, question: CoachQuestion, decision: AgentDecision,
        evidence: Optional[EvidenceReport] = None, research: Optional[ResearchNote] = None,
    ) -> CoachAdvice:
        handler = self._HANDLERS[question]
        return handler(self, decision, evidence, research)

    # -- 1. Should I enter now? -------------------------------------------

    def _advise_enter_now(
        self, decision: AgentDecision, evidence: Optional[EvidenceReport], research: Optional[ResearchNote],
    ) -> CoachAdvice:
        if decision.final_decision in _LONG_DECISIONS or decision.final_decision in _SHORT_DECISIONS:
            conviction = "high-conviction" if decision.confidence_pct >= _HIGH_CONFIDENCE_THRESHOLD else "moderate-conviction"
            verdict = f"Leaning yes - current read is {decision.final_decision} ({decision.recommendation_level}), a {conviction} setup."
        elif decision.final_decision == "HOLD":
            verdict = f"Not yet - current read is HOLD ({decision.recommendation_level}); no directional edge to act on right now."
        else:
            verdict = f"No - current read is {decision.final_decision}, not an entry signal."

        reasoning = [
            f"Confidence: {decision.confidence_pct}%. Risk category: {decision.risk_category}.",
        ] + _price_context_lines(decision)
        bal = _evidence_balance_line(evidence)
        if bal:
            reasoning.append(bal)
        if decision.risk_reward is not None:
            reasoning.append(f"Computed risk/reward for this setup: 1:{decision.risk_reward}.")

        target_reached = _target_reached(decision)
        caution = list(_evidence_caution_flags(evidence)) + list(_research_caution_flags(research))
        if target_reached:
            caution.append("Current price has already reached the computed take-profit target - entering fresh here no longer matches the original risk/reward.")
        if decision.warnings:
            caution.extend(decision.warnings)

        return CoachAdvice(
            question=CoachQuestion.ENTER_NOW.value, question_label=_QUESTION_LABELS[CoachQuestion.ENTER_NOW],
            verdict=verdict, reasoning=reasoning, caution_flags=caution,
        )

    # -- 2. Should I wait? --------------------------------------------------

    def _advise_wait(
        self, decision: AgentDecision, evidence: Optional[EvidenceReport], research: Optional[ResearchNote],
    ) -> CoachAdvice:
        if decision.final_decision in ("STRONG BUY", "STRONG SELL"):
            verdict = "No - this setup already meets the live Signal Generator's institutional-grade bar; waiting risks missing the entry."
        elif decision.final_decision == "HOLD":
            verdict = "Yes - nothing decisive yet (HOLD/NEUTRAL read). Worth waiting for a clearer signal."
        else:
            verdict = f"Possibly - a {decision.final_decision} lean exists but confidence ({decision.confidence_pct}%) hasn't reached a high-conviction bar yet."

        reasoning = [f"Recommendation: {decision.recommendation_level}."] + _price_context_lines(decision)
        ce = evidence.confidence_explanation if evidence is not None else None
        if ce is not None and ce.top_reducers:
            reasoning.append("What would need to improve: " + "; ".join(c.label for c in ce.top_reducers[:2]) + ".")

        caution = list(_evidence_caution_flags(evidence)) + list(_research_caution_flags(research))

        return CoachAdvice(
            question=CoachQuestion.WAIT.value, question_label=_QUESTION_LABELS[CoachQuestion.WAIT],
            verdict=verdict, reasoning=reasoning, caution_flags=caution,
        )

    # -- 3. Is it too late to enter? ----------------------------------------

    def _advise_too_late_to_enter(
        self, decision: AgentDecision, evidence: Optional[EvidenceReport], research: Optional[ResearchNote],
    ) -> CoachAdvice:
        r = _r_multiple(decision)
        stop_breached = _stop_breached(decision)
        target_reached = _target_reached(decision)

        if r is None:
            verdict = "Can't tell honestly - current live price isn't available in this report to compare against the computed entry."
        elif target_reached:
            verdict = "Yes - price has already reached the computed take-profit target; the original risk/reward for this setup no longer applies."
        elif stop_breached:
            verdict = "The original stop-loss level has already been breached at the current price - entering here would not be the same setup that was computed."
        elif r >= 1.5:
            verdict = f"Getting late - price has already moved about {r}R in the setup's favor, well past the original entry zone."
        elif r >= 0.5:
            verdict = f"Somewhat - price has moved about {r}R from the computed entry, but is still within a reasonable range of it."
        else:
            verdict = f"No - price is still close to the computed entry (about {r}R moved)."

        reasoning = _price_context_lines(decision) + [f"Current decision: {decision.final_decision} ({decision.recommendation_level})."]
        caution = list(_evidence_caution_flags(evidence)) + list(_research_caution_flags(research))

        return CoachAdvice(
            question=CoachQuestion.TOO_LATE_TO_ENTER.value, question_label=_QUESTION_LABELS[CoachQuestion.TOO_LATE_TO_ENTER],
            verdict=verdict, reasoning=reasoning, caution_flags=caution,
        )

    # -- 4. Should I move my stop loss? -------------------------------------

    def _advise_move_stop_loss(
        self, decision: AgentDecision, evidence: Optional[EvidenceReport], research: Optional[ResearchNote],
    ) -> CoachAdvice:
        r = _r_multiple(decision)
        is_long = _is_long_bias(decision)
        stop_breached = _stop_breached(decision)

        if decision.entry is None or decision.stop_loss is None:
            verdict = "Not enough data - this report has no computed entry/stop-loss levels to reason about."
        elif stop_breached:
            verdict = "The computed stop-loss level has already been breached at the current price - a position built on this setup would already be stopped out."
        elif r is None:
            verdict = "Can't assess distance moved - current live price isn't available in this report."
        elif r >= 2.0:
            verdict = f"Worth considering - price has moved about {r}R in your favor. A common, general practice at this stage is trailing the stop to lock in some of that move."
        elif r >= 1.0:
            verdict = f"Worth considering - price has covered its original 1R (about {r}R now). A common, general practice here is moving the stop to breakeven so the trade can no longer become a loss."
        else:
            verdict = f"No signal to move it yet - price hasn't covered its original risk distance (about {r}R so far)."

        reasoning = [_POSITION_DATA_GAP_NOTE] + _price_context_lines(decision)
        reasoning.append(
            f"Current platform read is still {decision.final_decision} ({decision.recommendation_level}) - "
            f"this advice assumes a position aligned with that direction ({'long' if is_long else 'short' if is_long is False else 'unclear'})."
        )
        caution = list(_evidence_caution_flags(evidence)) + list(_research_caution_flags(research))
        if decision.final_decision in _SHORT_DECISIONS and is_long:
            caution.append("The current read has flipped bearish since the original long-biased levels were computed - re-check direction before relying on these levels.")
        elif decision.final_decision in _LONG_DECISIONS and is_long is False:
            caution.append("The current read has flipped bullish since the original short-biased levels were computed - re-check direction before relying on these levels.")

        return CoachAdvice(
            question=CoachQuestion.MOVE_STOP_LOSS.value, question_label=_QUESTION_LABELS[CoachQuestion.MOVE_STOP_LOSS],
            verdict=verdict, reasoning=reasoning, caution_flags=caution,
        )

    # -- 5. Should I take partial profit? -----------------------------------

    def _advise_take_partial_profit(
        self, decision: AgentDecision, evidence: Optional[EvidenceReport], research: Optional[ResearchNote],
    ) -> CoachAdvice:
        r = _r_multiple(decision)
        target_reached = _target_reached(decision)

        if decision.take_profit_1 is None:
            verdict = "Not enough data - this report has no computed take-profit level to reason about."
        elif target_reached:
            verdict = "Yes, worth considering - price has already reached (or passed) the computed take-profit target."
        elif r is None:
            verdict = "Can't assess progress toward target - current live price isn't available in this report."
        elif r >= 1.5:
            verdict = f"Worth considering - price is about {r}R into the move, a stage many traders use to lock in part of the gain."
        else:
            verdict = f"Not yet based on distance alone - price is only about {r}R into the move toward the computed target."

        reasoning = [
            _POSITION_DATA_GAP_NOTE,
            (
                f"This platform's signal engine currently computes a single take-profit target (TP1 = "
                f"{decision.take_profit_1}) - it does not compute intermediate partial-profit levels "
                f"(TP2/TP3 are not populated: {decision.take_profit_2}, {decision.take_profit_3}), so no "
                "specific partial-exit price beyond TP1 itself can be given here."
            ),
        ] + _price_context_lines(decision)

        caution = list(_evidence_caution_flags(evidence)) + list(_research_caution_flags(research))
        ce = evidence.confidence_explanation if evidence is not None else None
        if ce is not None and len(evidence.negative_factors) > len(evidence.positive_factors):
            caution.append("Negative factors currently outnumber positive ones in the Evidence section - a reason conviction may be softening, worth weighing toward taking some profit.")

        return CoachAdvice(
            question=CoachQuestion.TAKE_PARTIAL_PROFIT.value, question_label=_QUESTION_LABELS[CoachQuestion.TAKE_PARTIAL_PROFIT],
            verdict=verdict, reasoning=reasoning, caution_flags=caution,
        )

    # -- 6. Should I scale in? -----------------------------------------------

    def _advise_scale_in(
        self, decision: AgentDecision, evidence: Optional[EvidenceReport], research: Optional[ResearchNote],
    ) -> CoachAdvice:
        strong = decision.final_decision in ("STRONG BUY", "STRONG SELL")
        directional = decision.final_decision in _LONG_DECISIONS or decision.final_decision in _SHORT_DECISIONS
        high_confidence = decision.confidence_pct >= _HIGH_CONFIDENCE_THRESHOLD
        elevated_risk = decision.risk_category in _ELEVATED_RISK_LABELS or "Unknown" in decision.risk_category

        if not directional:
            verdict = f"No - current read is {decision.final_decision}, not a directional signal to add to."
        elif decision.risk_category in _ELEVATED_RISK_LABELS:
            verdict = f"No - current risk category is '{decision.risk_category}'; adding exposure would increase an already-elevated risk read."
        elif strong and high_confidence:
            verdict = f"The current read still supports this direction ({decision.final_decision}, {decision.confidence_pct}% confidence) - scaling in wouldn't contradict the platform's own analysis."
        elif directional:
            verdict = f"Mixed - current read is {decision.final_decision} but confidence ({decision.confidence_pct}%) is below the high-conviction bar; scaling in here is a weaker case."
        else:
            verdict = "Not enough data to assess."

        reasoning = [
            _POSITION_DATA_GAP_NOTE,
            (
                "This Coach has no visibility into your existing position size, average entry, or the Risk "
                "Manager's own portfolio-level checks (correlation, daily loss limits) that a real add would "
                "need to pass - those live outside the Decision/Evidence/Research Engines this Coach reuses."
            ),
        ] + _price_context_lines(decision)

        caution = list(_evidence_caution_flags(evidence)) + list(_research_caution_flags(research))
        if elevated_risk:
            caution.append(f"Risk category: {decision.risk_category}.")

        return CoachAdvice(
            question=CoachQuestion.SCALE_IN.value, question_label=_QUESTION_LABELS[CoachQuestion.SCALE_IN],
            verdict=verdict, reasoning=reasoning, caution_flags=caution,
        )

    # -- 7. Should I exit? ---------------------------------------------------

    def _advise_exit(
        self, decision: AgentDecision, evidence: Optional[EvidenceReport], research: Optional[ResearchNote],
    ) -> CoachAdvice:
        stop_breached = _stop_breached(decision)
        target_reached = _target_reached(decision)

        if stop_breached:
            verdict = "Yes - the computed stop-loss level has already been breached at the current price; a position built on this setup would already have been stopped out."
        elif decision.final_decision in _SHORT_DECISIONS:
            verdict = f"Leaning yes - the current read has turned to {decision.final_decision}, against a long-biased position."
        elif target_reached:
            verdict = "Worth considering - price has already reached the computed take-profit target; many traders would already be out or trailing a stop here."
        else:
            verdict = f"Not based on this read - current decision is {decision.final_decision} ({decision.recommendation_level})."

        reasoning = [_POSITION_DATA_GAP_NOTE] + _price_context_lines(decision)
        bal = _evidence_balance_line(evidence)
        if bal:
            reasoning.append(bal)

        caution = list(_evidence_caution_flags(evidence)) + list(_research_caution_flags(research))
        if decision.warnings:
            caution.extend(decision.warnings)

        return CoachAdvice(
            question=CoachQuestion.EXIT.value, question_label=_QUESTION_LABELS[CoachQuestion.EXIT],
            verdict=verdict, reasoning=reasoning, caution_flags=caution,
        )

    _HANDLERS: Dict[CoachQuestion, Callable[["TradingCoach", AgentDecision, Optional[EvidenceReport], Optional[ResearchNote]], CoachAdvice]]


TradingCoach._HANDLERS = {
    CoachQuestion.ENTER_NOW: TradingCoach._advise_enter_now,
    CoachQuestion.WAIT: TradingCoach._advise_wait,
    CoachQuestion.TOO_LATE_TO_ENTER: TradingCoach._advise_too_late_to_enter,
    CoachQuestion.MOVE_STOP_LOSS: TradingCoach._advise_move_stop_loss,
    CoachQuestion.TAKE_PARTIAL_PROFIT: TradingCoach._advise_take_partial_profit,
    CoachQuestion.SCALE_IN: TradingCoach._advise_scale_in,
    CoachQuestion.EXIT: TradingCoach._advise_exit,
}
