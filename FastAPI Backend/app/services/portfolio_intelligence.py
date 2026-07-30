"""
Portfolio Intelligence (Phase 7, 2026-07-27).

Reporting/aggregation layer over data this project ALREADY collects.
Building on the explicit Phase 7 instructions ("do not create a second
scoring engine", "everything must reuse existing platform data", "never
fabricate statistics"), this module computes NOTHING that qualifies as a
new indicator or a new AI read:

  - Exposure analysis reads REAL live Binance futures positions (via the
    existing, read-only `BinanceAccountService` - same class the Account
    tab already uses) when credentials are linked, or falls back to the
    EXISTING `SignalService.get_portfolio_risk()`'s own active-signal
    suggested-risk figures when they aren't. Either way, every dollar
    figure already existed before this file ran - this module only
    aggregates and labels it.
  - Requirement 1 ("portfolio analysis must reuse the existing Decision
    Engine") is satisfied literally: for each symbol currently held, this
    module calls the SAME `build_market_scan_report()` +
    `DecisionEngine().merge()` pair the Trading Agent's own
    `orchestrator.py._analyze_asset()` calls for a trading-pair symbol -
    not a copy of that logic, the actual same functions - to attach a
    fresh "what does the Decision Engine say about this RIGHT NOW" read
    next to each real exposure line. This is the ONLY network I/O this
    module performs beyond the account snapshot itself, and it is
    deliberately best-effort/optional per symbol: a failure for one symbol
    never breaks the rest of the report (see `_attach_decision_reads`).
  - Correlation analysis reuses the EXACT SAME Pearson-correlation
    computation `app.services.correlation_risk.py` already uses for
    per-signal correlation warnings (`_pct_returns`, `CORRELATION_THRESHOLD`,
    `CORRELATION_LOOKBACK`, `MIN_ALIGNED_SAMPLES` are imported directly from
    that module, not reimplemented) - just applied across the current
    exposure set as a full matrix instead of one flag per signal.
  - Diversification analysis is pure arithmetic (a Herfindahl-Hirschman
    Index, the standard textbook concentration measure: sum of each
    holding's weight-fraction squared, scaled to 0-10000) over the exposure
    weights this module already computed above - no new data source, no
    new market read.

Every "not available" path below is a REAL data gap (no credentials linked,
no live scanner attached, fewer than 2 symbols currently held) stated
honestly, never a fabricated result.
"""
from __future__ import annotations

from datetime import datetime, timezone
from itertools import combinations
from typing import Any, Dict, List, Optional

import pandas as pd
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.decision_engine import DecisionEngine
from app.core.constants import asset_class_for_symbol, asset_type_for_symbol
from app.schemas.portfolio import (
    CorrelationAnalysisResponse,
    CorrelationPair,
    DiversificationResponse,
    ExposureAnalysisResponse,
    ExposureItem,
    PortfolioIntelligenceResponse,
)
from app.services import binance_credentials
from app.services.correlation_risk import (
    CORRELATION_LOOKBACK,
    CORRELATION_THRESHOLD,
    MIN_ALIGNED_SAMPLES,
    _pct_returns,
)
from app.services.market_scorer import MarketScanError, build_market_scan_report
from app.services.signal_service import SignalService

# Decision Engine enrichment is best-effort AND capped - a portfolio with
# many distinct symbols held would otherwise turn one report request into
# dozens of live market scans. This cap is disclosed in `ExposureAnalysisResponse.message`
# whenever it's actually hit, never silently applied.
MAX_DECISION_ENRICHMENT_SYMBOLS = 8


