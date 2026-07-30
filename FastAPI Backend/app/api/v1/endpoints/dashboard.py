from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.ai.calibration import DEFAULT_WEIGHTS, get_calibration_status, load_calibrated_weights
from app.api.dependencies import get_current_user, get_signal_service
from app.core.database import AsyncSessionLocal
from app.models.signal import Signal
from app.schemas.dashboard import AccountPanel, AiPanel, DashboardResponse, MarketPanel
from app.services import binance_credentials
from app.services.signal_service import SignalService
from app.smc.htf_structure_engine import HTFStructureEngine
from app.smc.liquidity_engine import LiquidityEngine
from app.smc.market_structure_engine import MarketStructureEngine

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

BTC_SYMBOL = "BTCUSDT"

# Stateless, same engine + pivot windows the live scanner's own HTF read
# uses (see UniversalScanner._load_htf_dataframes/HTF_TIMEFRAMES) - reused
# here rather than duplicated so this label can never disagree with what
# the production pipeline itself considers HTF structure.
_HTF_STRUCTURE_ENGINE = HTFStructureEngine()

# snapshot.structure_alignment -> a short, plain label for the Dashboard's
# Market panel. "conflicted"/"insufficient_data" both honestly render as
# "neutral", the same fallback value _build_market_panel already uses when
# there's no scanner at all - never a guess at a direction.
_ALIGNMENT_TO_LABEL = {
    "aligned_bullish": "bullish",
    "aligned_bearish": "bearish",
}


def _htf_trend_label(htf_snapshot, timeframe: str) -> str:
    structure = htf_snapshot.get(timeframe)
    if structure is None:
        return "neutral"
    return _ALIGNMENT_TO_LABEL.get(structure.structure_alignment, "neutral")


async def _build_account_panel() -> AccountPanel:
    if not binance_credentials.has_saved_credentials():
        return AccountPanel(has_credentials=False)

    try:
        service = binance_credentials.build_service_from_saved()
    except FileNotFoundError:
        return AccountPanel(has_credentials=False)

    try:
        snapshot = await service.get_full_snapshot()
    finally:
        await service.close()

    futures = snapshot.futures
    margin_ratio_pct = None
    if futures and futures.margin_balance:
        maint = futures.total_maintenance_margin or 0
        margin_ratio_pct = round((maint / futures.margin_balance) * 100, 2)

    realized_pnl = None
    if snapshot.trade_history:
        realized = [t.realized_pnl for t in snapshot.trade_history if t.realized_pnl is not None]
        if realized:
            realized_pnl = round(sum(realized), 2)

    return AccountPanel(
        has_credentials=True,
        environment=snapshot.environment,
        wallet_balance=futures.wallet_balance if futures else None,
        available_balance=futures.available_balance if futures else None,
        margin_ratio_pct=margin_ratio_pct,
        unrealized_pnl=futures.unrealized_pnl if futures else None,
        realized_pnl=realized_pnl,
        open_positions=len(futures.open_positions) if futures else 0,
        open_orders=len(snapshot.open_orders),
        warnings=snapshot.warnings,
    )


