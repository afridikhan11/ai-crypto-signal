using System.Collections.Generic;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace AI_Crypto_Signal_Pro.Models
{
    /// <summary>
    /// Request body for POST /agent/query. Matches FastAPI AgentQueryRequest
    /// schema (app/schemas/agent.py). Free-text message, optional style
    /// override, optional session_id for follow-up context (see
    /// app/agent/conversation_context.py) - when omitted the backend falls
    /// back to the authenticated user identity as an implicit session.
    /// </summary>
    public sealed class AgentQueryRequestDto
    {
        [JsonPropertyName("message")]
        public string Message { get; set; } = string.Empty;

        [JsonPropertyName("style")]
        public string? Style { get; set; }

        [JsonPropertyName("session_id")]
        public string? SessionId { get; set; }
    }

    /// <summary>Matches FastAPI AgentDecisionResponse schema - the same Decision Engine
    /// output every other screen (Dashboard, Token Scanner, Gold Signal) already shows,
    /// just reused here rather than recomputed.</summary>
    public sealed class AgentDecisionDto
    {
        [JsonPropertyName("symbol_or_address")]
        public string SymbolOrAddress { get; set; } = string.Empty;

        [JsonPropertyName("chain")]
        public string Chain { get; set; } = string.Empty;

        [JsonPropertyName("generated_at")]
        public string GeneratedAt { get; set; } = string.Empty;

        [JsonPropertyName("overall_score")]
        public int OverallScore { get; set; }

        [JsonPropertyName("confidence_pct")]
        public int ConfidencePct { get; set; }

        [JsonPropertyName("technical_score")]
        public int? TechnicalScore { get; set; }

        [JsonPropertyName("security_score")]
        public int? SecurityScore { get; set; }

        [JsonPropertyName("liquidity_score")]
        public int? LiquidityScore { get; set; }

        [JsonPropertyName("smart_money_score")]
        public int? SmartMoneyScore { get; set; }

        [JsonPropertyName("holder_score")]
        public int? HolderScore { get; set; }

        [JsonPropertyName("risk_score")]
        public int? RiskScore { get; set; }

        [JsonPropertyName("trend")]
        public string Trend { get; set; } = string.Empty;

        [JsonPropertyName("momentum")]
        public string? Momentum { get; set; }

        [JsonPropertyName("entry")]
        public double? Entry { get; set; }

        [JsonPropertyName("stop_loss")]
        public double? StopLoss { get; set; }

        [JsonPropertyName("take_profit_1")]
        public double? TakeProfit1 { get; set; }

        [JsonPropertyName("take_profit_2")]
        public double? TakeProfit2 { get; set; }

        [JsonPropertyName("take_profit_3")]
        public double? TakeProfit3 { get; set; }

        [JsonPropertyName("risk_reward")]
        public double? RiskReward { get; set; }

        [JsonPropertyName("final_decision")]
        public string FinalDecision { get; set; } = string.Empty;

        [JsonPropertyName("reasons")]
        public List<string> Reasons { get; set; } = new();

        [JsonPropertyName("warnings")]
        public List<string> Warnings { get; set; } = new();

        [JsonPropertyName("style_applied")]
        public string? StyleApplied { get; set; }

        [JsonPropertyName("confidence_breakdown")]
        public Dictionary<string, int?> ConfidenceBreakdown { get; set; } = new();

        [JsonPropertyName("recommendation_level")]
        public string RecommendationLevel { get; set; } = string.Empty;

        [JsonPropertyName("expected_holding_time")]
        public string ExpectedHoldingTime { get; set; } = string.Empty;

        [JsonPropertyName("risk_category")]
        public string RiskCategory { get; set; } = string.Empty;

        [JsonPropertyName("suitable_trading_style")]
        public string SuitableTradingStyle { get; set; } = string.Empty;
    }

    /// <summary>Matches FastAPI EvidenceItemResponse schema - one disclosed fact, never invented.</summary>
    public sealed class EvidenceItemDto
    {
        [JsonPropertyName("label")]
        public string Label { get; set; } = string.Empty;

        [JsonPropertyName("detail")]
        public string Detail { get; set; } = string.Empty;

        [JsonPropertyName("polarity")]
        public string Polarity { get; set; } = string.Empty;

        [JsonPropertyName("category")]
        public string Category { get; set; } = string.Empty;

        [JsonPropertyName("source")]
        public string Source { get; set; } = string.Empty;
    }

    /// <summary>Matches FastAPI ConfidenceContributionResponse schema.</summary>
    public sealed class ConfidenceContributionDto
    {
        [JsonPropertyName("category")]
        public string Category { get; set; } = string.Empty;

        [JsonPropertyName("label")]
        public string Label { get; set; } = string.Empty;

        [JsonPropertyName("score")]
        public int? Score { get; set; }

        [JsonPropertyName("weight_pct")]
        public double? WeightPct { get; set; }

        [JsonPropertyName("contribution_points")]
        public double? ContributionPoints { get; set; }

        [JsonPropertyName("shortfall_points")]
        public double? ShortfallPoints { get; set; }

        [JsonPropertyName("note")]
        public string Note { get; set; } = string.Empty;
    }

    /// <summary>Matches FastAPI ConfidenceExplanationResponse schema.</summary>
    public sealed class ConfidenceExplanationDto
    {
        [JsonPropertyName("confidence_pct")]
        public int ConfidencePct { get; set; }

        [JsonPropertyName("summary")]
        public string Summary { get; set; } = string.Empty;

        [JsonPropertyName("granularity")]
        public string Granularity { get; set; } = string.Empty;

        [JsonPropertyName("contributions")]
        public List<ConfidenceContributionDto> Contributions { get; set; } = new();

        [JsonPropertyName("top_contributors")]
        public List<ConfidenceContributionDto> TopContributors { get; set; } = new();

        [JsonPropertyName("top_reducers")]
        public List<ConfidenceContributionDto> TopReducers { get; set; } = new();
    }

    /// <summary>Matches FastAPI HistoricalEvidenceResponse schema. `Recent` is left as raw
    /// JsonElement rows (backend sends Dict[str, Any] per entry) - not rendered in the v1
    /// AI Assistant screen, but kept fully typed here rather than dropped, so no information
    /// silently disappears between the API and this DTO.</summary>
    public sealed class HistoricalEvidenceDto
    {
        [JsonPropertyName("available")]
        public bool Available { get; set; }

        [JsonPropertyName("reason")]
        public string? Reason { get; set; }

        [JsonPropertyName("symbol")]
        public string? Symbol { get; set; }

        [JsonPropertyName("sample_size")]
        public int SampleSize { get; set; }

        [JsonPropertyName("wins")]
        public int Wins { get; set; }

        [JsonPropertyName("losses")]
        public int Losses { get; set; }

        [JsonPropertyName("active")]
        public int Active { get; set; }

        [JsonPropertyName("win_rate_pct")]
        public double? WinRatePct { get; set; }

        [JsonPropertyName("recent")]
        public List<Dictionary<string, JsonElement>> Recent { get; set; } = new();
    }

    /// <summary>Matches FastAPI EvidenceReportResponse schema - see app/agent/evidence_engine.py:
    /// every field here is formatted from data the platform already produced, never invented.</summary>
    public sealed class EvidenceReportDto
    {
        [JsonPropertyName("positive_factors")]
        public List<EvidenceItemDto> PositiveFactors { get; set; } = new();

        [JsonPropertyName("negative_factors")]
        public List<EvidenceItemDto> NegativeFactors { get; set; } = new();

        [JsonPropertyName("technical_evidence")]
        public List<EvidenceItemDto> TechnicalEvidence { get; set; } = new();

        [JsonPropertyName("smart_money_evidence")]
        public List<EvidenceItemDto> SmartMoneyEvidence { get; set; } = new();

        [JsonPropertyName("security_evidence")]
        public List<EvidenceItemDto> SecurityEvidence { get; set; } = new();

        [JsonPropertyName("confidence_explanation")]
        public ConfidenceExplanationDto? ConfidenceExplanation { get; set; }

        [JsonPropertyName("historical_evidence")]
        public HistoricalEvidenceDto? HistoricalEvidence { get; set; }

        [JsonPropertyName("data_gaps")]
        public List<string> DataGaps { get; set; } = new();
    }

    /// <summary>Matches FastAPI ResearchSignalResponse schema.</summary>
    public sealed class ResearchSignalDto
    {
        [JsonPropertyName("label")]
        public string Label { get; set; } = string.Empty;

        [JsonPropertyName("value")]
        public string Value { get; set; } = string.Empty;

        [JsonPropertyName("interpretation")]
        public string Interpretation { get; set; } = string.Empty;

        [JsonPropertyName("source")]
        public string Source { get; set; } = string.Empty;
    }

    /// <summary>Matches FastAPI ResearchNoteResponse schema - see app/agent/research_engine.py:
    /// supporting macro/sentiment context only, deliberately has no score/confidence/decision
    /// of its own so it can never be mistaken for the Decision Engine's output.</summary>
    public sealed class ResearchNoteDto
    {
        [JsonPropertyName("applicable")]
        public bool Applicable { get; set; }

        [JsonPropertyName("asset_type")]
        public string? AssetType { get; set; }

        [JsonPropertyName("reason")]
        public string? Reason { get; set; }

        [JsonPropertyName("summary")]
        public string Summary { get; set; } = string.Empty;

        [JsonPropertyName("signals")]
        public List<ResearchSignalDto> Signals { get; set; } = new();

        [JsonPropertyName("disclaimer")]
        public string Disclaimer { get; set; } = string.Empty;
    }

    /// <summary>Matches FastAPI CoachAdviceResponse schema - see app/agent/trading_coach.py:
    /// advisory only, reasoned entirely from decision/evidence/research above, never overrides
    /// the Decision Engine.</summary>
    public sealed class CoachAdviceDto
    {
        [JsonPropertyName("question")]
        public string Question { get; set; } = string.Empty;

        [JsonPropertyName("question_label")]
        public string QuestionLabel { get; set; } = string.Empty;

        [JsonPropertyName("verdict")]
        public string Verdict { get; set; } = string.Empty;

        [JsonPropertyName("reasoning")]
        public List<string> Reasoning { get; set; } = new();

        [JsonPropertyName("caution_flags")]
        public List<string> CautionFlags { get; set; } = new();

        [JsonPropertyName("disclaimer")]
        public string Disclaimer { get; set; } = string.Empty;
    }

    /// <summary>Matches FastAPI AgentRankedRow schema (used for "rank/scan" style queries).</summary>
    public sealed class AgentRankedRowDto
    {
        [JsonPropertyName("symbol")]
        public string Symbol { get; set; } = string.Empty;

        [JsonPropertyName("direction")]
        public string Direction { get; set; } = string.Empty;

        [JsonPropertyName("confidence")]
        public int Confidence { get; set; }

        [JsonPropertyName("risk_reward")]
        public double RiskReward { get; set; }

        [JsonPropertyName("entry_price")]
        public double EntryPrice { get; set; }

        [JsonPropertyName("stop_loss")]
        public double StopLoss { get; set; }

        [JsonPropertyName("take_profit")]
        public double TakeProfit { get; set; }
    }

    /// <summary>
    /// Matches FastAPI AgentQueryResponse schema - the payload for POST /agent/query.
    /// One response can carry a single-asset decision, a multi-asset comparison, or a
    /// ranked scan, plus (when applicable) evidence, research, and coach advice - see
    /// app/schemas/agent.py's own field-by-field comments for exactly when each is set.
    /// </summary>
    public sealed class AgentQueryResponseDto
    {
        [JsonPropertyName("intent")]
        public string Intent { get; set; } = string.Empty;

        [JsonPropertyName("narrative")]
        public string Narrative { get; set; } = string.Empty;

        [JsonPropertyName("assets_analyzed")]
        public List<string> AssetsAnalyzed { get; set; } = new();

        [JsonPropertyName("decision")]
        public AgentDecisionDto? Decision { get; set; }

        [JsonPropertyName("comparison")]
        public List<AgentDecisionDto>? Comparison { get; set; }

        [JsonPropertyName("ranked")]
        public List<AgentRankedRowDto>? Ranked { get; set; }

        [JsonPropertyName("warnings")]
        public List<string> Warnings { get; set; } = new();

        [JsonPropertyName("context_used")]
        public bool ContextUsed { get; set; }

        [JsonPropertyName("context_note")]
        public string? ContextNote { get; set; }

        [JsonPropertyName("evidence")]
        public EvidenceReportDto? Evidence { get; set; }

        [JsonPropertyName("evidence_by_symbol")]
        public Dictionary<string, EvidenceReportDto>? EvidenceBySymbol { get; set; }

        [JsonPropertyName("research")]
        public ResearchNoteDto? Research { get; set; }

        [JsonPropertyName("research_by_symbol")]
        public Dictionary<string, ResearchNoteDto>? ResearchBySymbol { get; set; }

        [JsonPropertyName("coach_advice")]
        public CoachAdviceDto? CoachAdvice { get; set; }
    }

    /// <summary>Matches the payload for DELETE /agent/session - {"cleared": bool}.</summary>
    public sealed class ClearSessionResponseDto
    {
        [JsonPropertyName("cleared")]
        public bool Cleared { get; set; }
    }

    /// <summary>
    /// One turn in the AI Assistant screen's on-screen conversation log. This is a
    /// purely local, in-memory presentation wrapper (not a backend schema) - the
    /// backend's own conversation memory (app/agent/conversation_context.py) is
    /// tracked separately, server-side, keyed by SessionId.
    /// </summary>
    public sealed class AgentConversationTurn
    {
        public bool IsUser { get; set; }
        public string Text { get; set; } = string.Empty;
        public AgentQueryResponseDto? Response { get; set; }
    }
}