async def _live_exposure_items(session: AsyncSession) -> Optional[List[ExposureItem]]:
    """Real, live Binance futures open positions - None (not an empty list)
    when there's no linked account at all, so the caller can tell "no
    account" apart from "account linked, zero open positions" (an empty
    list is itself a real, honest answer in the second case)."""
    if not binance_credentials.has_saved_credentials():
        return None
    try:
        service = binance_credentials.build_service_from_saved()
    except FileNotFoundError:
        return None

    try:
        snapshot = await service.get_full_snapshot()
    except Exception as e:
        logger.warning(f"Portfolio Intelligence: live account snapshot failed, falling back to active signals: {e}")
        return None
    finally:
        await service.close()

    if snapshot.futures is None:
        return []

    items: List[ExposureItem] = []
    for p in snapshot.futures.open_positions:
        direction = "LONG" if p.position_amt > 0 else "SHORT"
        notional = abs(p.position_amt) * (p.mark_price if p.mark_price is not None else p.entry_price)
        items.append(ExposureItem(
            symbol=p.symbol,
            asset_class=asset_class_for_symbol(p.symbol),
            asset_type=asset_type_for_symbol(p.symbol),
            direction=direction,
            notional_usd=round(notional, 2),
            margin_usd=round(p.margin, 2) if p.margin is not None else None,
            unrealized_pnl_usd=round(p.unrealized_pnl, 2),
            weight_pct=0.0,  # filled in by _finalize_weights once the full set is known
        ))
    return items


async def _active_signal_exposure_items(session: AsyncSession) -> List[ExposureItem]:
    """Fallback source - the EXISTING SignalService.get_portfolio_risk()'s
    own suggested-$-risk figures per ACTIVE signal, unmodified. Used only
    when no live Binance account is linked (or the live fetch failed)."""
    portfolio_risk = await SignalService(session).get_portfolio_risk()
    items: List[ExposureItem] = []
    for row in portfolio_risk.items:
        items.append(ExposureItem(
            symbol=row.coin_symbol,
            asset_class=asset_class_for_symbol(row.coin_symbol),
            asset_type=asset_type_for_symbol(row.coin_symbol),
            direction=row.direction,
            risk_usd=round(row.risk_usd, 2),
            weight_pct=0.0,
        ))
    return items


def _finalize_weights(items: List[ExposureItem]) -> None:
    """Mutates `weight_pct` in place from whichever real figure this
    exposure source actually populated (margin for live positions - the
    capital genuinely committed - else notional, else risk_usd for the
    active-signal fallback). Pure arithmetic over numbers already on the
    items; no new data."""
    def _basis(i: ExposureItem) -> float:
        return abs(i.margin_usd if i.margin_usd is not None else (i.notional_usd if i.notional_usd is not None else (i.risk_usd or 0.0)))

    total = sum(_basis(i) for i in items)
    if total <= 0:
        return
    for i in items:
        i.weight_pct = round(_basis(i) / total * 100, 2)


async def _attach_decision_reads(items: List[ExposureItem]) -> None:
    """
    Requirement 1: reuse the EXISTING Decision Engine for a fresh read on
    each currently-held symbol. Calls the SAME two functions
    orchestrator.py's `_analyze_asset` calls for a trading-pair symbol -
    `build_market_scan_report()` then `DecisionEngine().merge()` - nothing
    reimplemented, nothing new computed. Best-effort per symbol: any
    failure (rate limit, unsupported symbol, transient network issue)
    leaves `current_decision=None` with an honest `decision_note` rather
    than breaking the whole exposure report.
    """
    engine = DecisionEngine()
    unique_symbols = list(dict.fromkeys(i.symbol for i in items))
    enriched = unique_symbols[:MAX_DECISION_ENRICHMENT_SYMBOLS]
    skipped = len(unique_symbols) - len(enriched)

    reads: Dict[str, ExposureItem] = {}
    for symbol in enriched:
        try:
            report = await build_market_scan_report(symbol)
            decision = engine.merge(report, risk=None, resolved_profile=None)
            reads[symbol] = (decision.final_decision, decision.confidence_pct, None)
        except MarketScanError as e:
            reads[symbol] = (None, None, str(e))
        except Exception as e:
            reads[symbol] = (None, None, f"Current AI read unavailable: {e}")

    for i in items:
        if i.symbol in reads:
            final_decision, confidence, note = reads[i.symbol]
            i.current_decision = final_decision
            i.current_confidence = confidence
            i.decision_note = note
        elif i.symbol not in enriched:
            i.decision_note = (
                f"Not fetched - Decision Engine enrichment is capped at "
                f"{MAX_DECISION_ENRICHMENT_SYMBOLS} distinct symbols per report to avoid a large "
                f"fan-out of live market scans ({skipped} additional symbol(s) skipped)."
            )


