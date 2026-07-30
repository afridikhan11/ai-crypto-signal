from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.decision_engine import AgentDecision
from app.agent.evidence_engine import EvidenceReport
from app.agent.orchestrator import TradingAgentOrchestrator
from app.agent.research_engine import ResearchNote
from app.agent.trading_coach import CoachAdvice
from app.api.dependencies import get_current_user, get_db
from app.schemas.agent import (
    AgentDecisionResponse,
    AgentQueryRequest,
    AgentQueryResponse,
    AgentRankedRow,
    CoachAdviceResponse,
    ConfidenceContributionResponse,
    ConfidenceExplanationResponse,
    EvidenceItemResponse,
    EvidenceReportResponse,
    HistoricalEvidenceResponse,
    ProviderHealthResponse,
    ResearchNoteResponse,
    ResearchSignalResponse,
)

router = APIRouter(prefix="/agent", tags=["trading-agent"])

# Process-wide ProviderManager so health telemetry accumulates across
# requests instead of resetting on every call - mirrors app.state.scanner's
# own single-instance-per-process pattern (see app/main.py), just not
# stored on app.state since it holds no live connections/background tasks
# to start or stop.
_provider_manager = None

# Phase 3: process-wide ConversationContextManager, same singleton pattern
# as _provider_manager above - constructing a fresh one per request would
# mean context never actually survives between a user's turns, defeating
# the whole point. See app/agent/conversation_context.py's module docstring
# for why this is still "session memory only, never persisted" despite
# living at process scope: a restart clears it, nothing here touches disk/DB.
_context_manager = None


def _get_provider_manager():
    global _provider_manager
    if _provider_manager is None:
        from app.agent.provider_manager import ProviderManager
        _provider_manager = ProviderManager()
    return _provider_manager


def _get_context_manager():
    global _context_manager
    if _context_manager is None:
        from app.agent.conversation_context import ConversationContextManager
        _context_manager = ConversationContextManager()
    return _context_manager


def _decision_to_response(decision: AgentDecision) -> AgentDecisionResponse:
    return AgentDecisionResponse(
        symbol_or_address=decision.symbol_or_address,
        chain=decision.chain,
        generated_at=decision.generated_at,
        overall_score=decision.overall_score,
        confidence_pct=decision.confidence_pct,
        technical_score=decision.technical_score,
        security_score=decision.security_score,
        liquidity_score=decision.liquidity_score,
        smart_money_score=decision.smart_money_score,
        holder_score=decision.holder_score,
        risk_score=decision.risk_score,
        trend=decision.trend,
        momentum=decision.momentum,
        entry=decision.entry,
        stop_loss=decision.stop_loss,
        take_profit_1=decision.take_profit_1,
        take_profit_2=decision.take_profit_2,
        take_profit_3=decision.take_profit_3,
        risk_reward=decision.risk_reward,
        final_decision=decision.final_decision,
        reasons=decision.reasons,
        warnings=decision.warnings,
        style_applied=decision.style_applied,
        confidence_breakdown=decision.confidence_breakdown,
        recommendation_level=decision.recommendation_level,
        expected_holding_time=decision.expected_holding_time,
        risk_category=decision.risk_category,
        suitable_trading_style=decision.suitable_trading_style,
    )


def _evidence_to_response(evidence: EvidenceReport) -> EvidenceReportResponse:
    def _item(i):
        return EvidenceItemResponse(label=i.label, detail=i.detail, polarity=i.polarity, category=i.category, source=i.source)

    def _contribution(c):
        return ConfidenceContributionResponse(
            category=c.category, label=c.label, score=c.score, weight_pct=c.weight_pct,
            contribution_points=c.contribution_points, shortfall_points=c.shortfall_points, note=c.note,
        )

    ce = evidence.confidence_explanation
    he = evidence.historical_evidence
    return EvidenceReportResponse(
        positive_factors=[_item(i) for i in evidence.positive_factors],
        negative_factors=[_item(i) for i in evidence.negative_factors],
        technical_evidence=[_item(i) for i in evidence.technical_evidence],
        smart_money_evidence=[_item(i) for i in evidence.smart_money_evidence],
        security_evidence=[_item(i) for i in evidence.security_evidence],
        confidence_explanation=ConfidenceExplanationResponse(
            confidence_pct=ce.confidence_pct, summary=ce.summary, granularity=ce.granularity,
            contributions=[_contribution(c) for c in ce.contributions],
            top_contributors=[_contribution(c) for c in ce.top_contributors],
            top_reducers=[_contribution(c) for c in ce.top_reducers],
        ) if ce is not None else None,
        historical_evidence=HistoricalEvidenceResponse(
            available=he.available, reason=he.reason, symbol=he.symbol, sample_size=he.sample_size,
            wins=he.wins, losses=he.losses, active=he.active, win_rate_pct=he.win_rate_pct, recent=he.recent,
        ) if he is not None else None,
        data_gaps=evidence.data_gaps,
    )


def _research_to_response(research: ResearchNote) -> ResearchNoteResponse:
    return ResearchNoteResponse(
        applicable=research.applicable,
        asset_type=research.asset_type,
        reason=research.reason,
        summary=research.summary,
        signals=[
            ResearchSignalResponse(label=s.label, value=s.value, interpretation=s.interpretation, source=s.source)
            for s in research.signals
        ],
        disclaimer=research.disclaimer,
    )


