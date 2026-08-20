"""
Smart AI owner-gate auth primitives.

The Smart AI premium page is gated by ONE owner password. The only stored
secret is a bcrypt hash of that password in the OWNER_PASSWORD_HASH env var -
never the plaintext, never in the repo, never compiled into the WPF client
(which decompiles trivially, so client-side hiding is UX only, never the
security boundary).

Tokens are JWTs signed with SECRET_KEY, carrying `scope="smartai"` so an
ordinary admin token (app/core/security.py) can never be replayed against the
premium routes and vice-versa. Two token types are issued: a short-lived
`access` token (default 12h) sent on every request, and a longer-lived
`refresh` token (default 7d) that mints new access tokens without re-entering
the password. Enforcement lives in app/api/v1/endpoints/smartai.py; this module
is pure crypto/claims and does no I/O beyond reading settings.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

from app.core.config import get_settings
from app.core.security import JWT_ALGORITHM, _pwd_context

_SCOPE = "smartai"
_SUBJECT = "owner"


def owner_password_is_configured() -> bool:
    return bool(get_settings().owner_password_hash)


def verify_owner_password(plain_password: str) -> bool:
    """True only when a hash is configured AND the password matches it. A blank
    OWNER_PASSWORD_HASH can never authenticate anyone (fail closed)."""
    password_hash = get_settings().owner_password_hash
    if not password_hash:
        return False
    try:
        return _pwd_context.verify(plain_password, password_hash)
    except Exception:
        return False


def _encode(typ: str, expires_minutes: int) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": _SUBJECT,
        "scope": _SCOPE,
        "typ": typ,
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=JWT_ALGORITHM)


def create_access_token() -> str:
    return _encode("access", get_settings().smartai_token_expire_minutes)


def create_refresh_token() -> str:
    return _encode("refresh", get_settings().smartai_refresh_expire_minutes)


def decode_token(token: str, expected_typ: str) -> Optional[str]:
    """Return the subject if `token` is a valid, unexpired Smart AI token of the
    expected type (`access` or `refresh`), else None. Rejects tokens of the
    wrong scope or type so an access token cannot be used to refresh, an admin
    token cannot be used here, and a refresh token cannot authorise a request."""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
    if payload.get("scope") != _SCOPE or payload.get("typ") != expected_typ:
        return None
    return payload.get("sub")