async def build_exposure_analysis(session: AsyncSession, enrich_with_decisions: bool = True) -> ExposureAnalysisResponse:
    live_items = await _live_exposure_items(session)

    if live_items is not None:
        source = "binance_live"
        items = live_items
        account_balance = None
        try:
            if binance_credentials.has_saved_credentials():
                service = binance_credentials.build_service_from_saved()
                try:
                    snapshot = await service.get_full_snapshot()
                    account_balance = snapshot.futures.wallet_balance if snapshot.futures else None
                finally:
                    await service.close()
        except Exception:
            account_balance = None
        message = None if items else "Binance account linked - no open futures positions right now."
    else:
        source = "active_signals"
        items = await _active_signal_exposure_items(session)
        account_balance = None
        message = (
            "No Binance account linked (or live fetch failed) - showing suggested exposure from "
            "currently ACTIVE signals instead (same figures as the existing Portfolio Risk summary)."
            if items else
            "No Binance account linked and no currently ACTIVE signals - nothing to show exposure for."
        )

    _finalize_weights(items)

    if enrich_with_decisions and items:
        await _attach_decision_reads(items)

    total_exposure = round(sum(
        abs(i.margin_usd if i.margin_usd is not None else (i.notional_usd if i.notional_usd is not None else (i.risk_usd or 0.0)))
        for i in items
    ), 2) if items else None

    by_asset_class: Dict[str, float] = {}
    by_direction: Dict[str, float] = {}
    for i in items:
        by_asset_class[i.asset_class] = round(by_asset_class.get(i.asset_class, 0.0) + i.weight_pct, 2)
        by_direction[i.direction] = round(by_direction.get(i.direction, 0.0) + i.weight_pct, 2)

    return ExposureAnalysisResponse(
        source=source if items or live_items is not None else "none",
        account_balance=account_balance,
        total_exposure_usd=total_exposure,
        items=items,
        by_asset_class_pct=by_asset_class,
        by_direction_pct=by_direction,
        message=message,
    )


async def build_correlation_analysis(items: List[ExposureItem], scanner: Any) -> CorrelationAnalysisResponse:
    data_manager = getattr(scanner, "data_manager", None)
    symbols = list(dict.fromkeys(i.symbol for i in items))

    if data_manager is None:
        return CorrelationAnalysisResponse(
            available=False,
            reason=(
                "No live scanner/data manager attached to this request - correlation analysis needs "
                "real cached 15m candles, the same requirement app.services.correlation_risk.py's "
                "existing per-signal correlation warnings already have."
            ),
            symbols_checked=symbols,
        )
    if len(symbols) < 2:
        return CorrelationAnalysisResponse(
            available=False,
            reason="Fewer than 2 distinct symbols currently held - nothing to correlate.",
            symbols_checked=symbols,
        )

    returns_cache: Dict[str, Optional[pd.Series]] = {}
    for sym in symbols:
        returns_cache[sym] = _pct_returns(data_manager, sym)

    direction_by_symbol = {i.symbol: i.direction for i in items}
    pairs: List[CorrelationPair] = []
    for a, b in combinations(symbols, 2):
        ra, rb = returns_cache.get(a), returns_cache.get(b)
        if ra is None or rb is None:
            continue
        aligned = pd.concat([ra, rb], axis=1, join="inner")
        if len(aligned) < MIN_ALIGNED_SAMPLES:
            continue
        corr = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
        if corr is None or pd.isna(corr):
            continue
        same_direction = direction_by_symbol.get(a) == direction_by_symbol.get(b)
        # bool(...) - pandas' .corr() returns a numpy.float64, so the
        # comparison below is a numpy.bool_, not a plain Python bool. Cast
        # explicitly so the pydantic CorrelationPair.stacked_risk field
        # always holds a real bool (numpy.bool_ compares equal to True/False
        # but is a different object, which broke an `is True` check in
        # testing - never surfaced by pydantic validation alone since
        # numpy.bool_ passes an `isinstance(x, bool)`-style check, so this
        # was a real latent type inconsistency, not just a test artifact).
        stacked = bool(same_direction and corr >= CORRELATION_THRESHOLD)
        pairs.append(CorrelationPair(
            symbol_a=a, symbol_b=b, correlation=round(float(corr), 3),
            same_direction=same_direction, stacked_risk=stacked,
        ))

    stacked_pairs = [p for p in pairs if p.stacked_risk]
    return CorrelationAnalysisResponse(
        available=True,
        threshold=CORRELATION_THRESHOLD,
        symbols_checked=symbols,
        pairs=pairs,
        stacked_risk_pairs=stacked_pairs,
        message=(
            f"{len(stacked_pairs)} same-direction pair(s) at or above the {CORRELATION_THRESHOLD} "
            f"correlation threshold (last {CORRELATION_LOOKBACK} 15m candles)."
            if pairs else
            "No symbol pair had enough aligned recent candle history to compute a correlation yet."
        ),
    )


