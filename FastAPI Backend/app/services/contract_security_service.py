"""
Institutional-grade Contract Security Dashboard - on-chain Token Scanner
only (not applicable to Binance trading-pair symbols, which have no smart
contract to inspect). Mirrors the shape/quality of the sibling Technical
Analysis (`ta_dashboard.py`) and Smart Money (`smart_money_service.py`)
dashboards, and reuses `ta_dashboard.py`'s `_section()` row helper
directly rather than duplicating it.

--------------------------------------------------------------------------
WHAT'S REAL VS UNAVAILABLE IN THIS DASHBOARD
--------------------------------------------------------------------------
Every one of the 20 fields below is backed by a REAL, free, no-API-key
provider response - never a placeholder or a guessed value. Four
providers are supported, mirroring `app/services/token_security_service.py`
(GoPlus) but fetched independently here (a small, deliberate duplication -
same isolation rationale as `market_scorer.py` vs `signal_generator.py`:
this dashboard needs several RAW fields GoPlus's existing normalized
`TokenSecuritySection` doesn't expose, e.g. the split between LP "locked"
vs LP "burned", the raw owner/creator address, and `transfer_pausable` -
so it fetches its own copy of the raw response rather than reaching into
`token_security_service.py`'s internals or changing that already-shipped
module):

  - GoPlus Security (api.gopluslabs.io) - free, no key. Covers EVM chains
    AND Solana. Real fields used: is_open_source, is_mintable,
    owner_address, is_proxy, is_honeypot, buy_tax, sell_tax,
    transfer_tax, is_blacklisted, is_whitelisted, transfer_pausable,
    lp_holders (split into locked vs burned by address/flag),
    creator_address, creator_percent, holders (top holder %). Solana
    endpoint fields: mintable/freezable/non_transferable/
    transfer_fee_upgradable/trusted_token/holders.
  - Honeypot.is (api.honeypot.is) - free, no API key currently required
    (confirmed via their own docs: "API Keys are currently not required").
    EVM chains only. Real fields used: honeypotResult.isHoneypot,
    simulationResult.buyTax/sellTax/transferTax, contractCode.openSource/
    isProxy.
  - RugCheck (api.rugcheck.xyz) - free, no API key, public read endpoints.
    Solana only. Real fields used: token.mintAuthority/freezeAuthority,
    creator/creatorBalance, transferFee.pct, verification.jup_verified,
    tokenMeta.mutable, rugged, topHolders, and the summary endpoint's
    lpLockedPct/score_normalised.
  - TokenSniffer - an official API DOES exist, but it is a PAID product
    (Sniffer Pack Pro, $99/mo for programmatic access - the free web UI at
    tokensniffer.com has no public API behind it). Per this project's
    "never fabricate, report Not Available" rule, `TokenSnifferProvider`
    is an honest, env-var-gated stub identical in spirit to this
    project's `ArkhamProvider`/`NansenProvider` - it contributes nothing
    today, but is a pure addition to wire in later if a subscription is
    ever purchased.

Every field is computed from whichever real provider(s) apply to the
token's chain; when zero providers have data for a field, it is reported
as `status="Not Available"` with a specific reason, never guessed. When
more than one provider reports the SAME field, all readings are shown
verbatim in `detail` (so any disagreement between providers is visible to
the user) and the FIRST provider in that chain's priority order is used as
the displayed value - readings are never averaged/blended across sources
of potentially different scale.

2026-07-26 addition: `holder_count` (raw total holder count) and
`top_10_holder_pct` (combined % held by the top 10 wallets, a genuinely
different metric from `top_holder_pct`'s single-largest-wallet figure)
were migrated in from the legacy "Token Security (GoPlus)" card in
TokenScannerView.xaml, per the user's explicit approval to retire that
card once these 2 fields it uniquely showed were preserved here. They are
purely informational (no AI-score impact) - same as before.
--------------------------------------------------------------------------
PROVIDER ARCHITECTURE
--------------------------------------------------------------------------
`SecurityProvider` is the base class - `readings(ctx)` returns a dict of
`{category_key: raw_value_or_None}`; a concrete provider only fills in
what it can genuinely back with real data. This is the same shape used by
`smart_money_service.py`'s `SmartMoneyProvider` (reused architecture, not
reinvented): `GoPlusSecurityProvider`, `HoneypotIsProvider`,
`RugCheckProvider` each implement `readings()` from data already fetched
into a `SecurityContext`; `TokenSnifferProvider` is the honest paid-only
stub. `build_contract_security_dashboard()` picks the provider priority
order for the token's chain, collects every reading per category, and
falls back to an explicit "Not Available" row when nothing real is found.
--------------------------------------------------------------------------
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx
from loguru import logger

from app.core.constants import EVM_CHAIN_TO_GOPLUS_ID
from app.services.ta_dashboard import _section

GOPLUS_BASE_URL = "https://api.gopluslabs.io/api/v1"
HONEYPOT_IS_BASE_URL = "https://api.honeypot.is/v2"
RUGCHECK_BASE_URL = "https://api.rugcheck.xyz/v1"
HTTP_TIMEOUT = 15.0

# Addresses treated as "burned" (tokens sent here are permanently
# unrecoverable) - distinct from GoPlus's `is_locked` flag (tokens held by
# a real time-locker contract) so "LP Locked" and "LP Burned" can be
# reported as two genuinely different, real signals instead of one
# conflated number.
EVM_BURN_ADDRESSES = {
    "0x000000000000000000000000000000000000dead",
    "0x0000000000000000000000000000000000000000",
}
# Solana's well-known "incinerator" burn address (SPL tokens sent here are
# permanently unrecoverable) - the direct Solana equivalent of EVM's
# 0x000...dead, used to split GoPlus's Solana lp_holders into locked vs
# burned the same way _parse_goplus_evm() does for EVM.
SOLANA_BURN_ADDRESS = "1nc1nerator11111111111111111111111111111"

CATEGORY_LABELS: Dict[str, str] = {
    "verified_contract": "Verified Contract",
    "mint_authority": "Mint Authority",
    "ownership": "Ownership",
    "ownership_renounced": "Ownership Renounced",
    "proxy_contract": "Proxy Contract",
    "upgradeable_contract": "Upgradeable Contract",
    "honeypot": "Honeypot",
    "buy_tax": "Buy Tax",
    "sell_tax": "Sell Tax",
    "transfer_tax": "Transfer Tax",
    "blacklist": "Blacklist",
    "whitelist": "Whitelist",
    "trading_pause": "Trading Pause",
    "lp_locked": "LP Locked",
    "lp_burned": "LP Burned",
    "creator_wallet": "Creator Wallet",
    "creator_holding_pct": "Creator Holding %",
    "top_holder_pct": "Top Holder %",
    "holder_count": "Holder Count",
    "top_10_holder_pct": "Top 10 Holders %",
}

# Order matches the user-facing spec exactly.
CATEGORY_ORDER: List[str] = [
    "verified_contract",
    "mint_authority",
    "ownership",
    "ownership_renounced",
    "proxy_contract",
    "upgradeable_contract",
    "honeypot",
    "buy_tax",
    "sell_tax",
    "transfer_tax",
    "blacklist",
    "whitelist",
    "trading_pause",
    "lp_locked",
    "lp_burned",
    "creator_wallet",
    "creator_holding_pct",
    "top_holder_pct",
    # Migrated in 2026-07-26 from the retired legacy "Token Security
    # (GoPlus)" card - the only 2 fields it showed that weren't already
    # covered above.
    "holder_count",
    "top_10_holder_pct",
]

# "bool_safe": True is a GOOD sign (e.g. verified, renounced) - strength
#   (how significant/concerning the reading is) is high when False.
# "bool_risk": True is a BAD sign (e.g. honeypot, blacklist) - strength is
#   high when True.
# "pct_risk": a higher percentage is more concerning (taxes, concentration).
# "pct_safe": a higher percentage is a GOOD sign (LP locked/burned).
# "address": a raw wallet/contract address string - no risk polarity.
# "count_neutral": a raw count (e.g. total holder count) - purely
#   informational, no risk polarity, never scored.
CATEGORY_KIND: Dict[str, str] = {
    "verified_contract": "bool_safe",
    "mint_authority": "bool_risk",
    "ownership": "address",
    "ownership_renounced": "bool_safe",
    "proxy_contract": "bool_risk",
    "upgradeable_contract": "bool_risk",
    "honeypot": "bool_risk",
    "buy_tax": "pct_risk",
    "sell_tax": "pct_risk",
    "transfer_tax": "pct_risk",
    "blacklist": "bool_risk",
    "whitelist": "bool_risk",
    "trading_pause": "bool_risk",
    "lp_locked": "pct_safe",
    "lp_burned": "pct_safe",
    "creator_wallet": "address",
    "creator_holding_pct": "pct_risk",
    "top_holder_pct": "pct_risk",
    "holder_count": "count_neutral",
    "top_10_holder_pct": "pct_risk",
}

BOOL_LABELS: Dict[str, Tuple[str, str]] = {
    "verified_contract": ("Verified", "Not Verified"),
    "mint_authority": ("Present", "Absent"),
    "ownership_renounced": ("Renounced", "Not Renounced"),
    "proxy_contract": ("Yes", "No"),
    "upgradeable_contract": ("Yes", "No"),
    "honeypot": ("HONEYPOT DETECTED", "Clean"),
    "blacklist": ("Enabled", "Disabled"),
    "whitelist": ("Enabled", "Disabled"),
    "trading_pause": ("Enabled", "Disabled"),
}

# How strongly a percentage reading drives "strength" (0-100). Taxes are
# scaled steeply (25% tax -> max strength); holding concentration is
# scaled more gently (50% -> max strength); LP locked/burned use the raw
# percentage directly (100% locked -> max strength).
PCT_RISK_SCALE: Dict[str, float] = {
    "buy_tax": 4.0,
    "sell_tax": 4.0,
    "transfer_tax": 4.0,
    "creator_holding_pct": 2.0,
    "top_holder_pct": 2.0,
    "top_10_holder_pct": 1.5,
}

SOLANA_NOT_APPLICABLE = {
    "proxy_contract", "blacklist", "whitelist", "trading_pause",
}


@dataclass
class SecurityContext:
    chain: str
    token_address: str
    goplus_raw: Optional[Dict[str, Any]] = None
    honeypot_raw: Optional[Dict[str, Any]] = None
    rugcheck_report: Optional[Dict[str, Any]] = None
    rugcheck_summary: Optional[Dict[str, Any]] = None


# --------------------------------------------------------------------------
# Raw fetchers - each is a real HTTP call to a real free endpoint, never
# raising (returns None on any failure so one provider outage doesn't
# block the others).
# --------------------------------------------------------------------------

async def _fetch_goplus_raw(chain: str, token_address: str) -> Optional[Dict[str, Any]]:
    address = token_address.lower() if chain != "solana" else token_address
    if chain == "solana":
        url = f"{GOPLUS_BASE_URL}/solana/token_security"
    elif chain in EVM_CHAIN_TO_GOPLUS_ID:
        url = f"{GOPLUS_BASE_URL}/token_security/{EVM_CHAIN_TO_GOPLUS_ID[chain]}"
    else:
        return None
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(url, params={"contract_addresses": address})
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning(f"[contract-security] GoPlus fetch failed for {token_address}: {e}")
        return None

    if data.get("code") != 1:
        return None
    result = data.get("result") or {}
    token_data = result.get(address.lower()) if chain != "solana" else result.get(address)
    return token_data or None


async def _fetch_honeypot_is(chain: str, token_address: str) -> Optional[Dict[str, Any]]:
    """EVM-only, free, no API key (Honeypot.is: "API Keys are currently
    not required")."""
    chain_id = EVM_CHAIN_TO_GOPLUS_ID.get(chain)
    if chain_id is None:
        return None
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(
                f"{HONEYPOT_IS_BASE_URL}/IsHoneypot",
                params={"address": token_address, "chainID": chain_id},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning(f"[contract-security] Honeypot.is fetch failed for {token_address}: {e}")
        return None
    return data or None


async def _fetch_rugcheck(token_address: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Solana-only, free, no API key. Fetches the detailed report (creator,
    mint/freeze authority, transfer fee, Jupiter verification) and the
    summary report (lpLockedPct, normalized score) concurrently - each
    exposes real fields the other doesn't."""
    async def _get(path: str) -> Optional[Dict[str, Any]]:
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                resp = await client.get(f"{RUGCHECK_BASE_URL}/tokens/{token_address}/{path}")
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.warning(f"[contract-security] RugCheck {path} fetch failed for {token_address}: {e}")
            return None

    report, summary = await asyncio.gather(_get("report"), _get("report/summary"))
    return report, summary


# --------------------------------------------------------------------------
# Small numeric helpers - shared conversion conventions, never guessing a
# scale: GoPlus's percent-ish fields (buy_tax, sell_tax, transfer_tax,
# creator_percent, holder percent, lp_holder percent) are fractions of 1
# (0.05 = 5%) per their documented schema and this project's own
# already-verified `token_security_service.py` convention - multiply by
# 100 for display. Honeypot.is's tax fields are already plain percentages
# per their documented response example.
# --------------------------------------------------------------------------

def _to_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        if value in ("1", "true", "True"):
            return True
        if value in ("0", "false", "False"):
            return False
    return None


def _to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _goplus_pct(value: Any) -> Optional[float]:
    """GoPlus percent-ish fields are fractions of 1 - convert to a 0-100 percent."""
    f = _to_float(value)
    return round(f * 100, 4) if f is not None else None


# --------------------------------------------------------------------------
# Per-provider parsers - each returns {category_key: value_or_None}.
# Deliberately separate EVM vs Solana parsing for GoPlus (their two
# endpoints have entirely different schemas), same pattern already
# established by token_security_service.py's own _normalize_evm/_normalize_solana.
# --------------------------------------------------------------------------

def _parse_goplus_evm(raw: Dict[str, Any]) -> Dict[str, Any]:
    lp_holders: List[Dict[str, Any]] = raw.get("lp_holders") or []
    lp_locked_pct = None
    lp_burned_pct = None
    if lp_holders:
        locked = 0.0
        burned = 0.0
        for h in lp_holders:
            pct = _to_float(h.get("percent")) or 0.0
            address = (h.get("address") or "").lower()
            if address in EVM_BURN_ADDRESSES:
                burned += pct
            elif _to_bool(h.get("is_locked")):
                locked += pct
        lp_locked_pct = round(locked * 100, 2)
        lp_burned_pct = round(burned * 100, 2)

    holders: List[Dict[str, Any]] = raw.get("holders") or []
    top_holder_pct = _goplus_pct(holders[0].get("percent")) if holders else None
    top_10_holder_pct = (
        round(sum(_to_float(h.get("percent")) or 0.0 for h in holders[:10]) * 100, 2)
        if holders else None
    )
    holder_count = int(raw["holder_count"]) if raw.get("holder_count") else None

    owner_address = raw.get("owner_address")
    if owner_address in ("", "0x0000000000000000000000000000000000000000"):
        owner_address = None

    is_proxy = _to_bool(raw.get("is_proxy"))

    return {
        "verified_contract": _to_bool(raw.get("is_open_source")),
        "mint_authority": _to_bool(raw.get("is_mintable")),
        "ownership": owner_address,
        "ownership_renounced": owner_address is None,
        "proxy_contract": is_proxy,
        "upgradeable_contract": is_proxy,  # same underlying GoPlus signal - disclosed, not a second data point
        "honeypot": _to_bool(raw.get("is_honeypot")),
        "buy_tax": _goplus_pct(raw.get("buy_tax")),
        "sell_tax": _goplus_pct(raw.get("sell_tax")),
        "transfer_tax": _goplus_pct(raw.get("transfer_tax")),
        "blacklist": _to_bool(raw.get("is_blacklisted")),
        "whitelist": _to_bool(raw.get("is_whitelisted")),
        "trading_pause": _to_bool(raw.get("transfer_pausable")),
        "lp_locked": lp_locked_pct,
        "lp_burned": lp_burned_pct,
        "creator_wallet": raw.get("creator_address") or None,
        "creator_holding_pct": _goplus_pct(raw.get("creator_percent")),
        "top_holder_pct": top_holder_pct,
        "holder_count": holder_count,
        "top_10_holder_pct": top_10_holder_pct,
    }


def _parse_goplus_solana(raw: Dict[str, Any]) -> Dict[str, Any]:
    mintable_status = _to_bool((raw.get("mintable") or {}).get("status"))
    freezable_status = _to_bool((raw.get("freezable") or {}).get("status"))

    ownership_renounced = None
    if mintable_status is not None and freezable_status is not None:
        ownership_renounced = not mintable_status and not freezable_status

    upgradable_flags = [
        _to_bool((raw.get("transfer_fee_upgradable") or {}).get("status")),
        _to_bool((raw.get("default_account_state_upgradable") or {}).get("status")),
        _to_bool((raw.get("transfer_hook_upgradable") or {}).get("status")),
        _to_bool((raw.get("metadata_mutable") or {}).get("status")),
    ]
    known_flags = [f for f in upgradable_flags if f is not None]
    upgradeable = any(known_flags) if known_flags else None

    lp_holders: List[Dict[str, Any]] = raw.get("lp_holders") or []
    lp_locked_pct = None
    lp_burned_pct = None
    if lp_holders:
        locked = 0.0
        burned = 0.0
        for h in lp_holders:
            pct = _to_float(h.get("percent")) or 0.0
            account = h.get("account") or ""
            if account == SOLANA_BURN_ADDRESS:
                burned += pct
            elif _to_bool(h.get("is_locked")):
                locked += pct
        lp_locked_pct = round(locked * 100, 2)
        lp_burned_pct = round(burned * 100, 2)

    holders: List[Dict[str, Any]] = raw.get("holders") or []
    top_holder_pct = _goplus_pct(holders[0].get("percent")) if holders else None
    top_10_holder_pct = (
        round(sum(_to_float(h.get("percent")) or 0.0 for h in holders[:10]) * 100, 2)
        if holders else None
    )
    holder_count = int(raw["holder_count"]) if raw.get("holder_count") else None

    transfer_fee = raw.get("transfer_fee") or {}
    transfer_tax = None
    if transfer_fee:
        current = transfer_fee.get("current") if isinstance(transfer_fee.get("current"), dict) else None
        transfer_tax = _to_float((current or transfer_fee).get("percent")) if (current or transfer_fee).get("percent") is not None else None

    return {
        "verified_contract": _to_bool(raw.get("trusted_token")),
        "mint_authority": mintable_status,
        "ownership": None,  # SPL tokens have no single "owner" contract account the way EVM does
        "ownership_renounced": ownership_renounced,
        "proxy_contract": None,  # not a meaningful SPL-token concept
        "upgradeable_contract": upgradeable,
        "honeypot": _to_bool(raw.get("non_transferable")),
        "buy_tax": None,
        "sell_tax": None,
        "transfer_tax": transfer_tax,
        "blacklist": None,
        "whitelist": None,
        "trading_pause": None,
        "lp_locked": lp_locked_pct,
        "lp_burned": lp_burned_pct,
        "creator_wallet": None,
        "creator_holding_pct": None,
        "top_holder_pct": top_holder_pct,
        "holder_count": holder_count,
        "top_10_holder_pct": top_10_holder_pct,
    }


def _parse_honeypot_is(raw: Dict[str, Any]) -> Dict[str, Any]:
    contract_code = raw.get("contractCode") or {}
    honeypot_result = raw.get("honeypotResult") or {}
    simulation = raw.get("simulationResult") or {}
    return {
        "verified_contract": contract_code.get("openSource"),
        "mint_authority": None,
        "ownership": None,
        "ownership_renounced": None,
        "proxy_contract": contract_code.get("isProxy"),
        "upgradeable_contract": contract_code.get("isProxy"),
        "honeypot": honeypot_result.get("isHoneypot"),
        "buy_tax": _to_float(simulation.get("buyTax")),
        "sell_tax": _to_float(simulation.get("sellTax")),
        "transfer_tax": _to_float(simulation.get("transferTax")),
        "blacklist": None,
        "whitelist": None,
        "trading_pause": None,
        "lp_locked": None,
        "lp_burned": None,
        "creator_wallet": None,
        "creator_holding_pct": None,
        "top_holder_pct": None,
        "holder_count": None,  # Honeypot.is doesn't report holder counts/distribution.
        "top_10_holder_pct": None,
    }


def _parse_rugcheck(report: Optional[Dict[str, Any]], summary: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if report is None and summary is None:
        return {}

    report = report or {}
    token = report.get("token") or {}
    mint_authority = token.get("mintAuthority")
    freeze_authority = token.get("freezeAuthority")
    verification = report.get("verification") or {}
    token_meta = report.get("tokenMeta") or {}

    creator_holding_pct = None
    creator_balance = report.get("creatorBalance")
    supply = token.get("supply")
    if creator_balance is not None and supply:
        try:
            creator_holding_pct = round(float(creator_balance) / float(supply) * 100, 4)
        except (TypeError, ValueError, ZeroDivisionError):
            creator_holding_pct = None

    top_holder_pct = None
    top_10_holder_pct = None
    top_holders = report.get("topHolders")
    if top_holders:
        first = top_holders[0] or {}
        for key in ("pct", "percentage", "percent"):
            if first.get(key) is not None:
                top_holder_pct = _to_float(first[key])
                break
        # Sum whichever percent-style key each entry actually has - RugCheck's
        # topHolders entries all share the same schema, so the key found on
        # the first entry is reused for the rest.
        pct_key = next((k for k in ("pct", "percentage", "percent") if first.get(k) is not None), None)
        if pct_key is not None:
            top_10_holder_pct = round(
                sum(_to_float((h or {}).get(pct_key)) or 0.0 for h in top_holders[:10]), 4
            )

    transfer_tax = None
    transfer_fee = report.get("transferFee") or {}
    if transfer_fee.get("pct") is not None:
        transfer_tax = _to_float(transfer_fee.get("pct"))

    lp_locked_pct = None
    if summary and summary.get("lpLockedPct") is not None:
        lp_locked_pct = _to_float(summary.get("lpLockedPct"))

    return {
        "verified_contract": verification.get("jup_verified"),
        "mint_authority": bool(mint_authority) if mint_authority is not None else None,
        "ownership": freeze_authority or None,
        "ownership_renounced": (not bool(mint_authority)) and (not bool(freeze_authority)) if report else None,
        "proxy_contract": None,
        "upgradeable_contract": bool(token_meta.get("mutable")) if "mutable" in token_meta else None,
        "honeypot": bool(report.get("rugged")) if "rugged" in report else None,
        "buy_tax": None,
        "sell_tax": None,
        "transfer_tax": transfer_tax,
        "blacklist": None,
        "whitelist": None,
        "trading_pause": None,
        "lp_locked": lp_locked_pct,
        "lp_burned": None,  # RugCheck's lpLockedPct doesn't cleanly separate locked vs burned - left for GoPlus's lp_holders split when available
        "creator_wallet": report.get("creator") or None,
        "creator_holding_pct": creator_holding_pct,
        "top_holder_pct": top_holder_pct,
        "holder_count": None,  # RugCheck's report only lists topHolders, not a total count.
        "top_10_holder_pct": top_10_holder_pct,
    }


# --------------------------------------------------------------------------
# Provider architecture - same shape as smart_money_service.py's
# SmartMoneyProvider: readings(ctx) returns a dict of already-real values
# (or omits/None's what it can't back).
# --------------------------------------------------------------------------

class SecurityProvider:
    name: str = "Unknown Provider"

    def readings(self, ctx: SecurityContext) -> Dict[str, Any]:
        return {}


class GoPlusSecurityProvider(SecurityProvider):
    name = "GoPlus Security"

    def readings(self, ctx: SecurityContext) -> Dict[str, Any]:
        if ctx.goplus_raw is None:
            return {}
        if ctx.chain == "solana":
            return _parse_goplus_solana(ctx.goplus_raw)
        return _parse_goplus_evm(ctx.goplus_raw)


class HoneypotIsProvider(SecurityProvider):
    name = "Honeypot.is"

    def readings(self, ctx: SecurityContext) -> Dict[str, Any]:
        if ctx.honeypot_raw is None:
            return {}
        return _parse_honeypot_is(ctx.honeypot_raw)


class RugCheckProvider(SecurityProvider):
    name = "RugCheck"

    def readings(self, ctx: SecurityContext) -> Dict[str, Any]:
        if ctx.rugcheck_report is None and ctx.rugcheck_summary is None:
            return {}
        return _parse_rugcheck(ctx.rugcheck_report, ctx.rugcheck_summary)


class TokenSnifferProvider(SecurityProvider):
    """
    Honest stub: TokenSniffer's OFFICIAL programmatic API is real but
    requires a paid "Sniffer Pack Pro" subscription ($99/mo) - the free
    tokensniffer.com web UI has no public API behind it. No client is
    wired in (gated on an unset TOKENSNIFFER_API_KEY, same pattern as
    this project's ArkhamProvider/NansenProvider in smart_money_service.py),
    so every category stays empty today. If a subscription is ever
    purchased, wiring in a real client is a pure addition to this class.
    """
    name = "TokenSniffer"

    def __init__(self):
        self._configured = bool(os.environ.get("TOKENSNIFFER_API_KEY"))

    def is_configured(self) -> bool:
        return self._configured

    def readings(self, ctx: SecurityContext) -> Dict[str, Any]:
        return {}


def _unavailable_reason(key: str, chain: str) -> str:
    if chain == "solana" and key in SOLANA_NOT_APPLICABLE:
        return (
            "Not a meaningful/reported concept for Solana SPL tokens via RugCheck or "
            "GoPlus's Solana security endpoint."
        )
    return (
        "No configured provider returned data for this field for this token. GoPlus, "
        "Honeypot.is, and RugCheck (whichever apply to this chain) had nothing here, and "
        "TokenSniffer's official API requires a paid Sniffer Pack Pro subscription (no free "
        "tier) so it is not queried."
    )


def _build_category_section(key: str, readings: List[Tuple[str, Any]], chain: str) -> Dict[str, Any]:
    label = CATEGORY_LABELS[key]
    kind = CATEGORY_KIND[key]
    valid = [(src, val) for src, val in readings if val is not None]

    if not valid:
        return _section(label, "Not Available", 0, 0, _unavailable_reason(key, chain))

    primary_source, primary_value = valid[0]
    n_sources = len(valid)

    if kind in ("bool_risk", "bool_safe"):
        agree = all(bool(v) == bool(primary_value) for _, v in valid)
    elif kind in ("pct_risk", "pct_safe"):
        agree = all(abs(float(v) - float(primary_value)) < 1.0 for _, v in valid)
    elif kind == "count_neutral":
        agree = all(abs(float(v) - float(primary_value)) <= max(1.0, float(primary_value) * 0.02) for _, v in valid)
    else:
        agree = all(v == primary_value for _, v in valid)

    confidence = 45 if n_sources == 1 else (85 if agree else 55)
    detail = "; ".join(f"{src}: {val}" for src, val in valid)
    if n_sources > 1 and not agree:
        detail = "Sources differ - " + detail

    if kind in ("bool_risk", "bool_safe"):
        value = bool(primary_value)
        true_text, false_text = BOOL_LABELS.get(key, ("Yes", "No"))
        status = true_text if value else false_text
        is_risky = value if kind == "bool_risk" else (not value)
        strength = 100 if is_risky else 0
    elif kind in ("pct_risk", "pct_safe"):
        value = float(primary_value)
        status = f"{value:.2f}%"
        if kind == "pct_risk":
            mult = PCT_RISK_SCALE.get(key, 2.0)
            strength = min(100, round(value * mult))
        else:
            strength = min(100, round(value))
    elif kind == "count_neutral":
        status = str(int(round(float(primary_value))))
        strength = 0
    else:
        status = str(primary_value)
        strength = 0

    return _section(label, status, strength, confidence, detail)


def build_contract_security_dashboard(ctx: SecurityContext) -> Dict[str, Any]:
    """
    Aggregates every category into the final dashboard shape:
    {"sections": [...18 rows...], "ai_summary": {...}, "sources_used": [...]}.
    Provider priority order is chain-specific: RugCheck leads for Solana
    (it has real creator/mint-authority/freeze-authority data GoPlus's
    Solana endpoint lacks), GoPlus leads for EVM chains (broadest single
    source), with Honeypot.is/RugCheck as real corroborating providers.
    """
    if ctx.chain == "solana":
        providers: List[SecurityProvider] = [
            RugCheckProvider(), GoPlusSecurityProvider(), HoneypotIsProvider(), TokenSnifferProvider(),
        ]
    else:
        providers = [
            GoPlusSecurityProvider(), HoneypotIsProvider(), RugCheckProvider(), TokenSnifferProvider(),
        ]

    all_readings: Dict[str, List[Tuple[str, Any]]] = {key: [] for key in CATEGORY_ORDER}
    sources_used: List[str] = []

    for provider in providers:
        readings = provider.readings(ctx)
        if readings:
            sources_used.append(provider.name)
        for key, value in readings.items():
            if key in all_readings and value is not None:
                all_readings[key].append((provider.name, value))

    sections: List[Dict[str, Any]] = []
    scored: Dict[str, Dict[str, Any]] = {}
    for key in CATEGORY_ORDER:
        section = _build_category_section(key, all_readings[key], ctx.chain)
        sections.append(section)
        if section["confidence"] > 0:
            scored[key] = section

    ai_summary = _build_ai_security_summary(scored, sources_used)
    return {"sections": sections, "ai_summary": ai_summary, "sources_used": sources_used}


def _pct_from_status(section: Optional[Dict[str, Any]]) -> Optional[float]:
    if not section:
        return None
    try:
        return float(section["status"].rstrip("%"))
    except (ValueError, AttributeError):
        return None


def _build_ai_security_summary(scored: Dict[str, Dict[str, Any]], sources_used: List[str]) -> Dict[str, Any]:
    """
    Adapts the same style of transparent, explainable point-deduction
    scoring already used by app/services/token_scorer.py's `_score_safety()`
    (duplicated/adapted here, not imported, per this project's established
    isolation convention - see market_scorer.py's module docstring) - but
    scoped to exactly the real fields this dashboard has, never guessing at
    fields with no data.
    """
    if not scored:
        # No provider returned ANY real data for this token - reporting a
        # high "safe" score here would be a fabricated safety signal (the
        # absence of evidence of risk is not evidence of safety). Be
        # explicit that this is unscored, not "clean".
        return {
            "security_score": 0,
            "risk_level": "Unknown",
            "verdict": "INSUFFICIENT DATA",
            "key_concerns": ["No security provider returned usable data for this token - cannot assess risk."],
            "caveat": (
                "GoPlus, Honeypot.is, and RugCheck (whichever apply to this chain) all returned no data, and "
                "TokenSniffer's official API requires a paid Sniffer Pack Pro subscription (no free tier) so "
                "it was not queried. This score is NOT a safety signal - it means nothing could be verified."
            ),
        }

    score = 100.0
    concerns: List[str] = []

    def status_of(key: str) -> Optional[str]:
        s = scored.get(key)
        return s["status"] if s else None

    honeypot_status = status_of("honeypot")
    is_honeypot = honeypot_status == "HONEYPOT DETECTED"
    if is_honeypot:
        score -= 70
        concerns.append("Honeypot detected - the contract may block selling.")

    mint_status = status_of("mint_authority")
    renounced_status = status_of("ownership_renounced")
    if mint_status == "Present":
        if renounced_status == "Not Renounced":
            score -= 20
            concerns.append("Mint authority is present and ownership is not renounced - supply can be inflated.")
        else:
            score -= 8
            concerns.append("Mint authority is present.")

    if status_of("blacklist") == "Enabled":
        score -= 12
        concerns.append("Contract can blacklist wallets from trading.")
    if status_of("trading_pause") == "Enabled":
        score -= 10
        concerns.append("Trading can be paused by a privileged address.")
    if status_of("whitelist") == "Enabled":
        score -= 5
        concerns.append("Contract restricts trading to a whitelist.")

    for tax_key, tax_label in (("buy_tax", "Buy"), ("sell_tax", "Sell"), ("transfer_tax", "Transfer")):
        pct = _pct_from_status(scored.get(tax_key))
        if pct is not None:
            if pct >= 25:
                score -= 20
                concerns.append(f"{tax_label} tax is extreme ({pct:.1f}%).")
            elif pct >= 10:
                score -= 8
                concerns.append(f"{tax_label} tax is high ({pct:.1f}%).")

    lp_locked_pct = _pct_from_status(scored.get("lp_locked")) or 0.0
    lp_burned_pct = _pct_from_status(scored.get("lp_burned")) or 0.0
    if "lp_locked" in scored or "lp_burned" in scored:
        total_locked = lp_locked_pct + lp_burned_pct
        if total_locked < 50:
            score -= 12
            concerns.append(f"Only {total_locked:.0f}% of LP is locked/burned - liquidity can be pulled.")
        elif total_locked >= 80:
            concerns.append(f"{total_locked:.0f}% of LP is locked/burned.")

    top_holder_pct = _pct_from_status(scored.get("top_holder_pct"))
    if top_holder_pct is not None and top_holder_pct >= 30:
        score -= 10
        concerns.append(f"Single largest holder controls {top_holder_pct:.1f}% of supply.")

    if status_of("verified_contract") == "Not Verified":
        score -= 10
        concerns.append("Contract source code is not verified/open-source.")

    score = max(0, min(100, round(score)))

    if score >= 80:
        risk_level, verdict = "Low", "SAFE"
    elif score >= 60:
        risk_level, verdict = "Medium", "CAUTION"
    elif score >= 35:
        risk_level, verdict = "High", "HIGH RISK"
    else:
        risk_level, verdict = "Critical", "AVOID"

    if is_honeypot:
        risk_level, verdict = "Critical", "AVOID"

    coverage_note = (
        f"Checked via: {', '.join(sources_used)}." if sources_used
        else "No security provider returned usable data for this token."
    )
    caveat = (
        f"{coverage_note} TokenSniffer's official API requires a paid Sniffer Pack Pro subscription "
        "(no free tier), so it was not queried. Every field in this dashboard is either backed by one "
        "of these real providers or explicitly marked 'Not Available'."
    )

    return {
        "security_score": score,
        "risk_level": risk_level,
        "verdict": verdict,
        "key_concerns": concerns or ["No significant red flags detected in the categories that had real data."],
        "caveat": caveat,
    }


async def build_evm_contract_security_dashboard(chain: str, token_address: str) -> Dict[str, Any]:
    """Convenience one-shot for EVM chains: fetches GoPlus + Honeypot.is
    concurrently and builds the full dashboard. Never raises."""
    goplus_raw, honeypot_raw = await asyncio.gather(
        _fetch_goplus_raw(chain, token_address),
        _fetch_honeypot_is(chain, token_address),
    )
    ctx = SecurityContext(chain=chain, token_address=token_address, goplus_raw=goplus_raw, honeypot_raw=honeypot_raw)
    return build_contract_security_dashboard(ctx)


async def build_solana_contract_security_dashboard(token_address: str) -> Dict[str, Any]:
    """Convenience one-shot for Solana: fetches GoPlus's Solana endpoint +
    RugCheck's report/summary concurrently and builds the full dashboard.
    Never raises."""
    (goplus_raw, (rugcheck_report, rugcheck_summary)) = await asyncio.gather(
        _fetch_goplus_raw("solana", token_address),
        _fetch_rugcheck(token_address),
    )
    ctx = SecurityContext(
        chain="solana",
        token_address=token_address,
        goplus_raw=goplus_raw,
        rugcheck_report=rugcheck_report,
        rugcheck_summary=rugcheck_summary,
    )
    return build_contract_security_dashboard(ctx)


async def build_contract_security_report(chain: str, token_address: str) -> Dict[str, Any]:
    """Top-level entry point used by token_scorer.py - dispatches to the
    Solana or EVM path based on `chain`."""
    if chain == "solana":
        return await build_solana_contract_security_dashboard(token_address)
    return await build_evm_contract_security_dashboard(chain, token_address)
