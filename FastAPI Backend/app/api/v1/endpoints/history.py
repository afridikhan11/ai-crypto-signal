from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_signal_service, get_current_user
from app.services.signal_service import SignalService
from app.schemas.history import (
    HistoryListResponse,
    HistoryQueryParams,
    HistorySummaryResponse,
)

router = APIRouter(prefix="/history", tags=["history"])


def _build_params(
    page: int,
    page_size: int,
    sort_by: str,
    sort_order: str,
    symbol: Optional[str],
    direction: Optional[str],
    result: Optional[str],
    start_date: Optional[datetime],
    end_date: Optional[datetime],
) -> HistoryQueryParams:
    return HistoryQueryParams(
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
        symbol=symbol,
        direction=direction,
        result=result,
        start_date=start_date,
        end_date=end_date,
    )


@router.get(
    "",
    response_model=HistoryListResponse,
    summary="List closed signal history",
    description="Retrieve a paginated list of closed signals (TP hit, stopped, or cancelled) with optional filters.",
)
async def list_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort_by: str = Query("closed_at"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    symbol: Optional[str] = None,
    direction: Optional[str] = None,
    result: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    _user: str = Depends(get_current_user),
    service: SignalService = Depends(get_signal_service),
):
    """List closed signal history with pagination and filtering."""
    params = _build_params(
        page, page_size, sort_by, sort_order, symbol, direction, result, start_date, end_date
    )
    return await service.get_history(params)


@router.get(
    "/summary",
    response_model=HistorySummaryResponse,
    summary="History win/loss summary",
    description="Return aggregated win/loss statistics for closed signal history matching the given filters. Cancelled signals are excluded.",
)
async def history_summary(
    symbol: Optional[str] = None,
    direction: Optional[str] = None,
    result: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    _user: str = Depends(get_current_user),
    service: SignalService = Depends(get_signal_service),
):
    """Get aggregated win/loss statistics for closed signal history."""
    params = _build_params(
        1, 20, "closed_at", "desc", symbol, direction, result, start_date, end_date
    )
    return await service.get_history_summary(params)


@router.get(
    "/export",
    summary="Export closed signal history as CSV",
    description="Download the full (unpaginated) filtered signal history as a CSV file.",
)
async def export_history(
    sort_by: str = Query("closed_at"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    symbol: Optional[str] = None,
    direction: Optional[str] = None,
    result: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    _user: str = Depends(get_current_user),
    service: SignalService = Depends(get_signal_service),
):
    """Export filtered signal history as a downloadable CSV file."""
    params = _build_params(
        1, 20, sort_by, sort_order, symbol, direction, result, start_date, end_date
    )
    csv_text = await service.export_history_csv(params)

    filename = f"signal_history_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([csv_text]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
