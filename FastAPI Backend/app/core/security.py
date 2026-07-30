"""
JWT authentication primitives.

Single-admin-user model (matches app/services/binance_credentials.py's
"single-user deployment for now" pattern - there is no user table/
multi-tenant data isolation anywhere else in this backend yet). This
issues and verifies tokens for ONE admin account, configured via
ADMIN_USERNAME/ADMIN_PASSWORD_HASH env vars.

Real multi-user support (separate accounts, per-user signal/credential
isolation) is a materially bigger change - the scanner, Binance
credentials store, and signal tables are all currently single global
instances, not scoped per user - and is intentionally NOT what this module
does. It only stops an unauthenticated stranger from hitting the API.

Enforcement is gated by Settings.require_auth (default False) - see
app/api/dependencies.py.get_current_user. When True, the FastAPI/WPF
client must first POST /api/v1/auth/login and send the returned token as
`Authorization: Bearer <token>` on every subsequent request.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from passlib.context import CryptContext

from app.core.config import get_settings

JWT_ALGORITHM = "HS256"

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    return _pwd_context.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return _pwd_context.verify(plain_password, password_hash)
    except Exception:
        return False


def create_access_token(subject: str, expires_minutes: Optional[int] = None) -> str:
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes if expires_minutes is not None else settings.access_token_expire_minutes
    )
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[str]:
    """Returns the subject (username) if the token is valid and unexpired, else None."""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[JWT_ALGORITHM])
        return payload.get("sub")
    except jwt.PyJWTError:
        return None
