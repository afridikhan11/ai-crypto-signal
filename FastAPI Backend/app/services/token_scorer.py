"""
Token Scanner - combines every REAL, free data source into one report.

--------------------------------------------------------------------------
WHAT'S REAL VS UNAVAILABLE IN THIS REPORT
--------------------------------------------------------------------------
Real, live data (each has its own service module):
  - DexScreener (dexscreener_service.py): liquidity, volume, price action,
    pair age, momentum. Free public API.
  - GeckoTerminal (geckoterminal_service.py): historical OHLCV candles,
    run through the EXACT SAME SMC/technical-analysis engine already used
    for Binance futures (app/smc/*, app/indicators/confirmation.py) - real
    BOS/CHoCH, order blocks, FVGs, liquidity sweeps, EMA/RSI/MACD/ADX/
    VWAP/Supertrend, not a separate/simplified reimplementation.
  - GoPlus Security (token_security_service.py): mint function, honeypot,
    blacklist/whitelist, ownership, proxy, LP lock, holder concentration.
    Free public API, a real security-scanning service.
  - Crypto Fear & Greed Index (reuses FundamentalsService, already used
    elsewhere in this project): free, no key.

Structurally UNAVAILABLE without a paid subscription (see every field
below where this is reported explicitly instead of guessed):
  - Arkham Intelligence (smart money wallets, whale activity, exchange
    flows, insider wallets) - no free API tier exists.
  - Nansen (smart money buys, VC/fund wallets, institutional activity) -
    paid subscription required for API access.
  - Bubblemaps (holder cluster visualization, connected wallets) - no
    public API at all, browser-only tool.
  - Twitter/X, Telegram, Reddit, news sentiment - Twitter's API is paid;
    Telegram/Reddit/news aggregation at any reliable quality needs either
    a paid data provider or scraping that violates most platforms' ToS.
    Only the Fear & Greed Index (a real, free, aggregate market-sentiment
    number) is used here as a market-wide sentiment proxy.

Per the product's own rule ("never hallucinate, if data is unavailable
clearly state it"), every one of the above is reported as an explicit
"Not Available" with a reason in TokenScanReport.unavailable_data, never
filled with a made-up value.
--------------------------------------------------------------------------
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
from loguru import logger

from app.legacy.confirmation import ConfirmationIndicators
from app.services.dexscreener_service import fetch_dexscreener_summary
from app.services.fundamentals_service import FundamentalsService
from app.services.geckoterminal_service import CHAIN_TO_GECKOTERMINAL_NETWORK, fetch_token_candles
from app.services.contract_security_service import build_contract_security_report
from app.services.smart_money_service import build_onchain_smart_money_dashboard
from app.services.ta_dashboard import build_ta_dashboard
from app.services.token_security_service import fetch_token_security_summary
from app.smc.fvg import FVGDetector
from app.smc.liquidity_engine import LiquidityEngine
from app.smc.market_structure_engine import MarketStructureEngine
from app.smc.order_block_engine import OrderBlockEngine

# Extra timeframes fetched ONLY for the Technical Analysis dashboard's
# Multi-Timeframe Analysis section - (GeckoTerminal timeframe, aggregate)
# pairs per their docs (hour: 1/4/12, minute: 1/5/15, day: 1). The primary
# "1h" series used for every other section is fetched separately above and
# reused as the "1h" entry here rather than re-fetched.
_MTF_TIMEFRAME_PARAMS = {
    "5m": ("minute", 5),
    "15m": ("minute", 15),
    "4h": ("hour", 4),
    "1D": ("day", 1),
}

WARMUP_CANDLES = 60  # far shorter than the 500 used for Binance futures - DEX
# tokens rarely have deep, clean history, and this is a point-in-time scan,
# not a continuously-recalibrated live scanner.


async def build_token_scan_report(chain: str, token_address: str) -> Dict[str, Any]:
    chain = chain.lower().strip()
    token_address = token_address.strip()
    unavailable: List[Dict[str, str]] = []

    # --- Fetch every real data source concurrently ------------------------
    fundamentals = FundamentalsService()
    try:
        dex_summary, security_summary, fear_greed = await asyncio.gather(
            fetch_dexscreener_summary(chain, token_address),
            fetch_token_security_summary(chain, token_address),
            fundamentals.get_fear_greed_index(),
        )
    finally:
        await fundamentals.close()

    dex_available = "error" not in dex_summary
    security_available = "error" not in security_summary

    # --- Technical analysis: needs a pool address from DexScreener to ----
    # pull GeckoTerminal candles. If DexScreener found nothing, there's no
    # pool address to fetch candles for either.
    ta_result: Dict[str, Any] = {"available": False, "error": None}
    candles_df = pd.DataFrame()
    if dex_available and dex_summary.get("pair_address"):
        network = CHAIN_TO_GECKOTERMINAL_NETWORK.get(chain)
        if network is None:
            ta_result["error"] = f"Chain '{chain}' isn't mapped to a GeckoTerminal network."
        else:
            candles_df = await fetch_token_candles(
                network, dex_summary["pair_address"], timeframe="hour", aggregate=1, limit=200
            )
            if len(candles_df) < WARMUP_CANDLES:
                ta_result["error"] = (
                    f"Only {len(candles_df)} hourly candles available (need >= {WARMUP_CANDLES}) - "
                    "token/pair is too new for reliable structure analysis yet."
                )
    else:
        ta_result["error"] = "No DexScreener pair found, so there's no pool to pull candles from."

    if ta_result["error"]:
        unavailable.append({
            "field": "technical_analysis",
            "reason": ta_result["error"],
        })

    smc_features: Optional[Dict[str, Any]] = None
    if not ta_result["error"]:
        smc_features = _run_technical_analysis(candles_df)
        ta_result.update(smc_features["ta_section"])
        ta_result["available"] = True

    # --- Whales / Smart Money / Insiders - honestly unavailable ----------
    unavailable.append({
        "field": "whales",
        "reason": "Requires Arkham Intelligence (no free API tier).",
    })
    unavailable.append({
        "field": "smart_money",
        "reason": "Requires Nansen (paid subscription for API access).",
    })
    unavailable.append({
        "field": "holder_clusters",
        "reason": "Requires Bubblemaps (no public API).",
    })
    unavailable.append({
        "field": "social_sentiment",
        "reason": (
            "Twitter/Telegram/Reddit/news sentiment require a paid data provider - "
            "only the free Crypto Fear & Greed Index (market-wide, not token-specific) is used."
        ),
    })

    # --- Combine into the final report ------------------------------------
    safety_score, contract_risk, manipulation_risk, holder_distribution, security_reasons = _score_safety(
        security_summary, security_available
    )
    liquidity_label, liquidity_reasons = _score_liquidity(dex_summary, dex_available)

    confidence_pct = 0
    trend = "Unknown"
    entry = stop_loss = tp1 = tp2 = tp3 = None
    risk_reward = None
    ta_reasons: List[str] = []

    if smc_features is not None:
        confidence_pct, trend, ta_reasons = _score_confidence(smc_features)
        entry, stop_loss, tp1, tp2, tp3, risk_reward = _compute_levels(smc_features, trend)

        # Institutional-grade Technical Analysis dashboard (BOS/CHoCH,
        # order blocks, breaker/mitigation blocks, supply/demand,
        # premium/discount, FVG/inverse-FVG, volatility/volume, and
        # multi-timeframe) - see app/services/ta_dashboard.py. Reuses the
        # SAME entry/stop_loss/take_profit already computed above for its
        # AI Technical Summary so the report never shows two conflicting
        # sets of trade levels.
        network = CHAIN_TO_GECKOTERMINAL_NETWORK.get(chain)
        pair_address = dex_summary.get("pair_address") if dex_available else None
        mtf_dfs = (
            await _fetch_onchain_mtf_dfs(network, pair_address, candles_df)
            if network and pair_address else {"1h": candles_df}
        )
        dashboard = build_ta_dashboard(
            candles_df,
            smc_features["current_price"],
            mtf_dfs=mtf_dfs,
            latest=smc_features["latest"],
            trade_levels={"entry": entry, "stop_loss": stop_loss, "take_profit": tp1, "risk_reward": risk_reward},
        )
        ta_result["dashboard"] = dashboard["sections"]
        ta_result["ai_summary"] = dashboard["ai_summary"]

    # --- Smart Money dashboard (institutional-grade whale/order-flow -----
    # analysis, see app/services/smart_money_service.py). Only needs a real
    # pool address to fetch trade prints from - independent of whether the
    # candle-based Technical Analysis dashboard above succeeded.
    smart_money_network = CHAIN_TO_GECKOTERMINAL_NETWORK.get(chain)
    smart_money_pair_address = dex_summary.get("pair_address") if dex_available else None
    if smart_money_network and smart_money_pair_address:
        sm_dashboard = await build_onchain_smart_money_dashboard(smart_money_network, smart_money_pair_address)
        smart_money_result: Dict[str, Any] = {
            "available": True,
            "error": None,
            "source": "GeckoTerminal on-chain trade prints",
            "window_description": "last 24h (up to 300 most recent trades, GeckoTerminal's own cap)",
            "dashboard": sm_dashboard["sections"],
            "ai_summary": sm_dashboard["ai_summary"],
        }
    else:
        smart_money_result = {
            "available": False,
            "error": "No DexScreener pool address found for this token, so there's no pool to pull trade prints from.",
            "source": None,
            "window_description": None,
            "dashboard": [],
            "ai_summary": None,
        }

    # --- Contract Security dashboard (institutional-grade, real-provider- --
    # only checks via GoPlus/Honeypot.is/RugCheck, see
    # app/services/contract_security_service.py). Independent of every
    # other section above - only needs the chain + token address.
    cs_dashboard = await build_contract_security_report(chain, token_address)
    contract_security_result: Dict[str, Any] = {
        "available": True,
        "error": None,
        "sources_used": cs_dashboard["sources_used"],
        "dashboard": cs_dashboard["sections"],
        "ai_summary": cs_dashboard["ai_summary"],
    }

    reasons = security_reasons + liquidity_reasons + ta_reasons

    final_decision = _final_decision(
        safety_score=safety_score,
        confidence_pct=confidence_pct,
        trend=trend,
        honeypot=security_summary.get("honeypot") if security_available else None,
        cannot_sell_all=security_summary.get("cannot_sell_all") if security_available else None,
    )

    report = {
        "chain": chain,
        "token_address": token_address,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "safety_score": safety_score,
        "confidence_pct": confidence_pct,
        "trend": trend,
        "whales": "Not Available (requires Arkham Intelligence)",
        "smart_money": "Not Available (requires Nansen)",
        "liquidity": liquidity_label,
        "holder_distribution": holder_distribution,
        "contract_risk": contract_risk,
        "manipulation_risk": manipulation_risk,
        "best_entry": entry,
        "stop_loss": stop_loss,
        "take_profit_1": tp1,
        "take_profit_2": tp2,
        "take_profit_3": tp3,
        "risk_reward": risk_reward,
        "final_decision": final_decision,
        "reasons": reasons,
        "dexscreener": {"available": dex_available, **dex_summary} if dex_available else {
            "available": False, "error": dex_summary.get("error")
        },
        "token_security": {"available": security_available, **security_summary} if security_available else {
            "available": False, "error": security_summary.get("error")
        },
        "technical_analysis": ta_result,
        "smart_money_analysis": smart_money_result,
        "contract_security_analysis": contract_security_result,
        "sentiment": {
            "available": True,
            "fear_greed_value": fear_greed.get("value"),
            "fear_greed_classification": fear_greed.get("classification"),
        },
        "unavailable_data": unavailable,
    }
    return report


def _run_technical_analysis(df: pd.DataFrame) -> Dict[str, Any]:
    """Runs the SAME SMC + confirmation-indicator modules used for Binance
    futures against this token's real GeckoTerminal candles."""
    # ICT Market Structure Engine (2026-07-28 cutover - see
    # MARKET_STRUCTURE_CUTOVER_REPORT.md). external_pivot_window=5
    # preserves the old MarketStructure default's swing granularity for
    # this token's Liquidity/OrderBlocks/SL-TP levels below.
    ms = MarketStructureEngine(df, external_pivot_window=5)
    snapshot = ms.analyze()
    breaks = snapshot.external_breaks

    current_price = float(df["close"].iloc[-1])

    latest_break = breaks[-1] if breaks else None
    direction = "bullish" if (latest_break and latest_break.direction == "bullish") else (
        "bearish" if latest_break else None
    )

    # FVG detection moved ahead of Order Block detection (was after it) so
    # the new ICT Order Block Engine can check FVG confluence - see
    # ORDER_BLOCK_ENGINE_IMPLEMENTATION_REPORT.md. FVGDetector itself is
    # unmodified, only the call order changed.
    fvg_detector = FVGDetector(df)
    fvgs = fvg_detector.detect_fvg()
    unfilled_fvgs = [f for f in fvgs if not f.filled]

    # ICT Order Block Engine (2026-07-28 cutover). `breaks`/`fvgs` (real,
    # already-computed above) thread through so the engine can tag genuine
    # BOS/CHoCH causation and FVG confluence.
    ob_engine = OrderBlockEngine(df, structure_breaks=breaks, fvgs=fvgs)
    if direction == "bullish":
        ob = ob_engine.find_bullish_order_block()
    elif direction == "bearish":
        ob = ob_engine.find_bearish_order_block()
    else:
        ob = None

    # ICT Liquidity Engine (2026-07-28 cutover - see
    # LIQUIDITY_ENGINE_IMPLEMENTATION_REPORT.md). Detection moved after
    # Market Structure/FVG/Order Block above (was first) so sweeps can be
    # tagged with real reversal_break_level/has_ob_confluence/
    # has_fvg_confluence, mirroring the Binance-pair scoring path's
    # migration in market_scorer.py.
    liq_engine = LiquidityEngine(
        df, snapshot.swing_highs, snapshot.swing_lows,
        internal_swing_highs=snapshot.internal_swing_highs,
        internal_swing_lows=snapshot.internal_swing_lows,
        structure_breaks=breaks,
        order_blocks=[ob] if ob else [],
        fvgs=fvgs,
    )
    all_levels = liq_engine.detect_buyside_liquidity() + liq_engine.detect_sellside_liquidity()
    swept = liq_engine.detect_liquidity_sweeps(all_levels)

    conf = ConfirmationIndicators(df)
    latest = conf.get_latest()

    trend = "neutral"
    if direction == "bullish":
        trend = "bullish"
    elif direction == "bearish":
        trend = "bearish"
    elif latest and latest.get("ema20") and latest.get("ema50"):
        trend = "bullish" if latest["ema20"] > latest["ema50"] else "bearish"

    ta_section = {
        "candles_used": len(df),
        "timeframe": "1h",
        "trend": trend,
        "latest_structure_break": (
            f"{latest_break.type} ({latest_break.direction})" if latest_break else None
        ),
        "order_block_present": bool(ob),
        "unfilled_fvg_count": len(unfilled_fvgs),
        "liquidity_swept": any(lvl.swept for lvl in swept),
        "rsi": round(latest["rsi"], 1) if latest and not pd.isna(latest.get("rsi")) else None,
        "adx": round(latest["adx"], 1) if latest and not pd.isna(latest.get("adx")) else None,
        "macd_bullish": (latest.get("macd_hist") or 0) > 0 if latest else None,
        "supertrend_bullish": (latest.get("supertrend_dir") or 0) > 0 if latest else None,
        "price_vs_vwap": (
            ("above" if current_price > latest["vwap"] else "below")
            if latest and latest.get("vwap") else None
        ),
        "price_vs_ema200": (
            ("above" if current_price > latest["ema200"] else "below")
            if latest and latest.get("ema200") else None
        ),
    }

    return {
        "ta_section": ta_section,
        "current_price": current_price,
        "atr": latest["atr"] if latest and not pd.isna(latest.get("atr")) else None,
        "trend": trend,
        "swing_highs": snapshot.swing_highs,
        "swing_lows": snapshot.swing_lows,
        "order_block": ob,
        "all_liquidity_levels": all_levels,
        "latest": latest,
    }


