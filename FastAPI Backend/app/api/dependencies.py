from typing import AsyncGenerator, Optional

from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.security import decode_access_token
from app.services.signal_service import SignalService

# auto_error=False so this doesn't 401 by itself when REQUIRE_AUTH is off -
# get_current_user below decides whether a missing/invalid token matters.
_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/v1/auth/login", auto_error=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Provide an async database session, ensuring it is closed after use."""
    async with AsyncSessionLocal() as session:
        yield session


def get_signal_service(db: AsyncSession = Depends(get_db)) -> SignalService:
    """Provide a SignalService instance with an active database session."""
    return SignalService(db)


async def get_current_user(token: Optional[str] = Depends(_oauth2_scheme)) -> str:
    """
    Gated by Settings.require_auth (default False, see app/core/config.py):
      - require_auth=False (today's dev default): behaves exactly as before
        this change - returns "demo_user" unconditionally, no Authorization
        header needed. The WPF app keeps working unmodified.
      - require_auth=True (set in .env.production.example): a valid JWT
        (from POST /api/v1/auth/login) is required, or this raises 401.
    See app/core/security.py's module docstring for the single-admin-user
    model this implements and what it deliberately does NOT do.
    """
    settings = get_settings()
    if not settings.require_auth:
        return "demo_user"

    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    username = decode_access_token(token)
    if username is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    return username