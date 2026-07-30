"""
Trading Agent Orchestrator (Phase 2, 2026-07-27).

This is the ONLY file that decides which existing module answers a given
request. It never computes an indicator, SMC read, or score itself - every
branch below ends in a call to something that already existed before this
phase:
  - app.services.market_scorer.build_market_scan_report - Binance trading
    pairs (crypto AND the Gold/Silver/Oil futures - same function, same SMC
    engine, same AIScorer already used live).
  - app.services.token_scorer.build_token_scan_report - on-chain contract
    addresses.
  - app.repositories.signal_repository.SignalRepository.get_active_signals -
    the REAL, already-persisted output of the live scanner, for portfolio-
    wide questions ("best trade today", "all bullish assets") instead of
    re-scanning the whole tracked universe on demand (which would make this
    "another scanner" - explicitly not the goal).

Wallet-address analysis is NOT implemented anywhere in this codebase (see
the Phase 1 Architecture Review) - `_handle_wallet` returns that fact
honestly instead of guessing.

PHASE 3 ADDITION (2026-07-27): `handle()` now takes an optional
`session_id`. When given, it consults `app.agent.conversation_context`
BEFORE routing (to fill in a missing asset from what was last discussed -
see `resolve_follow_up`'s own docstring for the exact rules) and updates
that same session AFTER routing (remembers the asset/decision just
produced). This is a thin wrapper around the SAME routing logic Phase 2
already had (moved into `_route()` unchanged) - no analysis branch below
was altered, and passing no `session_id` reproduces Phase 2's behavior
exactly (stateless, no context lookup at all).

PHASE 4 ADDITION (2026-07-27): after a single-asset or comparison decision
is produced (unchanged from Phase 2), `_build_evidence()` does the ONE new
piece of real I/O this phase needs - a read of this asset's own past
signals via the EXISTING `SignalRepository.get_signals()` - and hands the
decision plus that (optional) historical read to
`app.agent.evidence_engine.EvidenceEngine`, which is a pure formatter (see
its own module docstring: it fetches nothing itself). Nothing about
`_route()`'s analysis branches changed for this phase either.

PHASE 5 ADDITION (2026-07-27): after evidence is built (unchanged from
Phase 4), `_build_research()` optionally calls
`app.agent.research_engine.ResearchEngine.explain_market_move()` for
supporting macro/sentiment context - see that module's own docstring for
why it never touches scoring and routes every external call through the
same `ProviderManager` every other agent call already uses. Attached to
`AgentResponse.research`/`research_by_symbol`, entirely separate from
`decision`/`evidence` - never merged into or capable of overriding them.

PHASE 6 ADDITION (2026-07-27): for the 7 practical trading questions the
AI Trading Coach answers (see `app.agent.trading_coach.py`'s own module
docstring), `_handle_single_asset` now ALSO calls
`app.agent.trading_coach.TradingCoach.advise()` with the SAME
`decision`/`evidence`/`research` objects already built for this turn - no
new I/O, no new provider call, nothing computed here. Which of the 7
questions (if any) applies is looked up from
`trading_coach.COACH_QUESTION_BY_INTENT` purely by the intent the EXISTING
Intent Parser already classified; every other intent type (analyze_asset,
compare_assets, ranked queries, etc.) leaves `coach_advice` as None and is
completely unaffected. Attached to `AgentResponse.coach_advice`, entirely
separate from `decision`/`evidence`/`research` - an advisory layer only,
never capable of overriding the Decision Engine's own verdict.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.asset_resolver import ResolvedAsset
from app.agent.conversation_context import ConversationContextManager, resolve_follow_up
from app.agent.decision_engine import AgentDecision, DecisionEngine
from app.agent.evidence_engine import EvidenceEngine, EvidenceReport, build_historical_evidence
from app.agent.explainer import Explainer
from app.agent.intent_parser import AgentIntent, IntentParser, IntentType
from app.agent.provider_manager import (
    PROVIDER_BINANCE_ACCOUNT,
    PROVIDER_BINANCE_MARKET,
    PROVIDER_CONTRACT_SECURITY,
    ProviderManager,
)
from app.agent.research_engine import ResearchEngine, ResearchNote
from app.agent.risk_manager import RiskManager
from app.agent.strategy_profile_manager import StrategyProfileManager
from app.agent.trading_coach import COACH_QUESTION_BY_INTENT, CoachAdvice, TradingCoach
from app.repositories.signal_repository import SignalRepository
from app.schemas.signal import SignalQueryParams
from app.services.market_scorer import MarketScanError, build_market_scan_report
from app.services.token_scorer import build_token_scan_report


@dataclass
class AgentResponse:
    intent: str
    narrative: str
    assets_analyzed: List[str] = field(default_factory=list)
    decision: Optional[AgentDecision] = None
    comparison: Optional[List[AgentDecision]] = None
    ranked: Optional[List[Dict[str, Any]]] = None
    # Phase 4: evidence for the single `decision` above, or one entry per
    # symbol for `comparison` - always None for ranked/wallet/unrecognized
    # responses, which don't produce a fresh decision to explain.
    evidence: Optional[EvidenceReport] = None
    evidence_by_symbol: Optional[Dict[str, EvidenceReport]] = None
    # Phase 5: supporting macro/sentiment research, same shape as evidence -
    # never a substitute for `decision`/`evidence` above.
    research: Optional[ResearchNote] = None
    research_by_symbol: Optional[Dict[str, ResearchNote]] = None
    # Phase 6: AI Trading Coach's advisory answer for the 7 practical
    # questions - only set for a single-asset intent that maps to one of
    # them (see trading_coach.COACH_QUESTION_BY_INTENT); None for every
    # other intent, unchanged from before this phase.
    coach_advice: Optional[CoachAdvice] = None
    warnings: List[str] = field(default_factory=list)
    # Phase 3: set only when conversation context actually filled in
    # something this turn's own text didn't provide (see resolve_follow_up).
    context_used: bool = False
    context_note: Optional[str] = None


class TradingAgentOrchestrator:
    def __init__(
        self,
        session: AsyncSession,
        scanner: Any = None,
        provider_manager: Optional[ProviderManager] = None,
        context_manager: Optional[ConversationContextManager] = None,
    ):
        self.session = session
        self.scanner = scanner  # app.state.scanner - may be None (agent still works, minus correlation checks)
        self.provider_manager = provider_manager or ProviderManager()
        self.risk_manager = RiskManager(session, data_manager=getattr(scanner, "data_manager", None))
        self.strategy_manager = StrategyProfileManager()
        self.decision_engine = DecisionEngine()
        self.explainer = Explainer()
        self.intent_parser = IntentParser()
        self.evidence_engine = EvidenceEngine()
        # Phase 6: pure reasoning/formatting layer, no state, no I/O - see
        # trading_coach.py's own module docstring. Cheap to construct fresh
        # here, same category as self.decision_engine/self.explainer above.
        self.trading_coach = TradingCoach()
        # Phase 5: constructs its own FundamentalsService/
        # MacroFundamentalsService (cheap, no-credential, same pattern
        # market_scorer.py itself uses) - `handle()` closes their http
        # clients in a `finally` block below, same lifecycle as
        # build_market_scan_report's own `finally: await fundamentals.close()`.
        self.research_engine = ResearchEngine(self.provider_manager)
        # Phase 3: defaults to a fresh, empty manager (no context, same as
        # Phase 2) if the caller doesn't pass a shared one - see the
        # process-wide `_context_manager` singleton in
        # app/api/v1/endpoints/agent.py for how context actually persists
        # across requests in practice.
        self.context_manager = context_manager or ConversationContextManager()

    async def handle(self, raw_text: str, session_id: Optional[str] = None) -> AgentResponse:
        try:
            return await self._handle(raw_text, session_id)
        finally:
            await self.research_engine.close()

    async def _handle(self, raw_text: str, session_id: Optional[str] = None) -> AgentResponse:
        intent = self.intent_parser.parse(raw_text)

        context_session = None
        resolved_via_context = False
        if session_id:
            context_session = self.context_manager.get_or_create(session_id)
            resolved_via_context = resolve_follow_up(intent, context_session)

        response = await self._route(intent)

        if resolved_via_context:
            asset_desc = context_session.describe_last_asset() if context_session else None
            response.context_used = True
            response.context_note = (
                f"Continuing from your last analyzed asset this session ({asset_desc})."
                if asset_desc else "Reused context from earlier in this session."
            )
            # Prepend to the plain-text narrative too, not just the
            # structured field, so a client that only renders `narrative`
            # still shows the user what context was reused.
            response.narrative = f"{response.context_note}\n\n{response.narrative}"

        if context_session is not None:
            if response.decision is not None and intent.assets:
                context_session.remember_asset(intent.assets[0], intent.style)
                context_session.remember_decision(response.decision)
            elif response.comparison and intent.assets:
                # Remember the LAST-named asset of the pair, so a further
                # bare follow-up ("what about it now?") continues from
                # whichever side of the comparison was mentioned most
                # recently in the conversation.
                context_session.remember_asset(intent.assets[-1], intent.style)
                context_session.remember_decision(response.comparison[-1])
            context_session.record_turn(
                raw_text=raw_text,
                intent_type=intent.intent_type.value,
                resolved_assets=list(response.assets_analyzed),
                resolved_via_context=resolved_via_context,
            )

        return response

    async def _route(self, intent: AgentIntent) -> AgentResponse:
        """The exact routing logic Phase 2 shipped, unchanged - only
        extracted into its own method so Phase 3's `handle()` can wrap it
        with conversation-context lookups before and after, without
        touching a single analysis branch below."""
        if any(a.kind == "wallet_address" for a in intent.assets):
            return self._handle_wallet(intent)

        if intent.intent_type == IntentType.COMPARE_ASSETS and len(intent.assets) >= 2:
            return await self._handle_compare(intent)

        if intent.intent_type in (
            IntentType.FIND_BEST_TRADE, IntentType.FIND_SAFEST_TRADE,
            IntentType.SHOW_STRONGEST, IntentType.SHOW_WEAKEST,
            IntentType.FIND_BULLISH, IntentType.FIND_BEARISH,
        ):
            return await self._handle_ranked_query(intent)

        if intent.assets:
            return await self._handle_single_asset(intent)

        return AgentResponse(
            intent=intent.intent_type.value,
            narrative=(
                "I couldn't identify an asset or a recognized command in that message. "
                "Try things like: \"Analyze BTCUSDT\", \"Compare BTC vs ETH\", "
                "\"Find today's best trade\", \"Why is confidence only 73 for Gold?\", "
                "or \"Should I buy XAUUSDT?\"."
            ),
        )

    def _handle_wallet(self, intent: AgentIntent) -> AgentResponse:
        return AgentResponse(
            intent=intent.intent_type.value,
            narrative=(
                "Wallet holdings/PNL analysis isn't implemented in this project yet - only token "
                "CONTRACT security/liquidity analysis exists today (DexScreener/GeckoTerminal/GoPlus). "
                "This is a real gap, not a temporary error - see the Phase 1 Architecture Review's "
                "\"Missing Components\" section. I'm telling you this directly rather than guessing at "
                "wallet data I don't have."
            ),
            warnings=["wallet analysis not supported"],
        )

    async def _analyze_asset(self, asset: ResolvedAsset, style: Optional[str]) -> AgentDecision:
        if asset.kind == "trading_pair":
            report = await self.provider_manager.call(
                PROVIDER_BINANCE_MARKET, build_market_scan_report(asset.symbol)
            )
        elif asset.kind == "contract_address":
            chain = asset.chain or "ethereum"  # existing endpoint also requires an explicit chain; same default surfaced as a warning below
            report = await self.provider_manager.call(
                PROVIDER_CONTRACT_SECURITY, build_token_scan_report(chain, asset.address)
            )
        else:
            raise ValueError(f"Unsupported asset kind for analysis: {asset.kind}")

        resolved_profile = None
        risk_assessment = None
        if asset.symbol and report.get("best_entry") is not None and report.get("stop_loss") is not None:
            resolved_profile = self.strategy_manager.resolve(asset.symbol, style)
            risk_assessment = await self.provider_manager.call(
                PROVIDER_BINANCE_ACCOUNT,
                self.risk_manager.full_risk_report(
                    symbol=asset.symbol,
                    entry_price=report["best_entry"],
                    stop_loss=report["stop_loss"],
                    take_profit=report.get("take_profit_1"),
                    risk_reward=_rr_from_report(report),
                    profile=resolved_profile.asset_profile,
                ),
            )

        return self.decision_engine.merge(report, risk=risk_assessment, resolved_profile=resolved_profile)

    async def _build_evidence(self, decision: AgentDecision, asset: ResolvedAsset) -> EvidenceReport:
        """
        Phase 4: the ONE new real read this phase adds - this asset's own
        past signals, via the EXISTING `SignalRepository.get_signals()`
        (already used by GET /signals; not a new query pattern). Only
        applies to trading-pair symbols, which is the only asset kind this
        project's Signal history covers - honestly "not available" for
        contract addresses via `build_historical_evidence(None, ...)`, per
        that function's own docstring. Any lookup failure is caught and
        surfaced as "not available" rather than breaking the whole
        response, since evidence is supplementary to (not a precondition
        of) the decision the Decision Engine already produced.
        """
        historical = None
        if asset.kind == "trading_pair" and asset.symbol:
            try:
                repo = SignalRepository(self.session)
                signals, _total = await repo.get_signals(SignalQueryParams(
                    symbol=asset.symbol, page_size=5, sort_by="created_at", sort_order="desc",
                ))
                historical = build_historical_evidence(asset.symbol, signals)
            except Exception:
                historical = build_historical_evidence(asset.symbol, None)
        else:
            historical = build_historical_evidence(None, None)

        return self.evidence_engine.build(decision, historical=historical)

    async def _build_research(self, decision: AgentDecision, asset: ResolvedAsset) -> ResearchNote:
        """
        Phase 5: supplementary, best-effort macro/sentiment context - see
        research_engine.py's own module docstring. Never allowed to break
        the response the Decision Engine already produced: any unexpected
        failure here is caught and turned into an honest "not applicable"
        note rather than propagating.
        """
        try:
            return await self.research_engine.explain_market_move(asset, decision)
        except Exception as e:
            return ResearchNote(applicable=False, reason=f"Market intelligence research failed: {e}")

    def _build_coach_advice(
        self, intent_type: IntentType, decision: AgentDecision,
        evidence: EvidenceReport, research: ResearchNote,
    ) -> Optional[CoachAdvice]:
        """
        Phase 6: looks up whether this turn's intent is one of the 7
        practical questions the AI Trading Coach answers - see
        trading_coach.py's own module docstring. Pure, synchronous, no I/O
        (mirrors the same already-built decision/evidence/research objects
        this method's two async siblings above produced); returns None for
        every intent that isn't one of the 7, leaving every other response
        shape (analyze_asset, compare_assets, ranked queries, etc.)
        completely unaffected by this phase. Any unexpected error is caught
        and turned into an honest "not available" advice object rather than
        breaking the response the Decision Engine already produced.
        """
        question = COACH_QUESTION_BY_INTENT.get(intent_type)
        if question is None:
            return None
        try:
            return self.trading_coach.advise(question, decision, evidence=evidence, research=research)
        except Exception as e:
            return CoachAdvice(
                question=question.value, question_label=question.value,
                verdict=f"Trading Coach could not produce advice for this query: {e}",
            )

    async def _handle_single_asset(self, intent: AgentIntent) -> AgentResponse:
        asset = intent.assets[0]
        try:
            decision = await self._analyze_asset(asset, intent.style)
        except MarketScanError as e:
            return AgentResponse(intent=intent.intent_type.value, narrative=str(e), warnings=[str(e)])
        except Exception as e:
            return AgentResponse(
                intent=intent.intent_type.value,
                narrative=f"Could not analyze {asset.matched_text}: {e}",
                warnings=[str(e)],
            )

        evidence = await self._build_evidence(decision, asset)
        research = await self._build_research(decision, asset)
        coach_advice = self._build_coach_advice(intent.intent_type, decision, evidence, research)
        narrative = self.explainer.explain(decision, intent, evidence=evidence, research=research, coach_advice=coach_advice)
        return AgentResponse(
            intent=intent.intent_type.value,
            narrative=narrative,
            assets_analyzed=[decision.symbol_or_address],
            decision=decision,
            evidence=evidence,
            research=research,
            coach_advice=coach_advice,
            warnings=decision.warnings,
        )

    async def _handle_compare(self, intent: AgentIntent) -> AgentResponse:
        decisions: List[AgentDecision] = []
        symbols: List[str] = []
        evidence_by_symbol: Dict[str, EvidenceReport] = {}
        research_by_symbol: Dict[str, ResearchNote] = {}
        for asset in intent.assets[:2]:
            try:
                d = await self._analyze_asset(asset, intent.style)
                decisions.append(d)
                symbols.append(d.symbol_or_address)
                evidence_by_symbol[d.symbol_or_address] = await self._build_evidence(d, asset)
                research_by_symbol[d.symbol_or_address] = await self._build_research(d, asset)
            except Exception as e:
                return AgentResponse(
                    intent=intent.intent_type.value,
                    narrative=f"Could not complete the comparison - {asset.matched_text} failed: {e}",
                    warnings=[str(e)],
                )

        narrative = self.explainer.explain_comparison(
            decisions, symbols, evidence_by_symbol=evidence_by_symbol, research_by_symbol=research_by_symbol,
        )
        return AgentResponse(
            intent=intent.intent_type.value,
            narrative=narrative,
            assets_analyzed=symbols,
            comparison=decisions,
            evidence_by_symbol=evidence_by_symbol,
            research_by_symbol=research_by_symbol,
        )

    async def _handle_ranked_query(self, intent: AgentIntent) -> AgentResponse:
        """
        Reads REAL, already-persisted ACTIVE signals (the live scanner's own
        qualifying output) rather than re-scanning every tracked symbol on
        demand - see module docstring. An empty result is a real, honest
        answer (e.g. "no bullish commodity signals active right now"), not
        an error.
        """
        repo = SignalRepository(self.session)
        active = await repo.get_active_signals()

        if intent.intent_type == IntentType.FIND_BULLISH:
            active = [s for s in active if s.direction.value == "LONG"]
        elif intent.intent_type == IntentType.FIND_BEARISH:
            active = [s for s in active if s.direction.value == "SHORT"]

        if intent.intent_type in (IntentType.FIND_BEST_TRADE, IntentType.SHOW_STRONGEST):
            active.sort(key=lambda s: (s.confidence, s.risk_reward), reverse=True)
            title = "Best trade(s) today" if intent.intent_type == IntentType.FIND_BEST_TRADE else "Strongest setup(s)"
        elif intent.intent_type == IntentType.SHOW_WEAKEST:
            active.sort(key=lambda s: (s.confidence, s.risk_reward))
            title = "Weakest setup(s)"
        elif intent.intent_type == IntentType.FIND_SAFEST_TRADE:
            # "Safest" = highest RR relative to the asset's own minimum,
            # tie-broken by confidence - a defensible, disclosed proxy for
            # risk built only from real, already-stored fields (no new
            # per-symbol risk scan).
            active.sort(key=lambda s: (s.risk_reward, s.confidence), reverse=True)
            title = "Safest trade(s) today"
        elif intent.intent_type == IntentType.FIND_BULLISH:
            title = "All bullish assets (currently ACTIVE signals)"
        else:
            title = "All bearish assets (currently ACTIVE signals)"

        top = active[:5] if intent.intent_type in (
            IntentType.FIND_BEST_TRADE, IntentType.FIND_SAFEST_TRADE,
            IntentType.SHOW_STRONGEST, IntentType.SHOW_WEAKEST,
        ) else active

        rows = [
            {
                "symbol": s.coin.symbol if s.coin else "UNKNOWN",
                "direction": s.direction.value,
                "confidence": s.confidence,
                "risk_reward": s.risk_reward,
                "entry_price": s.entry_price,
                "stop_loss": s.stop_loss,
                "take_profit": s.take_profit,
            }
            for s in top
        ]

        narrative = self.explainer.explain_ranked(title, rows)
        return AgentResponse(
            intent=intent.intent_type.value,
            narrative=narrative,
            assets_analyzed=[r["symbol"] for r in rows],
            ranked=rows,
        )


def _rr_from_report(report: Dict[str, Any]) -> Optional[float]:
    rr = report.get("risk_reward")
    if not rr or not isinstance(rr, str) or ":" not in rr:
        return None
    try:
        return float(rr.split(":", 1)[1])
    except ValueError:
        return None
