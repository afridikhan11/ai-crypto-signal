"""
Institutional-grade Smart Money Dashboard - shared by both the on-chain
Token Scanner (token_scorer.py) and the Binance market scanner
(market_scorer.py), mirroring the shape/quality of the sibling Technical
Analysis dashboard (see app/services/ta_dashboard.py, whose `_section()`
row helper this module reuses directly rather than duplicating it).

--------------------------------------------------------------------------
WHAT'S REAL VS UNAVAILABLE IN THIS DASHBOARD
--------------------------------------------------------------------------
Real, live data (built from actual individual trade prints, not a
placeholder or a random number):
  - On-chain path: GeckoTerminal's `/pools/{pool}/trades` endpoint (free,
    no API key - see geckoterminal_service.get_pool_trades) - up to the
    most recent 300 real trades in the last 24h, each with a real
    buy/sell side and USD size.
  - Market path: Binance Futures' `/fapi/v1/aggTrades` endpoint (free, no
    API key) - the most recent real aggregated trade prints for the pair,
    each with a real price/quantity/aggressor side.

From those real trade prints, four categories are computed here with
transparent, explainable arithmetic (no black-box ML layer):
  - Whale Activity     - trades at/above WHALE_TRADE_USD
  - Accumulation / Distribution - net buy vs sell volume bias
  - Net Flow           - buy volume minus sell volume
  - Large Transactions - trades at/above LARGE_TX_USD

Structurally UNAVAILABLE without a paid subscription (reported explicitly
as "Not Available" with a specific reason, never guessed, per this
project's "never hallucinate" rule):
  - Exchange Inflows / Exchange Outflows - require wallet-to-exchange flow
    labeling (Arkham Intelligence - no free API tier - or a paid
    CryptoQuant/Glassnode-style subscription).
  - Smart Money Wallets / Fresh Wallets / VC & Fund Wallets / Wallet
    Profitability / Wallet Win Rate - all require a labeled wallet
    database with historical PnL/age tracking (Nansen, paid subscription
    for API access; Arkham for some wallet labels, also paid-only).

"Smart Money Confidence" is an honest META score over the four real
trade-flow categories above (data coverage + directional agreement) - it
is explicitly labeled as an ORDER-FLOW PROXY, never presented as a
wallet-identity-based "smart money" score, since that data doesn't exist
on any free tier.
--------------------------------------------------------------------------
PROVIDER ARCHITECTURE
--------------------------------------------------------------------------
`SmartMoneyProvider` is the base class: every one of the 11 categories
defaults to `None` ("this provider has nothing for this category").
Concrete providers override only what they can genuinely back with real
data:
  - `OnChainTradeFlowProvider` / `BinanceTradeFlowProvider` - real trade
    prints, override the 4 trade-flow categories.
  - `ArkhamProvider` / `NansenProvider` - honest stubs today (no client
    wired in, gated on ARKHAM_API_KEY / NANSEN_API_KEY which aren't set
    anywhere in this project) covering the 7 wallet-identity categories.
    If a real Arkham/Nansen client is ever wired in later, that's a pure
    addition to these two classes - `build_smart_money_dashboard()`'s
    aggregation loop below does not need to change at all.
--------------------------------------------------------------------------
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from app.services.binance_service import BinanceDataManager
from app.services.geckoterminal_service import fetch_pool_trades
from app.services.ta_dashboard import _section

# A single trade at/above this USD size counts toward "Whale Activity".
WHALE_TRADE_USD = 50_000.0
# A single trade at/above this (lower) USD size counts toward "Large
# Transactions" - deliberately a different, smaller bar than whale-sized.
LARGE_TX_USD = 10_000.0
# Binance's aggTrades endpoint allows up to 1000 per call with no time
# filter - this is a "most recent N trades" snapshot, not a fixed time
# window (a highly liquid pair's 1000 trades might span minutes; an
# illiquid one, hours) - disclosed via SmartMoneyContext.window_desc.
BINANCE_AGGTRADES_LIMIT = 1000

CATEGORY_LABELS: Dict[str, str] = {
    "whale_activity": "Whale Activity",
    "exchange_inflows": "Exchange Inflows",
    "exchange_outflows": "Exchange Outflows",
    "smart_money_wallets": "Smart Money Wallets",
    "fresh_wallets": "Fresh Wallets",
    "vc_fund_wallets": "VC / Fund Wallets",
    "wallet_profitability": "Wallet Profitability",
    "wallet_win_rate": "Wallet Win Rate",
    "accumulation_distribution": "Accumulation / Distribution",
    "net_flow": "Net Flow",
    "large_transactions": "Large Transactions",
}

# Order matches the user-facing spec exactly (Whale Activity first,
# Large Transactions last, "Smart Money Confidence" is appended
# separately at the end since it's a meta-score over the other 11).
CATEGORY_ORDER: List[str] = [
    "whale_activity",
    "exchange_inflows",
    "exchange_outflows",
    "smart_money_wallets",
    "fresh_wallets",
    "vc_fund_wallets",
    "wallet_profitability",
    "wallet_win_rate",
    "accumulation_distribution",
    "net_flow",
    "large_transactions",
]

TRADE_FLOW_CATEGORIES = frozenset({
    "whale_activity", "accumulation_distribution", "net_flow", "large_transactions",
})
IDENTITY_CATEGORIES = frozenset(CATEGORY_ORDER) - TRADE_FLOW_CATEGORIES

# Reported verbatim when a wallet-identity category has no configured
# provider - specific about WHICH paid service the data actually lives in,
# never a generic "unavailable".
UNAVAILABLE_REASONS: Dict[str, str] = {
    "exchange_inflows": (
        "Requires wallet-to-exchange flow labeling - Arkham Intelligence has no free API "
        "tier, and no paid CryptoQuant/Glassnode-style subscription is configured."
    ),
    "exchange_outflows": (
        "Requires wallet-to-exchange flow labeling - Arkham Intelligence has no free API "
        "tier, and no paid CryptoQuant/Glassnode-style subscription is configured."
    ),
    "smart_money_wallets": "Requires Nansen's labeled 'Smart Money' wallet database (paid subscription for API access).",
    "fresh_wallets": (
        "Requires wallet-age/history lookups across a labeled wallet database "
        "(Nansen/Arkham, paid) - not derivable from trade prints alone."
    ),
    "vc_fund_wallets": "Requires Nansen's labeled VC/Fund wallet database (paid subscription for API access).",
    "wallet_profitability": "Requires historical per-wallet PnL tracking (Nansen, paid subscription for API access).",
    "wallet_win_rate": "Requires historical per-wallet trade-outcome tracking (Nansen, paid subscription for API access).",
}


@dataclass
class NormalizedTrade:
    """One real individual trade, normalized to the same shape regardless
    of whether it came from GeckoTerminal (on-chain) or Binance (futures)."""
    side: str  # "buy" | "sell"
    usd_value: float
    timestamp: Optional[Any] = None


@dataclass
class SmartMoneyContext:
    """Everything the dashboard needs, already fetched - keeps every
    provider method a plain, synchronous, easily-testable function over
    data that's already in memory rather than repeating I/O."""
    source_label: str
    identifier: str
    window_desc: str
    trades: List[NormalizedTrade] = field(default_factory=list)


