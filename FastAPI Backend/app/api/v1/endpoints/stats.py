from fastapi import APIRouter, Depends

from app.api.dependencies import get_signal_service, get_current_user
from app.services.signal_service import SignalService
from app.schemas.stats import StatsResponse

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get(
    "",
    response_model=StatsResponse,
    summary="Performance statistics",
    description="Aggregated trading statistics for all signals. Zero values are returned if no data exists.",
)
async def get_stats(
    _user: str = Depends(get_current_user),
    service: SignalService = Depends(get_signal_service),
) -> StatsResponse:
    """Get overall trading performance statistics."""
    return await service.get_stats()