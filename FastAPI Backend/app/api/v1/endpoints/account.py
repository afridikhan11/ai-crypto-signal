from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.dependencies import get_current_user
from app.schemas.account import (
    CredentialsStatusResponse,
    IncomeHistoryResponse,
    OrderHistoryResponse,
    RiskSettingsResponse,
    SaveCredentialsRequest,
    TestConnectionRequest,
    UpdateRiskSettingsRequest,
)
from app.schemas.common import ErrorResponse
from app.services import binance_credentials
from app.services.binance_account_service import (
    AccountSnapshot,
    BinanceAccountService,
    ConnectionTestResult,
)
from app.services.trading_settings import get_risk_percent, set_risk_percent

router = APIRouter(prefix="/account", tags=["account"])


@router.get(
    "/credentials/status",
    response_model=CredentialsStatusResponse,
    summary="Check whether Binance API credentials are saved",
)
async def credentials_status(_user: str = Depends(get_current_user)):
    creds = binance_credentials.load_credentials()
    if creds is None:
        return CredentialsStatusResponse(has_credentials=False)
    return CredentialsStatusResponse(
        has_credentials=True,
        environment="testnet" if creds.get("testnet") else "mainnet",
    )


@router.post(
    "/credentials",
    response_model=CredentialsStatusResponse,
    summary="Save Binance API credentials (encrypted at rest)",
    description="Encrypts and persists the API key/secret. Does not verify them - use /test-connection for that.",
)
async def save_credentials(
    body: SaveCredentialsRequest,
    _user: str = Depends(get_current_user),
):
    binance_credentials.save_credentials(body.api_key, body.api_secret, body.testnet)
    return CredentialsStatusResponse(
        has_credentials=True, environment="testnet" if body.testnet else "mainnet"
    )


@router.delete(
    "/credentials",
    response_model=CredentialsStatusResponse,
    summary="Delete saved Binance API credentials",
)
async def delete_credentials(_user: str = Depends(get_current_user)):
    binance_credentials.clear_credentials()
    return CredentialsStatusResponse(has_credentials=False)


@router.post(
    "/test-connection",
    response_model=ConnectionTestResult,
    summary="Test Binance API credentials",
    description=(
        "Tests the credentials in the request body if both api_key and api_secret are "
        "provided; otherwise tests the currently saved (encrypted) credentials."
    ),
    responses={400: {"model": ErrorResponse}},
)
async def test_connection(
    body: TestConnectionRequest = Body(default_factory=TestConnectionRequest),
    _user: str = Depends(get_current_user),
):
    if body.api_key and body.api_secret:
        service = BinanceAccountService(
            api_key=body.api_key, api_secret=body.api_secret, testnet=bool(body.testnet)
        )
    else:
        try:
            service = binance_credentials.build_service_from_saved()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    try:
        return await service.test_connection()
    finally:
        await service.close()


@router.get(
    "/snapshot",
    response_model=AccountSnapshot,
    summary="Get full Binance account snapshot",
    description=(
        "Read-only account info, balances, futures, open orders, and auto-discovered "
        "trade history - using the saved credentials. Never places, cancels, or modifies "
        "any order."
    ),
    responses={400: {"model": ErrorResponse}},
)
async def get_snapshot(_user: str = Depends(get_current_user)):
    try:
        service = binance_credentials.build_service_from_saved()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        return await service.get_full_snapshot()
    finally:
        await service.close()


@router.get(
    "/order-history",
    response_model=OrderHistoryResponse,
    summary="Get Futures order history (any terminal status, not just open)",
    description=(
        "Read-only. Symbols auto-discovered the same way as trade history if not "
        "supplied. Never places, cancels, or modifies an order."
    ),
    responses={400: {"model": ErrorResponse}},
)
async def get_order_history(
    symbols: Optional[str] = Query(None, description="Comma-separated symbols, e.g. BTCUSDT,ETHUSDT"),
    limit: int = Query(50, ge=1, le=200),
    _user: str = Depends(get_current_user),
):
    try:
        service = binance_credentials.build_service_from_saved()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    symbol_list = [s.strip().upper() for s in symbols.split(",")] if symbols else None
    try:
        items = await service.get_order_history(symbols=symbol_list, limit=limit)
        return OrderHistoryResponse(items=items)
    finally:
        await service.close()


@router.get(
    "/income-history",
    response_model=IncomeHistoryResponse,
    summary="Get Futures transaction/income history (funding fees, realized PnL, transfers)",
    description="Read-only, account-wide - no symbol required.",
    responses={400: {"model": ErrorResponse}},
)
async def get_income_history(
    limit: int = Query(50, ge=1, le=200),
    income_type: Optional[str] = Query(
        None, description="Optional filter, e.g. FUNDING_FEE, REALIZED_PNL, COMMISSION, TRANSFER"
    ),
    _user: str = Depends(get_current_user),
):
    try:
        service = binance_credentials.build_service_from_saved()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        items = await service.get_income_history(limit=limit, income_type=income_type)
        return IncomeHistoryResponse(items=items)
    finally:
        await service.close()


@router.get(
    "/risk-settings",
    response_model=RiskSettingsResponse,
    summary="Get the current position-sizing risk percentage",
    description=(
        "The % of real account balance suggested to risk per position - used to compute "
        "suggested_risk_usd/suggested_quantity/suggested_notional_usd on every signal."
    ),
)
async def get_risk_settings(_user: str = Depends(get_current_user)):
    return RiskSettingsResponse(risk_percent=get_risk_percent())


@router.put(
    "/risk-settings",
    response_model=RiskSettingsResponse,
    summary="Update the position-sizing risk percentage",
)
async def update_risk_settings(
    body: UpdateRiskSettingsRequest,
    _user: str = Depends(get_current_user),
):
    try:
        value = set_risk_percent(body.risk_percent)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return RiskSettingsResponse(risk_percent=value)
