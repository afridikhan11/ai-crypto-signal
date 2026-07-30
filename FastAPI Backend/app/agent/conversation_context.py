"""
Conversation & Context Engine (Phase 3, 2026-07-27).

Session-scoped, IN-MEMORY-ONLY conversational state for the Trading Agent.
Requirements 5/6 from the Phase 3 brief ("never store long-term memory" /
"session memory only") are implemented BY CONSTRUCTION, not by a retention
policy layered on top of a persistent store: everything below lives in a
plain Python dict in this process's memory, exactly like the existing
`ProviderManager` singleton (see `app/agent/provider_manager.py` and its
`_provider_manager` module-level instance in
`app/api/v1/endpoints/agent.py`, which this module deliberately mirrors).
There is no database table, no file, no Redis key for any of this - a
process restart clears every session, and idle sessions are swept from
memory automatically (see `_IDLE_TIMEOUT_SECONDS` below) so a long-running
process can't grow this dict without bound either.

Requirement 7 ("reuse the existing Trading Agent Core") is why this module
never itself calls a scanner, service, or the Decision Engine - it only
remembers what `TradingAgentOrchestrator` already resolved/decided on a
PREVIOUS turn, so a LATER turn's already-existing Intent Parser + Asset
Resolver output can be filled in before the Orchestrator's existing
analysis path runs unchanged. See `resolve_follow_up()` below, and its one
call site in `orchestrator.py`.

SINGLE-SESSION CAVEAT (disclosed, not hidden): this project's auth model is
single-admin-user (see `app/core/security.py`'s own docstring) - by default
every request resolves to the same `"demo_user"` identity
(`app/api/dependencies.get_current_user`). `TradingAgentOrchestrator.handle()`
accepts an explicit `session_id` for callers that want distinct concurrent
conversations (e.g. multiple WPF windows), but if none is given, the
endpoint falls back to the authenticated user string - which, in today's
default single-user setup, means one shared session. This matches the
project's existing single-user architecture rather than inventing a new
multi-tenant concept this codebase doesn't otherwise have.
"""
from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Deque, Dict, List, Optional

if TYPE_CHECKING:
    from app.agent.asset_resolver import ResolvedAsset
    from app.agent.decision_engine import AgentDecision
    from app.agent.intent_parser import AgentIntent

_MAX_TURNS_REMEMBERED = 10  # bounded transcript - oldest turns drop off, never persisted anywhere
_IDLE_TIMEOUT_SECONDS = 60 * 60  # 1 hour of inactivity - session is forgotten, not archived

# Bare pronouns/back-references with no explicit asset in the SAME message -
# "what about it", "compare with that", "and this one too". Deliberately
# narrow (same rule-based, disclosed-not-fabricated approach as
# intent_parser.py's own docstring) rather than a general coreference model
# this project has no NLU/LLM dependency to support.
_FOLLOW_UP_PRONOUN_RE = re.compile(
    r"\b(it|this|that|this one|that one|the same( asset| coin| token)?|same one)\b", re.I
)

# Intents that inherently need exactly one asset to answer - if the current
# turn's own text didn't resolve one, the remembered last asset is the only
# reasonable fallback (never fabricated - see resolve_follow_up's docstring).
_SINGLE_ASSET_INTENT_VALUES = {
    "analyze_asset", "why_failed", "why_confidence", "what_blocking",
    "explain_signal", "should_wait", "should_buy", "should_exit",
    # Phase 6 additions - AI Trading Coach's remaining 4 practical
    # questions also inherently need exactly one remembered asset for a
    # bare follow-up like "should I scale in?" to resolve correctly.
    "too_late_to_enter", "move_stop_loss", "take_partial_profit", "scale_in",
}

# Detects a comparison ask by the bare word "compare" alone (not "compare
# ... vs/versus/against/and ..." like intent_parser.py's own COMPARE_ASSETS
# pattern requires). This matters because "compare it with ETH" only names
# ONE asset (ETH) and has no "vs"/"versus"/"against"/"and" - IntentParser
# therefore classifies it as ANALYZE_ASSET, not COMPARE_ASSETS, which is
# exactly the phrasing Requirement 3 ("support comparison without repeating
# the asset name") needs to handle. Rather than touch intent_parser.py's
# already-shipped, already-tested COMPARE_ASSETS pattern (Phase 2, approved,
# out of scope for this phase), this module recognizes the "compare + one
# asset + remembered asset" shape itself and promotes the intent.
_COMPARE_KEYWORD_RE = re.compile(r"\bcompare\b", re.I)


@dataclass
class ConversationTurn:
    """One remembered exchange - compact by design (asset symbols/addresses
    as strings, not full objects) since this is a bounded in-memory log, not
    a persistence layer."""
    raw_text: str
    intent_type: str
    resolved_assets: List[str] = field(default_factory=list)
    resolved_via_context: bool = False
    at: float = field(default_factory=time.time)