def _hhi(weights_pct: List[float]) -> float:
    """Herfindahl-Hirschman Index: sum of each share's percentage squared.
    Standard 0-10000 scale (a single 100%-weight holding scores the
    maximum 10000; N equally-weighted holdings score 10000/N.) Pure
    arithmetic over the weight_pct values already computed by
    build_exposure_analysis - no new data."""
    return round(sum(w * w for w in weights_pct), 2)


def _concentration_label(hhi: float) -> str:
    # Fixed, disclosed bands used by this report only (see DiversificationResponse
    # field docstring) - not a market-standard authority claim, just this
    # report's own consistent bucketing of the same HHI number above.
    if hhi >= 2500:
        return "Highly concentrated"
    if hhi >= 1500:
        return "Moderately concentrated"
    return "Well diversified"


def build_diversification_analysis(exposure: ExposureAnalysisResponse) -> DiversificationResponse:
    if not exposure.items:
        return DiversificationResponse(
            available=False,
            reason="No open exposure to analyze (see the Exposure Analysis section for why).",
        )

    by_symbol_weights = [i.weight_pct for i in exposure.items]
    hhi_symbol = _hhi(by_symbol_weights)

    by_class: Dict[str, float] = exposure.by_asset_class_pct
    hhi_class = _hhi(list(by_class.values()))

    largest_item = max(exposure.items, key=lambda i: i.weight_pct)
    largest_class = max(by_class.items(), key=lambda kv: kv[1]) if by_class else (None, None)

    return DiversificationResponse(
        available=True,
        hhi_by_symbol=hhi_symbol,
        hhi_by_asset_class=hhi_class,
        concentration_label_by_symbol=_concentration_label(hhi_symbol),
        concentration_label_by_asset_class=_concentration_label(hhi_class),
        largest_symbol=largest_item.symbol,
        largest_symbol_weight_pct=largest_item.weight_pct,
        largest_asset_class=largest_class[0],
        largest_asset_class_weight_pct=largest_class[1],
        message=(
            f"Largest single exposure: {largest_item.symbol} at {largest_item.weight_pct}% of "
            f"tracked exposure."
        ),
    )


async def build_portfolio_intelligence(session: AsyncSession, scanner: Any = None) -> PortfolioIntelligenceResponse:
    """Composes the EXISTING PortfolioRiskResponse/DailyLossResponse (Phase 1,
    unmodified) with the new exposure/correlation/diversification analyses
    above into one report."""
    signal_service = SignalService(session)
    risk = await signal_service.get_portfolio_risk()
    daily_loss = await signal_service.get_daily_loss()

    exposure = await build_exposure_analysis(session)
    correlation = await build_correlation_analysis(exposure.items, scanner)
    diversification = build_diversification_analysis(exposure)

    return PortfolioIntelligenceResponse(
        generated_at=datetime.now(timezone.utc),
        risk=risk,
        daily_loss=daily_loss,
        exposure=exposure,
        correlation=correlation,
        diversification=diversification,
    )