async def _fetch_onchain_mtf_dfs(network: str, pair_address: str, primary_1h_df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """
    Best-effort extra GeckoTerminal candle fetches for the Technical
    Analysis dashboard's Multi-Timeframe Analysis row. "1h" reuses the
    dataframe already fetched for every other section (no duplicate call).
    Any timeframe that fails to fetch is simply omitted - build_ta_dashboard
    reports a missing timeframe as "N/A", never a guess.
    """
    mtf_dfs: Dict[str, pd.DataFrame] = {"1h": primary_1h_df}

    async def _fetch_one(label: str, timeframe: str, aggregate: int):
        try:
            df = await fetch_token_candles(network, pair_address, timeframe=timeframe, aggregate=aggregate, limit=100)
            if not df.empty:
                mtf_dfs[label] = df
        except Exception as e:
            logger.warning(f"[token-scan] MTF fetch failed for {label}: {e}")

    await asyncio.gather(*(
        _fetch_one(label, timeframe, aggregate)
        for label, (timeframe, aggregate) in _MTF_TIMEFRAME_PARAMS.items()
    ))
    return mtf_dfs


def _score_confidence(f: Dict[str, Any]) -> tuple:
    """
    How many independent technical signals agree with the trend direction.
    Deliberately simpler than the futures AIScorer (no funding rate,
    liquidations, or multi-exchange BTC-trend context exist for an
    arbitrary DEX token) but built from the same real signal types.
    """
    trend = f["trend"]
    if trend == "neutral":
        return 0, "Neutral", ["No clear directional structure yet."]

    latest = f["latest"] or {}
    score = 40.0
    reasons = []
    is_long = trend == "bullish"

    if f["order_block"]:
        score += 15
        reasons.append("Order block present in trend direction.")
    if f["all_liquidity_levels"] and any(lvl.swept for lvl in f["all_liquidity_levels"]):
        score += 12
        reasons.append("Liquidity sweep detected.")

    ema20, ema50 = latest.get("ema20"), latest.get("ema50")
    if ema20 and ema50:
        aligned = (ema20 > ema50) if is_long else (ema20 < ema50)
        score += 10 if aligned else -10
        reasons.append(f"EMA20/50 {'aligned' if aligned else 'against'} trend.")

    adx = latest.get("adx")
    if adx and not pd.isna(adx):
        score += min(max((adx - 15) / 35, 0), 1) * 10

    macd_hist = latest.get("macd_hist")
    if macd_hist is not None and not pd.isna(macd_hist):
        macd_bullish = macd_hist > 0
        score += 8 if (macd_bullish == is_long) else -8

    st_dir = latest.get("supertrend_dir")
    if st_dir is not None:
        st_bullish = st_dir > 0
        score += 8 if (st_bullish == is_long) else -8
        reasons.append(f"Supertrend {'confirms' if (st_bullish == is_long) else 'opposes'} trend.")

    rsi = latest.get("rsi")
    if rsi is not None and not pd.isna(rsi):
        if is_long and 30 < rsi < 70:
            score += 5
        elif not is_long and 30 < rsi < 70:
            score += 5

    score = max(0, min(round(score), 100))
    trend_label = "Bullish" if is_long else "Bearish"
    return score, trend_label, reasons


def _compute_levels(f: Dict[str, Any], trend: str) -> tuple:
    if trend not in ("Bullish", "Bearish") or not f.get("atr") or not f.get("current_price"):
        return None, None, None, None, None, None

    price = f["current_price"]
    atr = f["atr"]
    swing_highs = f["swing_highs"]
    swing_lows = f["swing_lows"]
    ob = f["order_block"]

    if trend == "Bullish":
        swing_low = min(sw.price for sw in swing_lows[-3:]) if swing_lows else price - 2 * atr
        stop_candidates = [swing_low, price - 1.5 * atr]
        if ob and ob.type.value == "bullish_ob":
            stop_candidates.append(ob.low - 0.1 * atr)
        stop_loss = min(stop_candidates)
        risk = price - stop_loss
        tp1 = price + risk * 1.5
        tp2 = price + risk * 3.0
        tp3 = price + risk * 5.0
    else:
        swing_high = max(sw.price for sw in swing_highs[-3:]) if swing_highs else price + 2 * atr
        stop_candidates = [swing_high, price + 1.5 * atr]
        if ob and ob.type.value == "bearish_ob":
            stop_candidates.append(ob.high + 0.1 * atr)
        stop_loss = max(stop_candidates)
        risk = stop_loss - price
        tp1 = price - risk * 1.5
        tp2 = price - risk * 3.0
        tp3 = price - risk * 5.0

    if risk <= 0:
        return round(price, 8), None, None, None, None, None

    rr_to_tp2 = round(abs(tp2 - price) / risk, 1)
    return (
        round(price, 8),
        round(stop_loss, 8),
        round(tp1, 8),
        round(tp2, 8),
        round(tp3, 8),
        f"1:{rr_to_tp2}",
    )


def _score_safety(security: Dict[str, Any], available: bool) -> tuple:
    if not available:
        return 0, "Unknown", "Unknown", "Unknown", [
            f"Token security check unavailable: {security.get('error', 'unknown error')}"
        ]

    score = 50.0
    reasons: List[str] = []

    if security.get("honeypot") is True:
        score -= 60
        reasons.append("CRITICAL: contract flagged as a honeypot (cannot sell).")
    if security.get("cannot_sell_all") is True:
        score -= 20
        reasons.append("Cannot sell full balance in one transaction.")
    if security.get("mint_function_present") is True:
        score -= 10
        reasons.append("Mint function present - supply can be inflated.")
    if security.get("has_blacklist_function") is True:
        score -= 10
        reasons.append("Contract can blacklist wallets from trading.")
    if security.get("is_proxy_contract") is True:
        score -= 5
        reasons.append("Upgradeable proxy contract - logic can change after deployment.")
    if security.get("ownership_renounced") is True:
        score += 10
        reasons.append("Ownership renounced.")
    elif security.get("ownership_renounced") is False:
        score -= 5
        reasons.append("Ownership NOT renounced - owner retains privileged control.")
    if security.get("is_open_source") is True:
        score += 5

    lp_locked = security.get("lp_locked_pct")
    if lp_locked is not None:
        if lp_locked >= 80:
            score += 15
            reasons.append(f"LP {lp_locked:.0f}% locked/burned.")
        elif lp_locked >= 50:
            score += 8
        else:
            score -= 10
            reasons.append(f"Only {lp_locked:.0f}% of LP locked - liquidity can be pulled.")

    for tax_field, label in (("buy_tax_pct", "Buy"), ("sell_tax_pct", "Sell")):
        tax = security.get(tax_field)
        if tax is not None:
            if tax >= 25:
                score -= 25
                reasons.append(f"{label} tax is extreme ({tax:.0f}%).")
            elif tax >= 10:
                score -= 10
                reasons.append(f"{label} tax is high ({tax:.0f}%).")

    top_10 = security.get("top_10_holder_pct")
    holder_distribution = "Unknown"
    if top_10 is not None:
        if top_10 < 20:
            score += 10
            holder_distribution = "Excellent"
        elif top_10 < 40:
            score += 3
            holder_distribution = "Good"
        elif top_10 < 60:
            score -= 8
            holder_distribution = "Concentrated"
        else:
            score -= 15
            holder_distribution = "Highly Concentrated"
            reasons.append(f"Top 10 holders control {top_10:.0f}% of supply.")

    score = max(0, min(round(score), 100))

    if security.get("honeypot") is True or security.get("cannot_sell_all") is True:
        contract_risk = "Critical"
    elif score >= 75:
        contract_risk = "Low"
    elif score >= 50:
        contract_risk = "Medium"
    else:
        contract_risk = "High"

    manipulation_flags = sum([
        security.get("mint_function_present") is True,
        security.get("has_blacklist_function") is True,
        (lp_locked is not None and lp_locked < 50),
        (top_10 is not None and top_10 >= 40),
    ])
    manipulation_risk = "Low" if manipulation_flags == 0 else ("Medium" if manipulation_flags <= 2 else "High")

    return score, contract_risk, manipulation_risk, holder_distribution, reasons


def _score_liquidity(dex: Dict[str, Any], available: bool) -> tuple:
    if not available:
        return "Unknown", [f"Liquidity data unavailable: {dex.get('error', 'unknown error')}"]

    liq = dex.get("liquidity_usd")
    vol = dex.get("volume_24h_usd")
    reasons = []

    if liq is None:
        return "Unknown", ["No liquidity figure returned by DexScreener."]

    if liq >= 200_000:
        label = "Healthy"
    elif liq >= 50_000:
        label = "Moderate"
    elif liq >= 10_000:
        label = "Thin"
    else:
        label = "Very Thin"
        reasons.append(f"Liquidity is very thin (${liq:,.0f}) - high slippage risk.")

    if liq and vol and liq > 0:
        turnover = vol / liq
        if turnover > 5:
            reasons.append(f"24h volume is {turnover:.1f}x liquidity - unusually high turnover.")

    return label, reasons


def _final_decision(
    safety_score: int,
    confidence_pct: int,
    trend: str,
    honeypot: Optional[bool],
    cannot_sell_all: Optional[bool],
) -> str:
    if honeypot is True or cannot_sell_all is True:
        return "AVOID"
    if safety_score < 40:
        return "AVOID"

    if trend == "Bullish":
        if confidence_pct >= 80 and safety_score >= 70:
            return "STRONG BUY"
        if confidence_pct >= 60:
            return "BUY"
        return "NEUTRAL"
    if trend == "Bearish":
        if confidence_pct >= 60:
            return "SELL"
        return "NEUTRAL"
    return "NEUTRAL"