@dataclass
class ConversationSession:
    session_id: str
    created_at: float = field(default_factory=time.time)
    last_seen_at: float = field(default_factory=time.time)
    last_asset: Optional["ResolvedAsset"] = None
    last_decision: Optional["AgentDecision"] = None
    last_style: Optional[str] = None
    turns: Deque[ConversationTurn] = field(default_factory=lambda: deque(maxlen=_MAX_TURNS_REMEMBERED))

    def touch(self) -> None:
        self.last_seen_at = time.time()

    def record_turn(self, raw_text: str, intent_type: str, resolved_assets: List[str], resolved_via_context: bool) -> None:
        self.turns.append(ConversationTurn(
            raw_text=raw_text, intent_type=intent_type,
            resolved_assets=resolved_assets, resolved_via_context=resolved_via_context,
        ))
        self.touch()

    def remember_asset(self, asset: "ResolvedAsset", style: Optional[str]) -> None:
        self.last_asset = asset
        if style:
            self.last_style = style

    def remember_decision(self, decision: "AgentDecision") -> None:
        self.last_decision = decision

    def describe_last_asset(self) -> Optional[str]:
        if self.last_asset is None:
            return None
        return self.last_asset.symbol or self.last_asset.address or self.last_asset.matched_text


class ConversationContextManager:
    """
    Process-wide, in-memory registry of `ConversationSession`s. Construct
    once and share across requests (see `_context_manager` singleton in
    `app/api/v1/endpoints/agent.py`) so context actually persists between a
    user's turns within a process lifetime - constructing a fresh one per
    request would defeat the entire point of Phase 3.

    Nothing here is written to disk, a database, or a cache service at any
    point - `clear()` / the idle sweep are the ONLY ways a session's data
    goes away, and both simply drop the in-memory object.
    """

    def __init__(self, idle_timeout_seconds: float = _IDLE_TIMEOUT_SECONDS):
        self._sessions: Dict[str, ConversationSession] = {}
        self._idle_timeout = idle_timeout_seconds

    def _sweep_idle(self) -> None:
        now = time.time()
        expired = [sid for sid, s in self._sessions.items() if now - s.last_seen_at > self._idle_timeout]
        for sid in expired:
            del self._sessions[sid]

    def get_or_create(self, session_id: str) -> ConversationSession:
        self._sweep_idle()
        session = self._sessions.get(session_id)
        if session is None:
            session = ConversationSession(session_id=session_id)
            self._sessions[session_id] = session
        session.touch()
        return session

    def clear(self, session_id: str) -> bool:
        """Returns True if a session actually existed and was dropped."""
        return self._sessions.pop(session_id, None) is not None

    def active_session_count(self) -> int:
        self._sweep_idle()
        return len(self._sessions)


def resolve_follow_up(intent: "AgentIntent", session: ConversationSession) -> bool:
    """
    Mutates `intent` IN PLACE to fill in gaps from remembered session state
    - never overrides an asset the Intent Parser/Asset Resolver already
    found explicitly in THIS turn's own text (Requirement 7: this function
    still calls nothing in the Trading Agent Core itself; it only prepares
    the same `AgentIntent` shape the Orchestrator's existing, unmodified
    analysis path already consumes).

    Returns True if anything was actually filled in from context (so the
    Orchestrator can tell the user "continuing from BTCUSDT" instead of
    silently changing what's being analyzed).

    Three cases, matching the Phase 3 brief (checked in this priority
    order, first match wins):
      1. A comparison naming only ONE asset this turn ("compare it with
         ETH", or a proper "compare BTC and it" that IntentParser already
         tagged COMPARE_ASSETS but which only resolved one real asset) ->
         pair the remembered last asset with the one just found, so the
         user never has to repeat the first asset's name (Requirement 3).
         Checked first because the bare word "compare" can otherwise leave
         `intent_type` as ANALYZE_ASSET (see `_COMPARE_KEYWORD_RE`'s own
         comment) and get caught by case 2 instead, which would silently
         drop the newly-named asset rather than comparing against it.
      2. Any OTHER single-asset intent ("analyze it", "why did it fail", a
         bare "what about it?") with no asset resolved this turn -> use the
         remembered last asset.
      3. Style stickiness: if this turn didn't mention a style but the
         session remembers one from an earlier turn, keep using it.
    """
    resolved_via_context = False

    if (
        _COMPARE_KEYWORD_RE.search(intent.raw_text)
        and len(intent.assets) == 1
        and session.last_asset is not None
    ):
        existing_key = intent.assets[0].symbol or intent.assets[0].address
        last_key = session.last_asset.symbol or session.last_asset.address
        if existing_key != last_key:
            from app.agent.intent_parser import IntentType  # local import - see the matching note on the pronoun branch below
            intent.assets = [session.last_asset, intent.assets[0]]
            intent.intent_type = IntentType.COMPARE_ASSETS
            resolved_via_context = True

    elif (
        intent.intent_type.value in _SINGLE_ASSET_INTENT_VALUES
        and not intent.assets
        and session.last_asset is not None
    ):
        intent.assets = [session.last_asset]
        resolved_via_context = True

    elif (
        intent.intent_type.value == "unknown"
        and not intent.assets
        and session.last_asset is not None
        and _FOLLOW_UP_PRONOUN_RE.search(intent.raw_text)
    ):
        # A bare back-reference with no command verb at all ("what about
        # it?") - treat as "analyze the asset we were just discussing",
        # the same fallback intent_parser.py itself uses for a bare ticker.
        from app.agent.intent_parser import IntentType  # local import - avoids a module-load cycle with intent_parser, which imports asset_resolver/strategy_profile_manager but not this module
        intent.assets = [session.last_asset]
        intent.intent_type = IntentType.ANALYZE_ASSET
        resolved_via_context = True

    if intent.style is None and session.last_style:
        intent.style = session.last_style

    return resolved_via_context