class SmartMoneyProvider:
    """
    Base provider - every category defaults to `None` ("not backed by
    this provider"). A concrete provider overrides ONLY the categories it
    can genuinely fill with real data; everything else falls through to
    the next candidate provider, and finally to an explicit
    "Not Available" row if no provider has it.
    """
    name: str = "Unknown Provider"

    def whale_activity(self, ctx: SmartMoneyContext) -> Optional[Dict[str, Any]]:
        return None

    def exchange_inflows(self, ctx: SmartMoneyContext) -> Optional[Dict[str, Any]]:
        return None

    def exchange_outflows(self, ctx: SmartMoneyContext) -> Optional[Dict[str, Any]]:
        return None

    def smart_money_wallets(self, ctx: SmartMoneyContext) -> Optional[Dict[str, Any]]:
        return None

    def fresh_wallets(self, ctx: SmartMoneyContext) -> Optional[Dict[str, Any]]:
        return None

    def vc_fund_wallets(self, ctx: SmartMoneyContext) -> Optional[Dict[str, Any]]:
        return None

    def wallet_profitability(self, ctx: SmartMoneyContext) -> Optional[Dict[str, Any]]:
        return None

    def wallet_win_rate(self, ctx: SmartMoneyContext) -> Optional[Dict[str, Any]]:
        return None

    def accumulation_distribution(self, ctx: SmartMoneyContext) -> Optional[Dict[str, Any]]:
        return None

    def net_flow(self, ctx: SmartMoneyContext) -> Optional[Dict[str, Any]]:
        return None

    def large_transactions(self, ctx: SmartMoneyContext) -> Optional[Dict[str, Any]]:
        return None


