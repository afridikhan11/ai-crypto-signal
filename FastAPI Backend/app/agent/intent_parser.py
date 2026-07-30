"""
Intent Parser (Phase 2, 2026-07-27).

Deterministic, rule-based natural-language routing tuned to the exact
command set the Trading Agent is specified to support (see the module
docstring's examples). This is NOT a general-purpose language model - it's
ordered keyword/regex pattern matching, same category of technique as
`app.services.market_scorer.detect_input_type`. Documented here rather than
implied otherwise, since this project has no LLM integration configured
(no such package in requirements.txt) to genuinely "understand" arbitrary
phrasing - fabricating that capability would violate the project's own
no-fabrication standard.

The important interface boundary is `AgentIntent` - if a real NLU/LLM layer
is added in a later phase, it only needs to produce the same `AgentIntent`
shape; nothing downstream (orchestrator/decision engine/explainer) needs to
change.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from app.agent.asset_resolver import AssetResolver, ResolvedAsset
from app.agent.strategy_profile_manager import StrategyProfileManager


class IntentType(str, Enum):
    ANALYZE_ASSET = "analyze_asset"
    COMPARE_ASSETS = "compare_assets"
    FIND_BEST_TRADE = "find_best_trade"
    FIND_SAFEST_TRADE = "find_safest_trade"
    FIND_BULLISH = "find_bullish"
    FIND_BEARISH = "find_bearish"
    EXPLAIN_SIGNAL = "explain_signal"
    WHY_FAILED = "why_failed"
    WHY_CONFIDENCE = "why_confidence"
    SHOW_STRONGEST = "show_strongest"
    SHOW_WEAKEST = "show_weakest"
    WHAT_BLOCKING = "what_blocking"
    SHOULD_WAIT = "should_wait"
    SHOULD_BUY = "should_buy"
    SHOULD_EXIT = "should_exit"
    # Phase 6 additions (2026-07-27) - AI Trading Coach's remaining 4
    # practical questions (SHOULD_WAIT/SHOULD_BUY/SHOULD_EXIT above already
    # covered 3 of the 7 since Phase 2). Purely additive new members -
    # nothing above this line was touched, and no existing pattern below
    # was reordered or modified.
    TOO_LATE_TO_ENTER = "too_late_to_enter"
    MOVE_STOP_LOSS = "move_stop_loss"
    TAKE_PARTIAL_PROFIT = "take_partial_profit"
    SCALE_IN = "scale_in"
    UNKNOWN = "unknown"


@dataclass
class AgentIntent:
    intent_type: IntentType
    raw_text: str
    assets: List[ResolvedAsset] = field(default_factory=list)
    style: Optional[str] = None
    confidence_threshold_mentioned: Optional[int] = None


# Ordered (first match wins) - more specific patterns before general ones,
# e.g. "compare" before a bare "analyze" fallback, "why...confidence"
# before generic "why...fail".
_PATTERNS: List[tuple[re.Pattern, IntentType]] = [
    (re.compile(r"\bcompare\b.*\bvs\b|\bcompare\b.*\bversus\b|\bcompare\b.*\bagainst\b|\bcompare\b.*\band\b", re.I), IntentType.COMPARE_ASSETS),
    (re.compile(r"\bbest\s+(trade|setup|signal)\b", re.I), IntentType.FIND_BEST_TRADE),
    (re.compile(r"\bsafest\s+(trade|setup|signal)\b", re.I), IntentType.FIND_SAFEST_TRADE),
    (re.compile(r"\bstrongest\s+setup\b", re.I), IntentType.SHOW_STRONGEST),
    (re.compile(r"\bweakest\s+setup\b", re.I), IntentType.SHOW_WEAKEST),
    (re.compile(r"\ball\s+bullish\b|\bbullish\s+assets\b", re.I), IntentType.FIND_BULLISH),
    (re.compile(r"\ball\s+bearish\b|\bbearish\s+assets\b", re.I), IntentType.FIND_BEARISH),
    (re.compile(r"\bwhy\b.*\bconfidence\b|\bconfidence\b.*\bonly\b", re.I), IntentType.WHY_CONFIDENCE),
    (re.compile(r"\bwhy\b.*\b(fail|failed|rejected|no signal|didn'?t (trigger|fire))\b", re.I), IntentType.WHY_FAILED),
    (re.compile(r"\bwhat\s+is\s+blocking\b|\bwhat'?s\s+blocking\b|\bblocking\s+this\s+trade\b", re.I), IntentType.WHAT_BLOCKING),
    (re.compile(r"\bexplain\b.*\bsignal\b|\bexplain\s+this\b|\bshow\s+evidence\b", re.I), IntentType.EXPLAIN_SIGNAL),
    # Phase 6 additions (AI Trading Coach) - checked before the generic
    # SHOULD_WAIT/SHOULD_BUY/SHOULD_EXIT patterns below since their own
    # keywords ("stop loss", "partial profit", "too late", "scale in")
    # don't overlap with those three at all, but placing the more specific
    # multi-word patterns first matches this file's existing convention
    # (see the module docstring comment above _PATTERNS).
    (re.compile(r"\btoo\s+late\s+to\s+enter\b|\bmissed\s+(the\s+)?entry\b", re.I), IntentType.TOO_LATE_TO_ENTER),
    (re.compile(r"\bmove\s+(my\s+)?stop([\s-]?loss)?\b|\btrail(ing)?\s+(my\s+)?stop\b|\badjust\s+(my\s+)?stop([\s-]?loss)?\b", re.I), IntentType.MOVE_STOP_LOSS),
    (re.compile(r"\b(take\s+)?partial\s+profit(s)?\b|\bscale\s+out\b|\btake\s+(some\s+)?profit(s)?\s+(now|here)\b", re.I), IntentType.TAKE_PARTIAL_PROFIT),
    (re.compile(r"\bscale\s+in\b|\badd\s+to\s+(my\s+)?position\b|\bshould\s+i\s+add\s+more\b", re.I), IntentType.SCALE_IN),
    (re.compile(r"\bshould\s+i\s+wait\b", re.I), IntentType.SHOULD_WAIT),
    (re.compile(r"\bshould\s+i\s+(buy|enter|go\s+long|go\s+short)\b", re.I), IntentType.SHOULD_BUY),
    (re.compile(r"\bshould\s+i\s+(exit|sell|close)\b", re.I), IntentType.SHOULD_EXIT),
    (re.compile(r"\banalyz(e|ing)\b|\bscan\b|\blook\s+at\b|\bcheck\b", re.I), IntentType.ANALYZE_ASSET),
]

_NUMBER_NEAR_CONFIDENCE_RE = re.compile(r"confidence[^\d]{0,15}(\d{1,3})", re.I)


class IntentParser:
    def __init__(self, resolver: Optional[AssetResolver] = None):
        self._resolver = resolver or AssetResolver()

    def parse(self, raw_text: str) -> AgentIntent:
        text = raw_text.strip()
        assets = self._resolver.resolve_all(text)
        style = StrategyProfileManager.detect_style_in_text(text)

        confidence_match = _NUMBER_NEAR_CONFIDENCE_RE.search(text)
        confidence_mentioned = int(confidence_match.group(1)) if confidence_match else None

        intent_type = IntentType.UNKNOWN
        for pattern, candidate in _PATTERNS:
            if pattern.search(text):
                intent_type = candidate
                break

        # A bare "BTCUSDT" or "gold" with no verb at all still means
        # "analyze this" - don't fall through to UNKNOWN just because no
        # command verb matched, as long as an asset was actually resolved.
        if intent_type == IntentType.UNKNOWN and assets:
            intent_type = IntentType.ANALYZE_ASSET

        # COMPARE_ASSETS needs at least 2 resolved assets to make sense -
        # if only one was found, this was probably an ANALYZE_ASSET whose
        # phrasing happened to contain "and"/"vs" for another reason.
        if intent_type == IntentType.COMPARE_ASSETS and len(assets) < 2:
            intent_type = IntentType.ANALYZE_ASSET if assets else IntentType.UNKNOWN

        return AgentIntent(
            intent_type=intent_type,
            raw_text=raw_text,
            assets=assets,
            style=style,
            confidence_threshold_mentioned=confidence_mentioned,
        )
