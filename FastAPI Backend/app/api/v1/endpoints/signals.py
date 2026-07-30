from fastapi import APIRouter, Depends, Query, HTTPException, Request
from typing import Optional
import uuid

from app.api.dependencies import get_signal_service, get_current_user
from app.services.signal_service import SignalService
from app.services.correlation_risk import attach_correlation_warnings
from app.schemas.signal import (
    SignalListResponse,
    SignalResponse,
    SignalQueryParams,
    PortfolioRiskResponse,
    DailyLossResponse,
)
from app.schemas.common import ErrorResponse

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get(
    "",
    response_model=SignalListResponse,
    summary="List signals",
    description="Retrieve a paginated list of trading signals with optional filters.",
)
async def list_signals(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    symbol: Optional[str] = None,
    direction: Optional[str] = None,
    status: Optional[str] = None,
    min_confidence: Optional[int] = None,
    timeframe: Optional[str] = None,
    asset_class: Optional[str] = Query(
        None, description="Filter by 'crypto' or 'commodity' (Gold/Silver/Crude/Brent)"
    ),
    _user: str = Depends(get_current_user),
    service: SignalService = Depends(get_signal_service),
):
    """List signals with pagination and filtering."""
    params = SignalQueryParams(
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
        symbol=symbol,
        direction=direction,
        status=status,
        min_confidence=min_confidence,
        timeframe=timeframe,
        asset_class=asset_class,
    )
    result = await service.get_signals(params)

    scanner = getattr(request.app.state, "scanner", None)
    if scanner is not None:
        attach_correlation_warnings(result.items, scanner.data_manager)

    return result


@router.get(
    "/latest",
    response_model=SignalResponse,
    summary="Latest signal",
    description="Return the most recently generated signal.",
    responses={404: {"model": ErrorResponse}},
)
async def latest_signal(
    _user: str = Depends(get_current_user),
    service: SignalService = Depends(get_signal_service),
):
    """Get the latest signal."""
    signal = await service.get_latest_signal()
    if not signal:
        raise HTTPException(status_code=404, detail="No signals found")
    return signal


@router.get(
    "/portfolio-risk",
    response_model=PortfolioRiskResponse,
    summary="Total risk exposure if every ACTIVE signal's suggested position were taken",
    description=(
        "Advisory only - the bot never executes trades, so this can't enforce a cap. "
        "Shows how much of the real account balance would be at risk at once if every "
        "currently-ACTIVE signal's suggested position were taken simultaneously."
    ),
)
async def portfolio_risk(
    _user: str = Depends(get_current_user),
    service: SignalService = Depends(get_signal_service),
):
    return await service.get_portfolio_risk()


@router.get(
    "/daily-loss",
    response_model=DailyLossResponse,
    summary="Today's realized loss/profit across closed signals (advisory)",
    description=(
        "Advisory only - counts realized loss/profit (using suggested position sizing) "
        "across signals closed since UTC midnight. `breached` flags that the warning "
        "threshold was crossed today; nothing is auto-paused."
    ),
)
async def daily_loss(
    _user: str = Depends(get_current_user),
    service: SignalService = Depends(get_signal_service),
):
    return await service.get_daily_loss()


@router.get(
    "/{signal_id}",
    response_model=SignalResponse,
    summary="Get signal by ID",
    description="Retrieve a single signal by its unique identifier.",
    responses={404: {"model": ErrorResponse}},
)
async def get_signal(
    signal_id: uuid.UUID,
    _user: str = Depends(get_current_user),
    service: SignalService = Depends(get_signal_service),
):
    """Get a specific signal by ID."""
    signal = await service.get_signal_by_id(signal_id)
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    return signal