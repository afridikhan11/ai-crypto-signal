from typing import AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.services.signal_service import SignalService


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Provide an async database session, ensuring it is closed after use."""
    async with AsyncSessionLocal() as session:
        yield session


def get_signal_service(db: AsyncSession = Depends(get_db)) -> SignalService:
    """Provide a SignalService instance with an active database session."""
    return SignalService(db)


# TODO: Replace with JWT token validation for production authentication.
async def get_current_user() -> str:
    """Temporary user identifier; will be replaced by JWT-based authentication."""
    return "demo_user"