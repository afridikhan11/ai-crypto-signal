from fastapi import APIRouter, Depends, Query, HTTPException
from typing import Optional
import uuid

from app.api.dependencies import get_signal_service, get_current_user
from app.services.signal_service import SignalService
from app.schemas.signal import (
    SignalListResponse,
    SignalResponse,
    SignalQueryParams,
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
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    symbol: Optional[str] = None,
    direction: Optional[str] = None,
    status: Optional[str] = None,
    min_confidence: Optional[int] = None,
    timeframe: Optional[str] = None,
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
    )
    return await service.get_signals(params)


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