def _sample_confidence(trade_count: int) -> int:
    """How much weight to put on a trade-flow read, based purely on how
    many real trades were actually available - NOT on what the trades
    say. A handful of trades can still show a real (if noisy) signal, so
    this never drops to 0 unless there is truly nothing to read."""
    if trade_count >= 100:
        return 80
    if trade_count >= 50:
        return 65
    if trade_count >= 20:
        return 45
    if trade_count >= 5:
        return 25
    return 10 if trade_count > 0 else 0


def _compute_stats(trades: List[NormalizedTrade]) -> Dict[str, Any]:
    buy_usd = sum(t.usd_value for t in trades if t.side == "buy")
    sell_usd = sum(t.usd_value for t in trades if t.side == "sell")
    total_usd = buy_usd + sell_usd
    net_usd = buy_usd - sell_usd
    ratio = (net_usd / total_usd) if total_usd > 0 else 0.0
    whale_trades = [t for t in trades if t.usd_value >= WHALE_TRADE_USD]
    large_trades = [t for t in trades if t.usd_value >= LARGE_TX_USD]
    largest = max(trades, key=lambda t: t.usd_value) if trades else None
    return {
        "trade_count": len(trades),
        "buy_usd": buy_usd,
        "sell_usd": sell_usd,
        "total_usd": total_usd,
        "net_usd": net_usd,
        "ratio": ratio,
        "whale_trades": whale_trades,
        "large_trades": large_trades,
        "largest": largest,
    }


class TradeFlowProvider(SmartMoneyProvider):
    """
    Real order-flow provider backed by actual individual trade prints -
    this is explicitly NOT wallet-labeled "smart money" (that identity
    layer requires Arkham/Nansen, both paid-only). The scoring logic is
    identical regardless of source; only `name`/context differ between
    the two subclasses below, so nothing is duplicated between the
    on-chain and market scan paths.
    """
    name = "Trade Flow (Order Flow Proxy)"

    def whale_activity(self, ctx: SmartMoneyContext) -> Optional[Dict[str, Any]]:
        if not ctx.trades:
            return None
        stats = _compute_stats(ctx.trades)
        confidence = _sample_confidence(stats["trade_count"])
        if confidence == 0:
            return None
        whales = stats["whale_trades"]
        whale_buy = sum(t.usd_value for t in whales if t.side == "buy")
        whale_sell = sum(t.usd_value for t in whales if t.side == "sell")
        if not whales:
            return _section(
                "Whale Activity", "No Whale-Sized Trades Detected", 0, confidence,
                f"No single trade >= ${WHALE_TRADE_USD:,.0f} in the {ctx.window_desc} ({ctx.source_label}).",
            )
        dominant = (
            "Buy-Dominant" if whale_buy > whale_sell
            else "Sell-Dominant" if whale_sell > whale_buy
            else "Balanced"
        )
        strength = round((max(whale_buy, whale_sell) / stats["total_usd"]) * 100) if stats["total_usd"] else 0
        return _section(
            "Whale Activity", f"{len(whales)} Whale-Sized Trade(s) - {dominant}", strength, confidence,
            f"${whale_buy:,.0f} buy / ${whale_sell:,.0f} sell in trades >= ${WHALE_TRADE_USD:,.0f} "
            f"({ctx.window_desc}, {ctx.source_label}).",
        )

    def accumulation_distribution(self, ctx: SmartMoneyContext) -> Optional[Dict[str, Any]]:
        if not ctx.trades:
            return None
        stats = _compute_stats(ctx.trades)
        confidence = _sample_confidence(stats["trade_count"])
        if confidence == 0 or stats["total_usd"] <= 0:
            return None
        ratio = stats["ratio"]
        if ratio > 0.15:
            status = "Accumulation (Net Buying Pressure)"
        elif ratio < -0.15:
            status = "Distribution (Net Selling Pressure)"
        else:
            status = "Balanced / No Clear Bias"
        strength = round(abs(ratio) * 200)
        return _section(
            "Accumulation / Distribution", status, strength, confidence,
            f"Buy volume ${stats['buy_usd']:,.0f} vs sell volume ${stats['sell_usd']:,.0f} "
            f"({ctx.window_desc}, {ctx.source_label}).",
        )

    def net_flow(self, ctx: SmartMoneyContext) -> Optional[Dict[str, Any]]:
        if not ctx.trades:
            return None
        stats = _compute_stats(ctx.trades)
        confidence = _sample_confidence(stats["trade_count"])
        if confidence == 0 or stats["total_usd"] <= 0:
            return None
        net = stats["net_usd"]
        if net > 0:
            status = f"+${net:,.0f} Net Buy"
        elif net < 0:
            status = f"-${abs(net):,.0f} Net Sell"
        else:
            status = "Flat"
        strength = round(abs(stats["ratio"]) * 150)
        return _section(
            "Net Flow", status, strength, confidence,
            f"Net = buy volume (${stats['buy_usd']:,.0f}) - sell volume (${stats['sell_usd']:,.0f}) "
            f"over the {ctx.window_desc} ({ctx.source_label}).",
        )

    def large_transactions(self, ctx: SmartMoneyContext) -> Optional[Dict[str, Any]]:
        if not ctx.trades:
            return None
        stats = _compute_stats(ctx.trades)
        confidence = _sample_confidence(stats["trade_count"])
        if confidence == 0:
            return None
        large = stats["large_trades"]
        buy_count = sum(1 for t in large if t.side == "buy")
        sell_count = len(large) - buy_count
        largest = stats["largest"]
        strength = round(min(1.0, len(large) / max(stats["trade_count"], 1) * 3) * 100)
        detail = f"{buy_count} buy / {sell_count} sell trade(s) >= ${LARGE_TX_USD:,.0f}"
        if largest:
            detail += f"; largest single trade ${largest.usd_value:,.0f} ({largest.side})."
        return _section("Large Transactions", f"{len(large)} Large Trade(s) Detected", strength, confidence, detail)


