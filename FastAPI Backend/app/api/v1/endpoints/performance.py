"""
AI Performance Monitoring endpoints (Phase 7, 2026-07-27).

Read-only. Every response is built by app/services/ai_performance_service.py
from real, already-stored Signal data and the already-shipped calibration
status machinery - see that module's own docstring for exactly what's
reused. The trade journal reuses the EXISTING HistoryQueryParams query
shape (same filters/pagination the /history endpoints already use).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, get_db
from app.schemas.history import HistoryQueryParams
from app.schemas.performance import AIPerformanceOverviewResponse, TradeJournalResponse
from app.services.ai_performance_service import get_performance_overview, get_trade_journal

router = APIRouter(prefix="/performance", tags=["performance"])


@router.get(
    "/overview",
    response_model=AIPerformanceOverviewResponse,
    summary="AI performance overview",
    description=(
        "Aggregate signal stats (existing StatsResponse, unchanged), win rate by confidence band, by "
        "symbol, and by asset class, plus calibration health per registered asset-type profile "
        "(crypto/gold/silver/oil/forex). Every figure is a real count/average over already-stored Signal "
        "rows - see app/services/ai_performance_service.py. Optional start_date/end_date (Phase 8) scope "
        "the confidence/symbol/asset-class breakdowns to trades closed in that window - omit both for the "
        "full trade history, unchanged from pre-Phase-8 behavior. stats and calibration_health always "
        "reflect full history regardless of this filter (see ai_performance_service.get_performance_overview)."
    ),
)
async def performance_overview(
    start_date: Optional[datetime] = Query(None, description="Only include trades closed on/after this timestamp"),
    end_date: Optional[datetime] = Query(None, description="Only include trades closed on/before this timestamp"),
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
):
    return await get_performance_overview(db, date_from=start_date, date_to=end_date)


@router.get(
    "/journal",
    response_model=TradeJournalResponse,
    summary="Trade journal review",
    description=(
        "Paginated review of closed (and cancelled) trades - entry/stop/take-profit, real exit price by "
        "outcome, confidence, risk/reward, real held-duration, and the VERBATIM reason recorded at signal "
        "generation time. Reuses the existing History module's query/filter/pagination shape unchanged."
    ),
)
async def trade_journal(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort_by: str = Query("closed_at"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    symbol: Optional[str] = None,
    direction: Optional[str] = None,
    result: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
):
    params = HistoryQueryParams(
        page=page, page_size=page_size, sort_by=sort_by, sort_order=sort_order,
        symbol=symbol, direction=direction, result=result, start_date=start_date, end_date=end_date,
    )
    return await get_trade_journal(db, params)
