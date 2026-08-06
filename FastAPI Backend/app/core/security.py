"""API authentication.

Authentication is *optional* and controlled by the ``API_KEY`` setting:

* When ``API_KEY`` is empty (default) the API is open — this keeps the bundled
  desktop client working with zero configuration.
* When ``API_KEY`` is set, every protected endpoint requires a matching
  ``X-API-Key`` header (or ``Authorization: Bearer <key>``).

Using a constant-time comparison avoids leaking the key through timing.
"""

from __future__ import annotations

import secrets
from typing import Optional

from fastapi import Header, HTTPException, status

from app.core.config import get_settings


def _extract_key(x_api_key: Optional[str], authorization: Optional[str]) -> Optional[str]:
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


async def get_current_user(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None),
) -> str:
    """Validate the request's API key when auth is enabled.

    Returns an opaque principal identifier. Raises 401 when a key is required
    but missing/invalid.
    """
    settings = get_settings()

    if not settings.auth_enabled:
        return "anonymous"

    provided = _extract_key(x_api_key, authorization)
    if not provided or not secrets.compare_digest(provided, settings.api_key.strip()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return "api_client"