class OnChainTradeFlowProvider(TradeFlowProvider):
    """Trade-flow provider for the on-chain Token Scanner path - `ctx.trades`
    is expected to have been built from GeckoTerminal's real pool trades."""
    name = "GeckoTerminal On-Chain Trades"


class BinanceTradeFlowProvider(TradeFlowProvider):
    """Trade-flow provider for the Binance market-pair scan path -
    `ctx.trades` is expected to have been built from real Binance
    aggregated trade prints (`/fapi/v1/aggTrades`)."""
    name = "Binance Aggregated Trades"


class ArkhamProvider(SmartMoneyProvider):
    """
    Honest stub for Arkham Intelligence's wallet-labeling data (exchange
    flows, entity-labeled wallets). No real Arkham client exists in this
    codebase and Arkham has no free API tier, so every category stays
    `None` (falls through to "Not Available") regardless of
    `is_configured()` today - the env-var gate exists so that wiring in a
    real client later is a pure addition to this class's methods, not a
    redesign of the aggregator.
    """
    name = "Arkham Intelligence"

    def __init__(self):
        self._configured = bool(os.environ.get("ARKHAM_API_KEY"))

    def is_configured(self) -> bool:
        return self._configured


class NansenProvider(SmartMoneyProvider):
    """Honest stub for Nansen's labeled Smart Money / VC-Fund wallet
    database and wallet profitability/win-rate tracking - same rationale
    as ArkhamProvider. Nansen requires a paid subscription for API access;
    no client is wired in, so every category stays `None` today."""
    name = "Nansen"

    def __init__(self):
        self._configured = bool(os.environ.get("NANSEN_API_KEY"))

    def is_configured(self) -> bool:
        return self._configured


def _direction(section: Optional[Dict[str, Any]]) -> Optional[str]:
    if not section:
        return None
    status = section["status"]
    if "Buy" in status or "Accumulation" in status:
        return "up"
    if "Sell" in status or "Distribution" in status:
        return "down"
    return "flat"


