"""
GoPlus Security integration - free, public, no API key required for the
request volume a single-user token scanner generates.

This is the REAL, working substitute for "Token Security" section of the
report (mint function, honeypot, blacklist, whitelist, ownership, proxy
contract, liquidity lock, LP burn) - GoPlus is a real security-scanning
service used by many DEX aggregators for exactly this purpose, unlike
Arkham/Nansen/Bubblemaps which have no free API (see token_scorer.py's
module docstring for the full explanation of what's real vs unavailable
in this report).

--------------------------------------------------------------------------
LEGACY - BACKWARD COMPATIBILITY ONLY (marked 2026-07-26)
--------------------------------------------------------------------------
This module's output (`fetch_token_security_summary`) now only feeds the
legacy `TokenSecuritySection` API field (see app/schemas/token_scan.py) -
the WPF app's UI no longer displays it; the full Contract Security
Dashboard (app/services/contract_security_service.py) replaced it in the
UI. This module is intentionally kept as-is (not deprecated, not removed)
because `token_security` is still a required field on the public
`TokenScanReport` API response, and the user explicitly chose backward
compatibility over cleanup while this project is under active
development. Do NOT build new features on top of this module - all new
security-related work goes in contract_security_service.py. Any future
proposal to deprecate/remove this module or the `token_security` field is
an API-breaking change and needs the user's explicit approval first.
--------------------------------------------------------------------------

Docs: https://docs.gopluslabs.io/reference/token-security-api
Rate limit: free tier is generously rate-limited for this kind of
on-demand, single-token usage.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

# Moved to app.core.constants on 2026-07-26 so this module no longer needs
# to be the shared source of this mapping for other services (see
# app.services.contract_security_service, which needs the same mapping) -
# re-imported here under the original name so every existing call site in
# this file below is unchanged. Pure relocation, same values.
from app.core.constants import EVM_CHAIN_TO_GOPLUS_ID

# Solana's well-known "incinerator" burn address (SPL tokens sent here are
# permanently unrecoverable) - the Solana equivalent of EVM's 0x000...dead,
# used to recognize burned LP the same way the EVM path recognizes it via
# `tag in ("locked", "burn")`.
SOLANA_BURN_ADDRESS = "1nc1nerator11111111111111111111111111111"


class TokenSecurityError(Exception):
    """Raised when GoPlus's API is unreachable or returns no usable data."""


