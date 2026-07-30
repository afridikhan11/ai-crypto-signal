"""
app/services/binance_credentials.py

Thin persistence/orchestration layer for the user's Binance API credentials.

Encryption itself lives in app.security.api_key_cipher.ApiKeyCipher.
Talking to Binance lives in app.services.binance_account_service.BinanceAccountService.
This module just wires the two together: where credentials are stored on
disk, and how to build a ready-to-use BinanceAccountService from them.

Single-user deployment for now (matches the rest of this backend, which
has no real multi-tenant auth yet) - one encrypted credentials file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from loguru import logger

from app.security.api_key_cipher import ApiKeyCipher
from app.services.binance_account_service import BinanceAccountService
from app.services.binance_trading_service import BinanceTradingService

# FastAPI Backend/data/binance_credentials.enc
CREDENTIALS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "binance_credentials.enc"


def has_saved_credentials() -> bool:
    return CREDENTIALS_PATH.exists()


def save_credentials(api_key: str, api_secret: str, testnet: bool) -> None:
    ApiKeyCipher().save_to_file(CREDENTIALS_PATH, api_key, api_secret, testnet)


def load_credentials() -> Optional[dict]:
    if not CREDENTIALS_PATH.exists():
        return None
    try:
        return ApiKeyCipher().decrypt(CREDENTIALS_PATH.read_text(encoding="utf-8"))
    except ValueError as exc:
        logger.error(f"Could not decrypt saved Binance credentials: {exc}")
        return None


def clear_credentials() -> None:
    if CREDENTIALS_PATH.exists():
        CREDENTIALS_PATH.unlink()
        logger.info("Saved Binance credentials deleted.")


def build_service_from_saved() -> BinanceAccountService:
    """Raises FileNotFoundError if no credentials have been saved yet."""
    creds = load_credentials()
    if creds is None:
        raise FileNotFoundError("No Binance API credentials have been saved yet.")
    return BinanceAccountService(
        api_key=creds["api_key"],
        api_secret=creds["api_secret"],
        testnet=bool(creds.get("testnet", False)),
    )


def build_trading_service_from_saved() -> BinanceTradingService:
    """
    Same saved credentials, same testnet/mainnet flag, as build_service_from_saved() -
    there is exactly one saved API key/environment for this single-user
    deployment, used for both read-only account viewing and (only when the
    user explicitly clicks Execute) order placement. Raises FileNotFoundError
    if no credentials have been saved yet.
    """
    creds = load_credentials()
    if creds is None:
        raise FileNotFoundError("No Binance API credentials have been saved yet.")
    return BinanceTradingService(
        api_key=creds["api_key"],
        api_secret=creds["api_secret"],
        testnet=bool(creds.get("testnet", False)),
    )
