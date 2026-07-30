"""
Asset Resolver (Phase 2, 2026-07-27).

Extends the existing input-shape classifier (`app.services.market_scorer
.detect_input_type`, unmodified, imported directly) with:
  - free-text extraction (the existing function classifies one already-
    isolated string like "BTCUSDT"; this module finds and extracts the
    likely asset token(s) out of a full sentence like "should I buy
    ethereum right now"),
  - a name/alias map so "gold", "bitcoin", "wti" etc. resolve to the real
    Binance futures symbol this project actually tracks,
  - honest wallet-vs-contract disambiguation: an EVM/Solana address's
    *shape* alone can't tell a wallet from a token contract (both are
    just a hex/base58 string) - this only tags something a WALLET when the
    surrounding text says so ("this wallet", "my wallet", "address"
    without "token"/"contract"), and marks it unsupported rather than
    silently running a token scan against it or fabricating wallet data.
    Wallet holdings/PNL analysis does not exist anywhere in this codebase
    yet (see the Phase 1 Architecture Review) - flagging that honestly is
    this resolver's job, not pretending otherwise.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from app.core.constants import COMMODITY_SYMBOLS, asset_type_for_symbol
from app.services.market_scorer import detect_input_type

_EVM_ADDRESS_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
_SOLANA_ADDRESS_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
# A bare Binance-style pair typed directly, e.g. "BTCUSDT" or "btcusdt".
_TRADING_PAIR_RE = re.compile(r"\b([A-Za-z]{2,15}USDT?)\b")

_WALLET_HINT_WORDS = ("wallet", "my address", "holder address", "holdings of")
_CHAIN_HINT_WORDS = {
    "ethereum": "ethereum", "eth chain": "ethereum",
    "bsc": "bsc", "binance smart chain": "bsc", "bnb chain": "bsc",
    "polygon": "polygon", "matic chain": "polygon",
    "arbitrum": "arbitrum", "optimism": "optimism",
    "avalanche": "avalanche", "avax chain": "avalanche",
    "base chain": "base", "solana": "solana",
}

# Common names/tickers -> the exact Binance USD-M Futures symbol this
# project already tracks (see app/main.py's TOP_SYMBOLS and
# app/core/constants.COMMODITY_SYMBOLS) - kept intentionally to symbols
# this project actually scans, not an exhaustive market-wide alias table.
ASSET_ALIASES: dict[str, str] = {
    "bitcoin": "BTCUSDT", "btc": "BTCUSDT",
    "ethereum": "ETHUSDT", "eth": "ETHUSDT", "ether": "ETHUSDT",
    "bnb": "BNBUSDT", "binance coin": "BNBUSDT",
    "solana": "SOLUSDT", "sol": "SOLUSDT",
    "xrp": "XRPUSDT", "ripple": "XRPUSDT",
    "cardano": "ADAUSDT", "ada": "ADAUSDT",
    "dogecoin": "DOGEUSDT", "doge": "DOGEUSDT",
    "avalanche": "AVAXUSDT", "avax": "AVAXUSDT",
    "polkadot": "DOTUSDT", "dot": "DOTUSDT",
    "polygon": "MATICUSDT", "matic": "MATICUSDT",
    "chainlink": "LINKUSDT", "link": "LINKUSDT",
    "litecoin": "LTCUSDT", "ltc": "LTCUSDT",
    # Commodities - real Binance USD-M Futures contracts (see
    # app/core/constants.COMMODITY_SYMBOLS's own comment for launch dates).
    "gold": "XAUUSDT", "xau": "XAUUSDT", "xauusd": "XAUUSDT", "xauusdt": "XAUUSDT",
    "silver": "XAGUSDT", "xag": "XAGUSDT", "xagusd": "XAGUSDT", "xagusdt": "XAGUSDT",
    "oil": "CLUSDT", "wti": "CLUSDT", "crude": "CLUSDT", "crude oil": "CLUSDT",
    "brent": "BZUSDT", "brent crude": "BZUSDT",
}
# Longest alias first so "brent crude" matches before "crude" / "oil" would.
_ALIASES_BY_LENGTH = sorted(ASSET_ALIASES.keys(), key=len, reverse=True)


@dataclass
class ResolvedAsset:
    kind: str  # "trading_pair" | "contract_address" | "wallet_address" | "unknown"
    matched_text: str
    symbol: Optional[str] = None            # set when kind == "trading_pair"
    asset_type: Optional[str] = None        # crypto/gold/silver/oil - only for trading_pair
    address: Optional[str] = None           # set when kind in (contract_address, wallet_address)
    chain: Optional[str] = None             # set when a chain was named in the text, else None
    supported: bool = True                  # False for wallet_address - see module docstring


class AssetResolver:
    def _find_chain_hint(self, text: str) -> Optional[str]:
        lowered = text.lower()
        for phrase, chain in _CHAIN_HINT_WORDS.items():
            if phrase in lowered:
                return chain
        return None

    def _looks_like_wallet_intent(self, text: str) -> bool:
        lowered = text.lower()
        if "token" in lowered or "contract" in lowered:
            return False
        return any(hint in lowered for hint in _WALLET_HINT_WORDS)

    def resolve_all(self, text: str) -> List[ResolvedAsset]:
        """
        Finds every distinct asset mention in `text`. Order of precedence
        per match: contract/wallet address shape first (unambiguous,
        regex-anchored), then known alias names, then a bare
        already-Binance-shaped pair. Deduplicates by resolved
        symbol/address so "Compare BTC vs BTCUSDT" doesn't return BTC twice.
        """
        found: List[ResolvedAsset] = []
        seen_keys: set[str] = set()
        chain_hint = self._find_chain_hint(text)
        wallet_intent = self._looks_like_wallet_intent(text)

        for match in _EVM_ADDRESS_RE.findall(text) + _SOLANA_ADDRESS_RE.findall(text):
            if match in seen_keys:
                continue
            seen_keys.add(match)
            input_type = detect_input_type(match)
            if input_type != "contract_address":
                continue  # Solana regex is broad; detect_input_type() re-validates shape.
            if wallet_intent:
                found.append(ResolvedAsset(
                    kind="wallet_address", matched_text=match, address=match,
                    chain=chain_hint, supported=False,
                ))
            else:
                found.append(ResolvedAsset(
                    kind="contract_address", matched_text=match, address=match, chain=chain_hint,
                ))

        lowered = text.lower()
        for alias in _ALIASES_BY_LENGTH:
            if alias in lowered:
                symbol = ASSET_ALIASES[alias]
                if symbol in seen_keys:
                    continue
                seen_keys.add(symbol)
                found.append(ResolvedAsset(
                    kind="trading_pair", matched_text=alias, symbol=symbol,
                    asset_type=asset_type_for_symbol(symbol),
                ))

        for match in _TRADING_PAIR_RE.findall(text):
            symbol = match.upper()
            if not symbol.endswith("USDT"):
                symbol = symbol + "T" if symbol.endswith("USD") else symbol
            if symbol in seen_keys:
                continue
            seen_keys.add(symbol)
            found.append(ResolvedAsset(
                kind="trading_pair", matched_text=match, symbol=symbol,
                asset_type=asset_type_for_symbol(symbol),
            ))

        return found

    def resolve_one(self, text: str) -> Optional[ResolvedAsset]:
        matches = self.resolve_all(text)
        return matches[0] if matches else None

    @staticmethod
    def is_commodity(symbol: str) -> bool:
        return symbol.upper() in COMMODITY_SYMBOLS
