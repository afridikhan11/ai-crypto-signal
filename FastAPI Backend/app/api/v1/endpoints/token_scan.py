from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_current_user
from app.schemas.common import ErrorResponse
from app.schemas.token_scan import (
    TokenScanRequest,
    TokenScanReport,
    TokenSearchResponse,
    TokenSearchResult,
)
from app.services.dexscreener_service import search_tokens
from app.services.geckoterminal_service import CHAIN_TO_GECKOTERMINAL_NETWORK
from app.services.market_scorer import MarketScanError, build_market_scan_report, detect_input_type
from app.services.token_scorer import build_token_scan_report

router = APIRouter(prefix="/token-scan", tags=["token-scan"])

SUPPORTED_CHAINS = sorted(CHAIN_TO_GECKOTERMINAL_NETWORK.keys())

MAX_SEARCH_RESULTS = 20


@router.post(
    "",
    response_model=TokenScanReport,
    summary="Scan a crypto token and return one combined report",
    description=(
        "The input is auto-detected: a contract address (EVM 0x... or a Solana mint "
        "address) runs the on-chain scan below; anything else (e.g. BTCUSDT, bnbusdt) "
        "is treated as a Binance trading pair and runs a Spot/Futures market analysis "
        "instead (app/services/market_scorer.py) - the SAME SMC engine and AI Scorer "
        "used by the live signal system, just never hard-gated, so a report is always "
        "returned. Either way the response uses this same shape. "
        "On-chain path combines DexScreener (liquidity/volume/price action - free), "
        "GeckoTerminal (real OHLCV candles run through the SAME SMC/technical-analysis "
        "engine used for Binance futures - free), GoPlus Security (mint/honeypot/"
        "blacklist/ownership/proxy/LP-lock checks - free), and the Crypto Fear & Greed "
        "Index (free) into one Safety Score / Confidence / Final Decision report. "
        "Whales, Smart Money, holder-cluster, and social-sentiment sections are "
        "reported as explicitly Not Available (Arkham/Nansen/Bubblemaps/Twitter all "
        "require a paid API this project doesn't have) rather than guessed - see "
        "`unavailable_data` in the response."
    ),
    responses={400: {"model": ErrorResponse}},
)
async def scan_token(
    payload: TokenScanRequest,
    _user: str = Depends(get_current_user),
):
    raw_input = (payload.token_address or "").strip()
    if len(raw_input) < 2:
        raise HTTPException(
            status_code=400,
            detail="Enter a token contract address, or a Binance trading pair symbol (e.g. BTCUSDT).",
        )

    input_type = detect_input_type(raw_input)

    if input_type == "trading_pair":
        try:
            report = await build_market_scan_report(raw_input)
        except MarketScanError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Market scan failed: {e}")

        return TokenScanReport(**report)

    # --- contract_address path (unchanged on-chain behavior) ---
    chain = (payload.chain or "").lower().strip()
    if chain not in SUPPORTED_CHAINS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{raw_input}' looks like a contract address - select a chain "
                f"({', '.join(SUPPORTED_CHAINS)}) and try again."
            ),
        )

    if len(raw_input) < 8:
        raise HTTPException(status_code=400, detail="token_address looks too short to be valid.")

    try:
        report = await build_token_scan_report(chain, raw_input)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Token scan failed: {e}")

    return TokenScanReport(**report)


@router.get(
    "/supported-chains",
    summary="List the chains this scanner supports",
)
async def supported_chains():
    return {"chains": SUPPORTED_CHAINS}


@router.get(
    "/search",
    response_model=TokenSearchResponse,
    summary="Search for a token by symbol or name (DexScreener free-text search)",
    description=(
        "A ticker/name alone is ambiguous - many unrelated tokens share the "
        "same symbol across chains, and even within one chain multiple "
        "unrelated contracts can use it. This returns every distinct "
        "contract DexScreener found for the query, one entry per token "
        "(its single most-liquid pair), restricted to chains this scanner "
        "supports, sorted by liquidity - most liquid first. Pick the "
        "correct result's chain + token_address and pass them to "
        "POST /token-scan for the full report."
    ),
    responses={400: {"model": ErrorResponse}},
)
async def search_token(
    query: str,
    _user: str = Depends(get_current_user),
):
    query = query.strip()
    if len(query) < 2:
        raise HTTPException(status_code=400, detail="Search query must be at least 2 characters.")

    try:
        pairs = await search_tokens(query, SUPPORTED_CHAINS)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Token search failed: {e}")

    results = []
    for pair in pairs[:MAX_SEARCH_RESULTS]:
        base_token = pair.get("baseToken") or {}
        liquidity = pair.get("liquidity") or {}
        volume = pair.get("volume") or {}
        results.append(
            TokenSearchResult(
                chain=pair.get("chainId", ""),
                token_address=base_token.get("address", ""),
                symbol=base_token.get("symbol", "?"),
                name=base_token.get("name", "Unknown"),
                dex_id=pair.get("dexId", ""),
                price_usd=float(pair["priceUsd"]) if pair.get("priceUsd") else None,
                liquidity_usd=liquidity.get("usd"),
                volume_24h_usd=volume.get("h24"),
                pair_url=pair.get("url"),
            )
        )

    return TokenSearchResponse(query=query, results=results)
