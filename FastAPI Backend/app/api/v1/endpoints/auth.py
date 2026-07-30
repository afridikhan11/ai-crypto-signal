from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm

from app.core.config import get_settings
from app.core.security import create_access_token, verify_password
from app.schemas.auth import TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Log in and get a JWT access token",
    description=(
        "Only meaningful once REQUIRE_AUTH=true (see .env.production.example - it's "
        "off by default in dev). Single admin account, configured via "
        "ADMIN_USERNAME/ADMIN_PASSWORD_HASH (generate a hash with "
        "scripts/generate_password_hash.py). Use the returned token as "
        "'Authorization: Bearer <token>' on other requests, or click the Swagger "
        "'Authorize' button above and paste the username/password there directly."
    ),
)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    settings = get_settings()

    if form_data.username != settings.admin_username or not verify_password(
        form_data.password, settings.admin_password_hash
    ):
        raise HTTPException(status_code=401, detail="Incorrect username or password")

    token = create_access_token(subject=form_data.username)
    return TokenResponse(
        access_token=token,
        expires_in_minutes=settings.access_token_expire_minutes,
    )
