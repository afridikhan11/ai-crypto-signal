from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AgentQueryRequest(BaseModel):
    message: str = Field(..., description="Free-text command, e.g. 'Analyze BTCUSDT' or 'Why is confidence only 73 for Gold?'.")
    style: Optional[str] = Field(
        None,
        description="Optional trading style override: scalping, intraday, swing, or position. "
                     "If omitted, a style keyword in `message` is used, else defaults to intraday.",
    )
    session_id: Optional[str] = Field(
        None,
        description="Optional conversation session identifier (Phase 3). Turns sharing the same "
                     "session_id can use follow-ups like 'why did it fail?' or 'compare it with ETH' "
                     "without repeating the asset name. Session state is IN-MEMORY ONLY on the server "
                     "process and is never persisted - if omitted, the authenticated user's identity is "
                     "used as an implicit single session (see app/agent/conversation_context.py).",
    )


class AgentDecisionResponse(BaseModel):
    symbol_or_address: str
    chain: str
    generated_at: str

    overall_score: int
    confidence_pct: int
    technical_score: Optional[int] = None
    security_score: Optional[int] = None
    liquidity_score: Optional[int] = None
    smart_money_score: Optional[int] = None
    holder_score: Optional[int] = None
    risk_score: Optional[int] = None

    trend: str
    momentum: Optional[str] = None

    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit_1: Optional[float] = None
    take_profit_2: Optional[float] = None
    take_profit_3: Optional[float] = None
    risk_reward: Optional[float] = None

    final_decision: str
    reasons: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    style_applied: Optional[str] = None

    # Phase 3 additions - purely presentational, computed from the SAME
    # already-existing scores/fields above (see decision_engine.py's
    # merge() docstring for each field's exact, disclosed derivation). No
    # existing calculation was changed to add these.
    confidence_breakdown: Dict[str, Optional[int]] = Field(default_factory=dict)
    recommendation_level: str = ""
    expected_holding_time: str = ""
    risk_category: str = ""
    suitable_trading_style: str = ""


class EvidenceItemResponse(BaseModel):
    label: str
    detail: str
    polarity: str
    category: str
    source: str


class ConfidenceContributionResponse(BaseModel):
    category: str
    label: str
    score: Optional[int] = None
    weight_pct: Optional[float] = None
    contribution_points: Optional[float] = None
    shortfall_points: Optional[float] = None
    note: str


class ConfidenceExplanationResponse(BaseModel):
    confidence_pct: int
    summary: str
    granularity: str
    contributions: List[ConfidenceContributionResponse] = Field(default_factory=list)
    top_contributors: List[ConfidenceContributionResponse] = Field(default_factory=list)
    top_reducers: List[ConfidenceContributionResponse] = Field(default_factory=list)


class HistoricalEvidenceResponse(BaseModel):
    available: bool
    reason: Optional[str] = None
    symbol: Optional[str] = None
    sample_size: int = 0
    wins: int = 0
    losses: int = 0
    active: int = 0
    win_rate_pct: Optional[float] = None
    recent: List[Dict[str, Any]] = Field(default_factory=list)


class EvidenceReportResponse(BaseModel):
    """Phase 4 - see app/agent/evidence_engine.py's module docstring: every
    field here is formatted from data the Technical/Smart Money/Contract
    Security dashboards and the Decision Engine already produced, never a
    newly invented fact."""
    positive_factors: List[EvidenceItemResponse] = Field(default_factory=list)
    negative_factors: List[EvidenceItemResponse] = Field(default_factory=list)
    technical_evidence: List[EvidenceItemResponse] = Field(default_factory=list)
    smart_money_evidence: List[EvidenceItemResponse] = Field(default_factory=list)
    security_evidence: List[EvidenceItemResponse] = Field(default_factory=list)
    confidence_explanation: Optional[ConfidenceExplanationResponse] = None
    historical_evidence: Optional[HistoricalEvidenceResponse] = None
    data_gaps: List[str] = Field(default_factory=list)


class ResearchSignalResponse(BaseModel):
    label: str
    value: str
    interpretation: str
    source: str


class ResearchNoteResponse(BaseModel):
    """Phase 5 - see app/agent/research_engine.py's module docstring.
    Supporting macro/sentiment context ONLY - deliberately has no score,
    confidence, or decision field of its own, so it can never be mistaken
    for (or override) the Decision Engine's output."""
    applicable: bool
    asset_type: Optional[str] = None
    reason: Optional[str] = None
    summary: str = ""
    signals: List[ResearchSignalResponse] = Field(default_factory=list)
    disclaimer: str = ""


class CoachAdviceResponse(BaseModel):
    """Phase 6 - see app/agent/trading_coach.py's module docstring.
    Advisory only, reasoned entirely from `decision`/`evidence`/`research`
    above - deliberately has no score/confidence/decision field of its own
    so it can never be mistaken for (or override) the Decision Engine's
    output."""
    question: str
    question_label: str
    verdict: str
    reasoning: List[str] = Field(default_factory=list)
    caution_flags: List[str] = Field(default_factory=list)
    disclaimer: str = ""


class AgentRankedRow(BaseModel):
    symbol: str
    direction: str
    confidence: int
    risk_reward: float
    entry_price: float
    stop_loss: float
    take_profit: float


class AgentQueryResponse(BaseModel):
    intent: str
    narrative: str
    assets_analyzed: List[str] = Field(default_factory=list)
    decision: Optional[AgentDecisionResponse] = None
    comparison: Optional[List[AgentDecisionResponse]] = None
    ranked: Optional[List[AgentRankedRow]] = None
    warnings: List[str] = Field(default_factory=list)
    # Phase 3: set only when a follow-up was resolved from session context
    # (see app/agent/conversation_context.py's resolve_follow_up).
    context_used: bool = False
    context_note: Optional[str] = None
    # Phase 4: evidence for `decision`, or one entry per symbol for `comparison`.
    evidence: Optional[EvidenceReportResponse] = None
    evidence_by_symbol: Optional[Dict[str, EvidenceReportResponse]] = None
    # Phase 5: supporting market intelligence, same shape as evidence above.
    research: Optional[ResearchNoteResponse] = None
    research_by_symbol: Optional[Dict[str, ResearchNoteResponse]] = None
    # Phase 6: AI Trading Coach's advisory answer, only set when `intent`
    # is one of the 7 practical questions it covers (see
    # app/agent/trading_coach.py's COACH_QUESTION_BY_INTENT).
    coach_advice: Optional[CoachAdviceResponse] = None


class ProviderHealthResponse(BaseModel):
    name: str
    status: str
    total_calls: int
    total_failures: int
    recent_success_rate_pct: Optional[float] = None
    avg_latency_ms: Optional[float] = None
    last_success_at: Optional[float] = None
    last_error: Optional[str] = None