def _smart_money_confidence_section(real_results: Dict[str, Dict[str, Any]], total_categories: int) -> Dict[str, Any]:
    """
    Meta-score over the four real trade-flow categories: how much real
    data is behind this dashboard (coverage) and whether the signals that
    ARE available agree with each other (directional agreement). This is
    explicitly an ORDER-FLOW PROXY confidence, never a wallet-identity
    "smart money" confidence - that data doesn't exist without a paid
    Arkham/Nansen subscription.
    """
    coverage = len(real_results) / total_categories if total_categories else 0.0

    dirs = [
        d for d in (
            _direction(real_results.get("whale_activity")),
            _direction(real_results.get("net_flow")),
            _direction(real_results.get("accumulation_distribution")),
        )
        if d
    ]
    directional = [d for d in dirs if d != "flat"]
    if len(directional) >= 2 and len(set(directional)) == 1:
        agreement_score, agreement_label = 100, "the available signals agree with each other"
    elif not dirs:
        agreement_score, agreement_label = 0, "there aren't enough available signals to compare"
    elif len(set(directional)) > 1:
        agreement_score, agreement_label = 20, "the available signals conflict with each other"
    else:
        agreement_score, agreement_label = 55, "the available signals only partially agree"

    score = round(coverage * 50 + agreement_score * 0.5)
    if score >= 70:
        status = "High Confidence (Order-Flow Proxy)"
    elif score >= 40:
        status = "Medium Confidence (Order-Flow Proxy)"
    else:
        status = "Low Confidence (Order-Flow Proxy)"

    return _section(
        "Smart Money Confidence", status, score, score,
        f"{len(real_results)}/{total_categories} trade-derived categories have real data and {agreement_label} - "
        "this reflects confidence in the available ORDER-FLOW proxy only, not wallet-labeled smart money "
        "(Arkham/Nansen wallet-identity data is not available on any free tier).",
    )


def _build_ai_summary(
    ctx: SmartMoneyContext, real_results: Dict[str, Dict[str, Any]], confidence_section: Dict[str, Any]
) -> Dict[str, Any]:
    accum = real_results.get("accumulation_distribution")
    if accum and "Accumulation" in accum["status"]:
        bias = "Accumulation"
    elif accum and "Distribution" in accum["status"]:
        bias = "Distribution"
    else:
        bias = "Neutral"

    whale = real_results.get("whale_activity")
    if whale and whale["strength"] >= 60:
        activity_level = "High"
    elif whale and whale["strength"] >= 25:
        activity_level = "Moderate"
    else:
        activity_level = "Low"

    score = confidence_section["strength"]
    if score >= 70 and bias != "Neutral":
        verdict = f"{bias.upper()} BIAS (ORDER-FLOW PROXY)"
    elif real_results:
        verdict = "MIXED / INCONCLUSIVE"
    else:
        verdict = "INSUFFICIENT DATA"

    return {
        "smart_money_score": score,
        "activity_level": activity_level,
        "net_flow_bias": bias,
        "data_coverage_pct": round(len(real_results) / len(CATEGORY_ORDER) * 100),
        "verdict": verdict,
        "caveat": (
            f"Based only on real trade-size order flow from {ctx.source_label} ({ctx.window_desc}). "
            "No wallet-labeled 'smart money', exchange-flow, or wallet-profitability data is included - "
            "Arkham Intelligence and Nansen both require a paid subscription with no free API tier for "
            f"that data. {len(real_results)} of {len(CATEGORY_ORDER)} trade-derived categories have real "
            "data; every wallet-identity category is explicitly marked 'Not Available' above."
        ),
    }


def build_smart_money_dashboard(ctx: SmartMoneyContext, trade_flow_provider: SmartMoneyProvider) -> Dict[str, Any]:
    """
    Aggregates every category into the final dashboard shape:
    {"sections": [...12 rows...], "ai_summary": {...}}. `trade_flow_provider`
    is whichever real source backs this scan (`OnChainTradeFlowProvider` or
    `BinanceTradeFlowProvider`); the 7 wallet-identity categories always try
    `ArkhamProvider` then `NansenProvider`, falling through to an explicit
    "Not Available" row (with a specific, honest reason) when neither has
    real data - which is always, today, since no paid client is wired in.
    """
    identity_provider_candidates: List[SmartMoneyProvider] = [ArkhamProvider(), NansenProvider()]

    sections: List[Dict[str, Any]] = []
    real_results: Dict[str, Dict[str, Any]] = {}

    for key in CATEGORY_ORDER:
        label = CATEGORY_LABELS[key]
        result: Optional[Dict[str, Any]] = None

        if key in TRADE_FLOW_CATEGORIES:
            method = getattr(trade_flow_provider, key)
            result = method(ctx)
            if result is None:
                reason = (
                    f"No usable trade data returned by {ctx.source_label} - cannot compute "
                    f"{label.lower()} without at least a few recent trades."
                )
                result = _section(label, "Not Available", 0, 0, reason)
        else:
            for provider in identity_provider_candidates:
                method = getattr(provider, key)
                candidate = method(ctx)
                if candidate is not None:
                    result = candidate
                    break
            if result is None:
                reason = UNAVAILABLE_REASONS.get(key, "Not available from any configured free data source.")
                result = _section(label, "Not Available", 0, 0, reason)

        if result["confidence"] > 0:
            real_results[key] = result
        sections.append(result)

    confidence_section = _smart_money_confidence_section(real_results, len(CATEGORY_ORDER))
    sections.append(confidence_section)

    ai_summary = _build_ai_summary(ctx, real_results, confidence_section)
    return {"sections": sections, "ai_summary": ai_summary}