class TokenSecurityService:
    BASE_URL = "https://api.gopluslabs.io/api/v1"
    HTTP_TIMEOUT = 15.0

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.HTTP_TIMEOUT)
        return self._client

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "TokenSecurityService":
        return self

    async def __aexit__(self, *exc_info):
        await self.close()

    async def _fetch_raw(self, chain: str, token_address: str) -> Dict[str, Any]:
        client = await self._get_client()
        token_address = token_address.lower() if chain != "solana" else token_address

        if chain == "solana":
            url = f"{self.BASE_URL}/solana/token_security"
        elif chain in EVM_CHAIN_TO_GOPLUS_ID:
            url = f"{self.BASE_URL}/token_security/{EVM_CHAIN_TO_GOPLUS_ID[chain]}"
        else:
            raise TokenSecurityError(f"Unsupported chain for token security check: {chain}")

        try:
            resp = await client.get(url, params={"contract_addresses": token_address})
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            raise TokenSecurityError(f"GoPlus request failed: {e}") from e

        if data.get("code") != 1:
            raise TokenSecurityError(f"GoPlus returned an error: {data.get('message', 'unknown error')}")

        result = data.get("result") or {}
        token_data = result.get(token_address.lower()) if chain != "solana" else result.get(token_address)
        if not token_data:
            raise TokenSecurityError("GoPlus has no security data for this contract yet.")
        return token_data

    @staticmethod
    def _to_bool(value: Any) -> Optional[bool]:
        """
        GoPlus encodes most top-level EVM booleans as the strings "0"/"1",
        but fields nested inside lists (e.g. `lp_holders[].is_locked`,
        `holders[].is_contract`) come back as real JSON integers 0/1, not
        strings - verified against a live GoPlus response. The original
        version of this method only handled bool/str, so `is_locked`
        (int 0/1) always fell through to `return None` (falsy), meaning
        LP-locked detection never actually matched a locked holder even
        when GoPlus reported one.

        BUG FIX: added the int/float branch below so both encodings are
        recognized. Leaves genuinely missing fields as None, never
        guessing False.
        """
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            if value == "1":
                return True
            if value == "0":
                return False
        return None

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _normalize_evm(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        lp_holders: List[Dict[str, Any]] = raw.get("lp_holders") or []
        lp_locked_pct = None
        if lp_holders:
            locked = sum(
                self._to_float(h.get("percent")) or 0.0
                for h in lp_holders
                if self._to_bool(h.get("is_locked")) or (h.get("tag") or "").lower() in ("locked", "burn")
            )
            lp_locked_pct = round(locked * 100, 2)

        holders: List[Dict[str, Any]] = raw.get("holders") or []
        top_10_pct = None
        if holders:
            top_10_pct = round(
                sum(self._to_float(h.get("percent")) or 0.0 for h in holders[:10]) * 100, 2
            )

        return {
            "mint_function_present": self._to_bool(raw.get("is_mintable")),
            "honeypot": self._to_bool(raw.get("is_honeypot")),
            "has_blacklist_function": self._to_bool(raw.get("is_blacklisted")),
            "has_whitelist_function": self._to_bool(raw.get("is_whitelisted")),
            "ownership_renounced": (
                raw.get("owner_address") in (None, "", "0x0000000000000000000000000000000000000000")
            ),
            "is_proxy_contract": self._to_bool(raw.get("is_proxy")),
            "is_open_source": self._to_bool(raw.get("is_open_source")),
            "lp_locked_pct": lp_locked_pct,
            "buy_tax_pct": (self._to_float(raw.get("buy_tax")) or 0) * 100 if raw.get("buy_tax") else self._to_float(raw.get("buy_tax")),
            "sell_tax_pct": (self._to_float(raw.get("sell_tax")) or 0) * 100 if raw.get("sell_tax") else self._to_float(raw.get("sell_tax")),
            "cannot_sell_all": self._to_bool(raw.get("cannot_sell_all")),
            "holder_count": int(raw["holder_count"]) if raw.get("holder_count") else None,
            "top_10_holder_pct": top_10_pct,
            "creator_pct": self._to_float(raw.get("creator_percent")),
        }

    def _normalize_solana(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        holders: List[Dict[str, Any]] = raw.get("holders") or []
        top_10_pct = None
        if holders:
            top_10_pct = round(
                sum(self._to_float(h.get("percent")) or 0.0 for h in holders[:10]) * 100, 2
            )

        mint_authority = (raw.get("mintable") or {}).get("authority")
        freeze_authority = (raw.get("freezable") or {}).get("authority")

        # BUG FIX: GoPlus's Solana endpoint returns `non_transferable` as a
        # plain "0"/"1" STRING (verified against a live response), not a
        # dict with a "status" key - the previous `.get("status")` call on
        # that string raised AttributeError on every single Solana scan,
        # crashing the honeypot check (and the endpoint's error handling
        # then silently downgraded the whole security section to
        # "unavailable" instead of showing this real, computable field).
        honeypot = self._to_bool(raw.get("non_transferable"))

        # BUG FIX: `lp_locked_percent` is not a field GoPlus's Solana
        # endpoint actually returns (verified against a live response) -
        # this always evaluated to None. Real LP lock data lives in
        # `lp_holders` (each entry has `is_locked` and an `account`
        # address) - same `is_locked`-or-burn-address convention already
        # used by `_normalize_evm()` above, just for Solana's schema.
        lp_holders: List[Dict[str, Any]] = raw.get("lp_holders") or []
        lp_locked_pct = None
        if lp_holders:
            locked = sum(
                self._to_float(h.get("percent")) or 0.0
                for h in lp_holders
                if self._to_bool(h.get("is_locked")) or h.get("account") == SOLANA_BURN_ADDRESS
            )
            lp_locked_pct = round(locked * 100, 2)

        return {
            "mint_function_present": bool(mint_authority),
            "honeypot": honeypot,
            "has_blacklist_function": None,  # Not a Solana SPL-token concept the same way as EVM.
            "has_whitelist_function": None,
            "ownership_renounced": not bool(mint_authority) and not bool(freeze_authority),
            "is_proxy_contract": None,  # Not applicable to SPL tokens.
            "is_open_source": None,  # GoPlus's Solana endpoint doesn't report this.
            "lp_locked_pct": lp_locked_pct,
            "buy_tax_pct": self._to_float((raw.get("transfer_fee") or {}).get("current", {}).get("percent")),
            "sell_tax_pct": None,
            "cannot_sell_all": None,
            "holder_count": int(raw["holder_count"]) if raw.get("holder_count") else None,
            "top_10_holder_pct": top_10_pct,
            "creator_pct": None,
        }

    async def check(self, chain: str, token_address: str) -> Dict[str, Any]:
        raw = await self._fetch_raw(chain, token_address)
        if chain == "solana":
            return self._normalize_solana(raw)
        return self._normalize_evm(raw)


async def fetch_token_security_summary(chain: str, token_address: str) -> Dict[str, Any]:
    """
    Convenience one-shot for the Token Scanner - never raises, returns a
    dict with an "error" key on failure instead, so a security-check
    outage doesn't crash the whole scan (the rest of the report can still
    render, just with the security section marked unavailable).
    """
    async with TokenSecurityService() as service:
        try:
            return await service.check(chain, token_address)
        except TokenSecurityError as e:
            logger.warning(f"[token-scan] Token security check failed for {token_address}: {e}")
            return {"error": str(e)}