async def _build_ai_panel() -> AiPanel:
    weights = load_calibrated_weights() or dict(DEFAULT_WEIGHTS)
    status = await get_calibration_status()

    async with AsyncSessionLocal() as session:
        stmt = (
            select(Signal)
            .options(selectinload(Signal.coin))
            .order_by(Signal.created_at.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        latest = result.scalars().first()

    return AiPanel(
        weights=weights,
        is_calibrated=bool(status["is_calibrated"]),
        wins_collected=status["wins_collected"],
        losses_collected=status["losses_collected"],
        min_required_per_group=status["min_required_per_group"],
        last_signal_symbol=latest.coin.symbol if latest and latest.coin else None,
        last_signal_direction=latest.direction.value if latest else None,
        last_signal_confidence=latest.confidence if latest else None,
        last_signal_score_breakdown=latest.score_breakdown if latest else None,
        last_signal_reason=latest.reason if latest else None,
    )


async def _build_market_panel(request: Request) -> MarketPanel:
    scanner = getattr(request.app.state, "scanner", None)

    if scanner is None:
        return MarketPanel(
            btc_trend_1h="neutral",
            btc_trend_4h="neutral",
            market_structure="Unknown",
            liquidity_sweep="Unknown",
            open_interest_trend="flat",
            fear_greed_value=50,
            fear_greed_classification="Neutral",
            binance_connected=False,
        )

    df = scanner.data_manager.get_dataframe(BTC_SYMBOL, "15m", limit=500)
    market_structure = "No clear structure"
    liquidity_sweep = "None swept"
    if len(df) >= 50:
        # ICT Market Structure Engine (2026-07-28 cutover - see
        # MARKET_STRUCTURE_CUTOVER_REPORT.md). external_pivot_window=5
        # preserves the old MarketStructure default's swing granularity.
        ms = MarketStructureEngine(df, external_pivot_window=5)
        snapshot = ms.analyze()
        breaks = snapshot.external_breaks
        if breaks:
            latest_break = breaks[-1]
            market_structure = f"{latest_break.type} ({latest_break.direction})"

        # ICT Liquidity Engine (2026-07-28 cutover - see
        # LIQUIDITY_ENGINE_IMPLEMENTATION_REPORT.md). This panel has no
        # structure_breaks/order_blocks/fvgs already computed at this call
        # site (it only needs a swept-count, not sweep relationship
        # tagging), so none are threaded through here - same "no
        # confluence data available, don't guess it" pattern used
        # elsewhere in this migration.
        liq_engine = LiquidityEngine(df, snapshot.swing_highs, snapshot.swing_lows)
        levels = liq_engine.detect_buyside_liquidity() + liq_engine.detect_sellside_liquidity()
        swept = liq_engine.detect_liquidity_sweeps(levels)
        swept_count = sum(1 for level in swept if level.swept)
        if swept_count:
            liquidity_sweep = f"{swept_count} level(s) swept"

    funding_rate = await scanner.data_manager.get_funding_rate(BTC_SYMBOL)
    oi_data = await scanner.fundamentals.get_open_interest_trend(BTC_SYMBOL)
    fear_greed = await scanner.fundamentals.get_fear_greed_index()
    btc_dominance = await scanner.fundamentals.get_btc_dominance()

    # BTC 1h/4h trend labels - real ICT Market Structure, read via the
    # SAME HTF dataframe loader and HTFStructureEngine the live scanner's
    # own pipeline uses for every symbol (UniversalScanner._load_htf_dataframes
    # + HTF_TIMEFRAMES), not a separate/duplicated computation. Replaces a
    # removed retail EMA20/50 helper (`get_symbol_htf_trend`, quarantined to
    # app/legacy/trend.py during the ICT migration) that this panel was
    # still calling - see PRODUCTION_VALIDATION... Phase 1 stability report.
    htf_dataframes = await scanner._load_htf_dataframes(BTC_SYMBOL)
    htf_snapshot = _HTF_STRUCTURE_ENGINE.build_snapshot(htf_dataframes)

    return MarketPanel(
        btc_trend_1h=_htf_trend_label(htf_snapshot, "1h"),
        btc_trend_4h=_htf_trend_label(htf_snapshot, "4h"),
        market_structure=market_structure,
        liquidity_sweep=liquidity_sweep,
        funding_rate=funding_rate,
        open_interest_trend=oi_data.get("oi_trend", "flat"),
        fear_greed_value=fear_greed.get("value", 50),
        fear_greed_classification=fear_greed.get("classification", "Neutral"),
        btc_dominance=btc_dominance,
        binance_connected=scanner.data_manager.is_connected,
    )


@router.get(
    "",
    response_model=DashboardResponse,
    summary="Combined dashboard data: account, AI scorer, and market context panels",
)
async def get_dashboard(
    request: Request,
    _user: str = Depends(get_current_user),
    signal_service: SignalService = Depends(get_signal_service),
):
    stats = await signal_service.get_stats()

    # This panel composes calls to Binance's account API and several
    # fundamentals endpoints (fear/greed, funding rate, OI, BTC
    # dominance) - the same "external service call" shape as
    # backtest.py/token_scan.py/agent.py, which all translate failures
    # to a clean 502 instead of letting them fall through to the
    # generic 500 handler. Missing credentials / no scanner are NOT
    # errors and are already handled above with explicit fallback
    # panels - this only catches genuine transient failures (timeouts,
    # rate limits, etc.) from a live external call.
    try:
        account_panel = await _build_account_panel()
        account_panel.win_rate = stats.win_rate

        ai_panel = await _build_ai_panel()
        market_panel = await _build_market_panel(request)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Dashboard data fetch failed: {e}")

    return DashboardResponse(account=account_panel, ai=ai_panel, market=market_panel)