def _coach_advice_to_response(advice: CoachAdvice) -> CoachAdviceResponse:
    return CoachAdviceResponse(
        question=advice.question,
        question_label=advice.question_label,
        verdict=advice.verdict,
        reasoning=advice.reasoning,
        caution_flags=advice.caution_flags,
        disclaimer=advice.disclaimer,
    )


@router.post(
    "/query",
    response_model=AgentQueryResponse,
    summary="Trading Agent - natural-language orchestration over existing modules",
    description=(
        "Detects intent and asset type from free text, routes to the existing "
        "Market Scanner (Binance pairs incl. Gold/Silver/Oil) or Token Scanner "
        "(on-chain contracts), merges the result through the Risk Manager and "
        "Strategy Profile Manager, and returns both a structured decision and a "
        "natural-language explanation. Recalculates nothing that already exists - "
        "see app/agent/orchestrator.py's module docstring. Pass `session_id` (or "
        "rely on the authenticated user identity as an implicit session) to enable "
        "follow-ups like 'why did it fail?' or 'compare it with ETH' without "
        "repeating the asset name - see app/agent/conversation_context.py. Also returns "
        "`evidence` (Technical/Smart Money/Security evidence and a confidence breakdown), "
        "`research` (supporting macro/sentiment market intelligence, never a substitute for "
        "the decision itself), and - for 7 practical questions ('Should I enter now?', 'Should I "
        "wait?', 'Should I move my stop loss?', 'Should I take partial profit?', 'Is it too late to "
        "enter?', 'Should I scale in?', 'Should I exit?') - `coach_advice`, the AI Trading Coach's "
        "advisory answer, reasoned only from the decision/evidence/research above. See "
        "app/agent/evidence_engine.py, app/agent/research_engine.py, and app/agent/trading_coach.py."
    ),
)
async def query_agent(
    payload: AgentQueryRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: str = Depends(get_current_user),
):
    scanner = getattr(request.app.state, "scanner", None)
    orchestrator = TradingAgentOrchestrator(
        session=db, scanner=scanner,
        provider_manager=_get_provider_manager(),
        context_manager=_get_context_manager(),
    )
    # Falls back to the authenticated user's identity as an implicit,
    # single conversation session when the caller doesn't pass an explicit
    # session_id - see AgentQueryRequest.session_id's own field description
    # and conversation_context.py's "SINGLE-SESSION CAVEAT" docstring.
    session_id = payload.session_id or user
    try:
        result = await orchestrator.handle(payload.message, session_id=session_id)
    except Exception as e:
        # Same try/except -> HTTPException(502) translation used by
        # backtest.py and token_scan.py for calls that reach external
        # services - orchestrator.handle() touches multiple external
        # providers (LLM providers, market/token scanners, research
        # engine) and previously had no endpoint-level guard, so a
        # provider timeout or similar would fall through as a bare,
        # unlabeled 500 from the global handler instead of a clear 502.
        raise HTTPException(status_code=502, detail=f"Trading Agent query failed: {e}")

    return AgentQueryResponse(
        intent=result.intent,
        narrative=result.narrative,
        assets_analyzed=result.assets_analyzed,
        decision=_decision_to_response(result.decision) if result.decision else None,
        comparison=[_decision_to_response(d) for d in result.comparison] if result.comparison else None,
        ranked=[AgentRankedRow(**r) for r in result.ranked] if result.ranked else None,
        warnings=result.warnings,
        context_used=result.context_used,
        context_note=result.context_note,
        evidence=_evidence_to_response(result.evidence) if result.evidence else None,
        evidence_by_symbol=(
            {sym: _evidence_to_response(ev) for sym, ev in result.evidence_by_symbol.items()}
            if result.evidence_by_symbol else None
        ),
        research=_research_to_response(result.research) if result.research else None,
        research_by_symbol=(
            {sym: _research_to_response(rn) for sym, rn in result.research_by_symbol.items()}
            if result.research_by_symbol else None
        ),
        coach_advice=_coach_advice_to_response(result.coach_advice) if result.coach_advice else None,
    )


@router.get(
    "/providers/health",
    response_model=list[ProviderHealthResponse],
    summary="Provider Manager health telemetry",
    description="Real success/failure/latency history for each external provider the Trading Agent has called, since process start.",
)
async def provider_health(_user: str = Depends(get_current_user)):
    manager = _get_provider_manager()
    return [ProviderHealthResponse(**h) for h in manager.get_all_health().values()]


@router.delete(
    "/session",
    summary="Clear this user's conversation session (Phase 3)",
    description=(
        "Forgets the last analyzed asset/decision and turn history for the "
        "caller's conversation session. Session state is in-memory only and is "
        "never persisted, so this simply lets a user reset context on demand "
        "instead of waiting for the 1-hour idle timeout."
    ),
)
async def clear_session(session_id: str | None = None, user: str = Depends(get_current_user)):
    manager = _get_context_manager()
    cleared = manager.clear(session_id or user)
    return {"cleared": cleared}
