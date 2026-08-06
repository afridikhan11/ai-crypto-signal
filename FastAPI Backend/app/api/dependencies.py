from typing import AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.core.security import get_current_user  # re-exported for endpoints
from app.services.signal_service import SignalService

__all__ = ["get_db", "get_signal_service", "get_current_user"]


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Provide an async database session, ensuring it is closed after use."""
    async with AsyncSessionLocal() as session:
        yield session


def get_signal_service(db: AsyncSession = Depends(get_db)) -> SignalService:
    """Provide a SignalService instance with an active database session."""
    return SignalService(db)