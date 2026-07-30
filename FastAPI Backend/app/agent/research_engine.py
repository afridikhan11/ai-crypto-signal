"""
Research & Market Intelligence Engine (Phase 5, 2026-07-27).

Adds explanatory macro/sentiment CONTEXT for a Binance trading-pair
symbol's price action - "what's happening in the wider market that could
help explain this move" - strictly as SUPPORTING evidence alongside the
Decision Engine's output, never a replacement for it:

  - This module produces NO score, confidence, entry/SL/TP, or final
    decision of its own. `ResearchNote` has no such fields, on purpose -
    there is nothing here for a caller to mistake for the Decision
    Engine's output (Requirement: never replace or override the Decision
    Engine).
  - It NEVER touches `app.ai.scorer.AIScorer`, calibration, or any other
    part of the existing scoring system - not imported, not called, not
    read. "Do not modify the existing scoring system" is honored by never
    going near it in the first place.
  - It reuses ONLY already-shipped, already-free data sources this project
    has had since before this phase:
      * `app.services.fundamentals_service.FundamentalsService` - Open
        Interest trend, long/short account ratio, Fear & Greed Index, BTC
        Dominance (crypto).
      * `app.services.macro_fundamentals_service.MacroFundamentalsService`
        - DXY trend, US 10Y real yield trend, high-impact USD event risk
        (Gold/Silver/Oil).
    Both are the EXACT SAME services `app/services/market_scorer.py` and
    `AIScorer.fundamental_context` already call internally for scoring -
    this phase does not add a single new provider integration. It calls
    them a SECOND time (a fresh, independent read) rather than modifying
    `market_scorer.py` to expose its already-fetched values, specifically
    because this phase's instructions were "do not redesign any existing
    modules" - reusing the public service classes directly, unmodified,
    was the way to add this without touching a single existing file.
  - Every external call is routed through the EXISTING `ProviderManager`
    (`provider_manager.call(...)`) - Requirement 5 ("reuse Provider
    Manager for all external data access"). Its calls show up in the same
    `GET /agent/providers/health` telemetry as every other agent call,
    under `PROVIDER_FUNDAMENTALS`/`PROVIDER_MACRO_FUNDAMENTALS` (both
    constants already existed in `provider_manager.py` since Phase 2,
    unused until now).
  - Honest about unavailability (Requirement 6), with one disclosed
    boundary: `FundamentalsService`/`MacroFundamentalsService` already
    catch their own fetch errors internally and fall back to a
    neutral/cached default (see those files' own docstrings) rather than
    raising. This module cannot distinguish "the market is genuinely
    neutral" from "the underlying free API failed and defaulted to
    neutral" from the outside - and says so in `ResearchNote.disclaimer`
    rather than pretending to a certainty neither this module nor the
    services it calls actually has.

Only applies to Binance trading-pair symbols (crypto AND commodities) -
this project has no equivalent macro/sentiment data source for on-chain
tokens (only DexScreener price/volume plus the Security/Smart Money
dashboards exist there, already surfaced by `evidence_engine.py`).
Contract addresses get an honest "not implemented" `ResearchNote`, never a
guess.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from app.agent.asset_resolver import AssetResolver, ResolvedAsset
from app.agent.decision_engine import AgentDecision
from app.agent.provider_manager import PROVIDER_FUNDAMENTALS, PROVIDER_MACRO_FUNDAMENTALS, ProviderManager
from app.services.fundamentals_service import FundamentalsService
from app.services.macro_fundamentals_service import MacroFundamentalsService

_RESEARCH_DISCLAIMER = (
    "This is correlative market context, not a causal explanation and not a trading signal by "
    "itself - it never overrides the Decision Engine's output above. FundamentalsService/"
    "MacroFundamentalsService fall back to a neutral/cached default internally on a failed fetch "
    "(see their own docstrings), so a 'neutral' reading below may mean a genuinely quiet market OR "
    "an unavailable data source - this module cannot tell the two apart from the outside."
)


@dataclass
class ResearchSignal:
    label: str
    value: str            # human-readable real value, e.g. "Rising (+3.40% vs ~2h ago)"
    interpretation: str   # plain-language read, mirrors this project's own documented framework
    source: str            # exact existing method this came from


@dataclass
class ResearchNote:
    applicable: bool
    asset_type: Optional[str] = None   # "crypto" | "commodity" | None
    reason: Optional[str] = None       # honest reason when not applicable/unavailable
    summary: str = ""
    signals: List[ResearchSignal] = field(default_factory=list)
    disclaimer: str = ""


class ResearchEngine:
    """Constructs its own `FundamentalsService`/`MacroFundamentalsService`
    instances (both are cheap, cache-first, no-credential classes - same
    pattern `market_scorer.py` itself uses) and routes every call through
    the shared `ProviderManager` passed in by the Orchestrator."""

    def __init__(self, provider_manager: ProviderManager):
        self.provider_manager = provider_manager
        self._fundamentals = FundamentalsService()
        self._macro = MacroFundamentalsService()

    async def close(self) -> None:
        await self._fundamentals.close()
        await self._macro.close()

    async def explain_market_move(self, asset: ResolvedAsset, decision: AgentDecision) -> ResearchNote:
        if asset.kind != "trading_pair" or not asset.symbol:
            return ResearchNote(
                applicable=False,
                reason=(
                    "Market intelligence research only covers Binance trading-pair symbols in this "
                    "phase - there is no equivalent macro/sentiment data source for on-chain tokens "
                    "yet (only DexScreener price/volume and the Security/Smart Money dashboards apply "
                    "there - see the Evidence section for those)."
                ),
            )

        if AssetResolver.is_commodity(asset.symbol):
            return await self._explain_commodity(asset.symbol, decision)
        return await self._explain_crypto(asset.symbol, decision)

    # -- crypto: Open Interest / Long-Short Ratio / Fear & Greed / BTC Dominance --

    async def _explain_crypto(self, symbol: str, decision: AgentDecision) -> ResearchNote:
        try:
            oi = await self.provider_manager.call(PROVIDER_FUNDAMENTALS, self._fundamentals.get_open_interest_trend(symbol))
            ls_ratio = await self.provider_manager.call(PROVIDER_FUNDAMENTALS, self._fundamentals.get_long_short_ratio(symbol))
            fng = await self.provider_manager.call(PROVIDER_FUNDAMENTALS, self._fundamentals.get_fear_greed_index())
        except Exception as e:
            return ResearchNote(
                applicable=False, asset_type="crypto",
                reason=f"Market intelligence fetch failed for {symbol}: {e}",
            )

        signals: List[ResearchSignal] = [
            ResearchSignal(
                label="Open Interest",
                value=f"{oi['oi_trend'].capitalize()} ({oi['oi_change_pct']:+.2f}% vs ~2h ago)",
                interpretation=(
                    "Rising OI alongside this move suggests fresh positioning is fueling it (real "
                    "conviction, not just existing holders trading)."
                    if oi["oi_trend"] == "rising" else
                    "Falling OI suggests existing positions are being unwound rather than new "
                    "conviction building."
                    if oi["oi_trend"] == "falling" else
                    "Open interest is roughly flat - no strong signal either way from positioning."
                ),
                source="FundamentalsService.get_open_interest_trend (Binance public futures data)",
            ),
        ]

        if ls_ratio > 1.15:
            ls_read = f"Long-crowded ({ls_ratio:.2f}x more long accounts than short)"
            ls_interpretation = (
                "A heavily long-skewed crowd is a classic contrarian setup - a squeeze lower (long "
                "liquidations) becomes more likely, regardless of the current trend direction."
            )
        elif ls_ratio < 0.87:
            ls_read = f"Short-crowded ({1 / ls_ratio:.2f}x more short accounts than long)"
            ls_interpretation = (
                "A heavily short-skewed crowd is a classic contrarian setup - a squeeze higher (short "
                "liquidations) becomes more likely, regardless of the current trend direction."
            )
        else:
            ls_read = f"Roughly balanced ({ls_ratio:.2f})"
            ls_interpretation = "No strongly one-sided crowd positioning to flag either way."
        signals.append(ResearchSignal(
            label="Long/Short Account Ratio", value=ls_read, interpretation=ls_interpretation,
            source="FundamentalsService.get_long_short_ratio (Binance public futures data)",
        ))

        fng_value = fng.get("value", 50)
        fng_class = fng.get("classification", "Neutral")
        if fng_value <= 25:
            fng_interpretation = "Extreme Fear readings have historically often marked market bottoms, not confirmations of further downside."
        elif fng_value >= 75:
            fng_interpretation = "Extreme Greed readings have historically often marked market tops, not confirmations of further upside."
        else:
            fng_interpretation = "A middling reading - sentiment isn't at an extreme in either direction right now."
        signals.append(ResearchSignal(
            label="Crypto Fear & Greed Index", value=f"{fng_value}/100 ({fng_class})",
            interpretation=fng_interpretation,
            source="FundamentalsService.get_fear_greed_index (alternative.me, market-wide)",
        ))

        # BTC Dominance is only meaningful context for an ALTCOIN, not for
        # BTC itself - fetched and shown only when relevant, not as noise.
        if symbol.upper() != "BTCUSDT":
            dominance = await self.provider_manager.call(PROVIDER_FUNDAMENTALS, self._fundamentals.get_btc_dominance())
            signals.append(ResearchSignal(
                label="BTC Dominance", value=f"{dominance:.1f}%",
                interpretation=(
                    "Elevated BTC dominance means capital has generally rotated into BTC and out of "
                    "altcoins like this one - a headwind for alt strength independent of this symbol's "
                    "own chart."
                ),
                source="FundamentalsService.get_btc_dominance (CoinGecko global market data)",
            ))

        summary = (
            f"Market intelligence for {symbol} ({decision.trend.lower()} read from the Decision Engine): "
            f"open interest is {oi['oi_trend']}, the long/short crowd is "
            f"{'long-crowded' if ls_ratio > 1.15 else 'short-crowded' if ls_ratio < 0.87 else 'balanced'}, "
            f"and market-wide sentiment reads {fng_class.lower()} ({fng_value}/100)."
        )

        return ResearchNote(
            applicable=True, asset_type="crypto", summary=summary,
            signals=signals, disclaimer=_RESEARCH_DISCLAIMER,
        )

    # -- commodities: DXY / Real Yield / High-Impact Event Risk --

    async def _explain_commodity(self, symbol: str, decision: AgentDecision) -> ResearchNote:
        try:
            dxy = await self.provider_manager.call(PROVIDER_MACRO_FUNDAMENTALS, self._macro.get_dollar_index_trend())
            real_yield = await self.provider_manager.call(PROVIDER_MACRO_FUNDAMENTALS, self._macro.get_real_yield_trend())
            event_risk = await self.provider_manager.call(PROVIDER_MACRO_FUNDAMENTALS, self._macro.get_high_impact_event_risk())
        except Exception as e:
            return ResearchNote(
                applicable=False, asset_type="commodity",
                reason=f"Market intelligence fetch failed for {symbol}: {e}",
            )

        signals: List[ResearchSignal] = [
            ResearchSignal(
                label="US Dollar Index (DXY)",
                value=f"{dxy['dxy_trend'].capitalize()} ({dxy['dxy_change_pct']:+.2f}%)",
                interpretation=(
                    "A strengthening dollar is typically a headwind for USD-priced Gold/Silver/Oil."
                    if dxy["dxy_trend"] == "up" else
                    "A weakening dollar is typically a tailwind for USD-priced Gold/Silver/Oil."
                    if dxy["dxy_trend"] == "down" else
                    "The dollar index is roughly flat - no strong directional pressure from currency."
                ),
                source="MacroFundamentalsService.get_dollar_index_trend (Yahoo Finance DX-Y.NYB)",
            ),
            ResearchSignal(
                label="US 10Y Real Yield",
                value=f"{real_yield['real_yield_trend'].capitalize()} ({real_yield['real_yield_change_bps']:+.1f} bps)",
                interpretation=(
                    "Rising real yields raise the opportunity cost of holding a non-yielding asset "
                    "like gold - typically a headwind."
                    if real_yield["real_yield_trend"] == "up" else
                    "Falling real yields lower the opportunity cost of holding a non-yielding asset "
                    "like gold - typically a tailwind."
                    if real_yield["real_yield_trend"] == "down" else
                    "Real yields are roughly flat - no strong opportunity-cost pressure either way."
                ),
                source="MacroFundamentalsService.get_real_yield_trend (FRED DFII10)",
            ),
        ]

        if event_risk.get("high_impact_event_soon"):
            signals.append(ResearchSignal(
                label="High-Impact USD Event",
                value=f"{event_risk['event_name']} in ~{event_risk['hours_until_event']}h",
                interpretation=(
                    "A high-impact USD release this close can move Gold/Silver/Oil sharply and "
                    "unpredictably in EITHER direction - treat any current signal with extra caution "
                    "until it passes."
                ),
                source="MacroFundamentalsService.get_high_impact_event_risk (ForexFactory weekly calendar)",
            ))

        summary = (
            f"Market intelligence for {symbol} ({decision.trend.lower()} read from the Decision Engine): "
            f"DXY is {dxy['dxy_trend']}, US 10Y real yield is {real_yield['real_yield_trend']}"
            + (f", and a high-impact USD event ({event_risk['event_name']}) is within "
               f"{event_risk['hours_until_event']}h" if event_risk.get("high_impact_event_soon") else "")
            + "."
        )

        return ResearchNote(
            applicable=True, asset_type="commodity", summary=summary,
            signals=signals, disclaimer=_RESEARCH_DISCLAIMER,
        )
