"""The tradeable symbol universe.

Kept out of ``main.py`` so the list can be overridden from configuration
(``SCAN_SYMBOLS``) and reused by tests without importing the FastAPI app.
"""

from __future__ import annotations

from typing import List

from app.core.config import get_settings

# Liquid USDT-margined perpetuals on Binance Futures.
DEFAULT_SYMBOLS: List[str] = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT",
    "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT", "UNIUSDT",
    "ATOMUSDT", "LTCUSDT", "ETCUSDT", "FILUSDT", "OPUSDT", "ARBUSDT",
    "APTUSDT", "NEARUSDT", "ICPUSDT", "AAVEUSDT", "INJUSDT",
    "SUIUSDT", "SEIUSDT", "TIAUSDT", "RUNEUSDT", "FETUSDT",
]

# Reference symbol used to derive broad-market (BTC) bias.
MARKET_LEADER = "BTCUSDT"


def get_scan_symbols() -> List[str]:
    """Return the configured symbol universe, falling back to the default."""
    configured = get_settings().scan_symbol_list
    symbols = configured or list(DEFAULT_SYMBOLS)
    # Always include the market leader so BTC bias can be computed.
    if MARKET_LEADER not in symbols:
        symbols = [MARKET_LEADER, *symbols]
    return symbols