def _parse_geckoterminal_trades(raw: List[Dict[str, Any]]) -> List[NormalizedTrade]:
    """`raw` is the list of trade `attributes` dicts already returned by
    geckoterminal_service.fetch_pool_trades() - each real entry has at
    least `kind` ("buy"/"sell") and `volume_in_usd`. Anything missing
    either field is skipped rather than guessed."""
    trades: List[NormalizedTrade] = []
    for attrs in raw:
        kind = attrs.get("kind")
        vol = attrs.get("volume_in_usd")
        if kind not in ("buy", "sell") or vol is None:
            continue
        try:
            usd_value = float(vol)
        except (TypeError, ValueError):
            continue
        trades.append(NormalizedTrade(side=kind, usd_value=usd_value, timestamp=attrs.get("block_timestamp")))
    return trades


async def _fetch_binance_agg_trades(symbol: str, limit: int = BINANCE_AGGTRADES_LIMIT) -> List[Dict[str, Any]]:
    """Real, free, no-API-key individual trade prints for a Binance USD-M
    Futures pair. Returns [] (never raises) on failure - same
    "Not Available, not a crash" pattern used everywhere else in this
    project."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{BinanceDataManager.REST_URL}/fapi/v1/aggTrades",
                params={"symbol": symbol, "limit": limit},
            )
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"[smart-money] Binance aggTrades fetch failed for {symbol}: {e}")
        return []


def _parse_binance_agg_trades(raw: List[Dict[str, Any]]) -> List[NormalizedTrade]:
    """
    Binance aggTrades fields: `p` (price), `q` (quantity), `m`
    (isBuyerMaker), `T` (trade time, ms). `m=True` means the BUYER was the
    resting maker order, i.e. the SELLER was the aggressor that hit the
    bid - so this trade counts as seller-initiated ("sell") pressure;
    `m=False` means the buyer was the aggressor ("buy") - the same
    maker/taker convention already used by this project's own
    taker-buy-volume/CVD indicators (see app/indicators/confirmation.py).
    """
    trades: List[NormalizedTrade] = []
    for t in raw:
        try:
            price = float(t["p"])
            qty = float(t["q"])
        except (KeyError, TypeError, ValueError):
            continue
        side = "sell" if t.get("m") else "buy"
        trades.append(NormalizedTrade(side=side, usd_value=price * qty, timestamp=t.get("T")))
    return trades


async def build_onchain_smart_money_dashboard(network: str, pool_address: str) -> Dict[str, Any]:
    """
    Convenience one-shot for the Token Scanner (token_scorer.py): fetch
    this pool's real recent trades from GeckoTerminal, normalize them, and
    build the full Smart Money dashboard. Never raises - if the fetch
    fails or returns no trades, every trade-derived category simply comes
    back "Not Available" via the normal aggregator path.
    """
    raw = await fetch_pool_trades(network, pool_address)
    trades = _parse_geckoterminal_trades(raw)
    ctx = SmartMoneyContext(
        source_label="GeckoTerminal on-chain trade prints",
        identifier=pool_address,
        window_desc="last 24h (up to 300 most recent trades, GeckoTerminal's own cap)",
        trades=trades,
    )
    return build_smart_money_dashboard(ctx, OnChainTradeFlowProvider())


async def build_market_smart_money_dashboard(symbol: str) -> Dict[str, Any]:
    """
    Convenience one-shot for the Binance market scanner (market_scorer.py):
    fetch this pair's real recent aggregated trades from Binance, normalize
    them, and build the full Smart Money dashboard. Never raises - same
    fail-soft behaviour as build_onchain_smart_money_dashboard.
    """
    raw = await _fetch_binance_agg_trades(symbol)
    trades = _parse_binance_agg_trades(raw)
    ctx = SmartMoneyContext(
        source_label="Binance aggregated trade prints",
        identifier=symbol,
        window_desc=f"most recent {BINANCE_AGGTRADES_LIMIT} trades (not a fixed time window)",
        trades=trades,
    )
    return build_smart_money_dashboard(ctx, BinanceTradeFlowProvider())
