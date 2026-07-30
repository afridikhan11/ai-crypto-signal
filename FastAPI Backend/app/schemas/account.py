from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.services.binance_account_service import IncomeHistoryItem, OrderHistoryItem


class OrderHistoryResponse(BaseModel):
    items: list[OrderHistoryItem] = Field(default_factory=list)


class IncomeHistoryResponse(BaseModel):
    items: list[IncomeHistoryItem] = Field(default_factory=list)


class SaveCredentialsRequest(BaseModel):
    """Body for POST /api/v1/account/credentials. Credentials are encrypted at rest immediately."""

    api_key: str = Field(..., min_length=1)
    api_secret: str = Field(..., min_length=1)
    testnet: bool = False


class TestConnectionRequest(BaseModel):
    """
    Body for POST /api/v1/account/test-connection.
    All fields optional: if api_key/api_secret are omitted, the currently
    saved (encrypted) credentials are tested instead - this lets the UI
    test either brand-new, not-yet-saved keys, or the saved ones.
    """

    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    testnet: Optional[bool] = None


class CredentialsStatusResponse(BaseModel):
    has_credentials: bool
    environment: Optional[str] = None  # "mainnet" | "testnet", present only when has_credentials is True


class RiskSettingsResponse(BaseModel):
    risk_percent: float = Field(..., description="% of account balance suggested to risk per position")


class UpdateRiskSettingsRequest(BaseModel):
    risk_percent: float = Field(..., gt=0, le=100)
