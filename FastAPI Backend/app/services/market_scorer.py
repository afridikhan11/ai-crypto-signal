"""
Market Scanner - Spot/Futures analysis for a Binance trading pair symbol
(e.g. "BTCUSDT", "BNBUSDT"), returned in the SAME report shape as the
on-chain Token Scanner (app/services/token_scorer.py) so the WPF UI can
render either kind of scan without any interface changes.

--------------------------------------------------------------------------
WHY THIS EXISTS / HOW IT DIFFERS FROM THE LIVE SIGNAL GENERATOR
--------------------------------------------------------------------------
app/strategy/signal_generator.py's SignalGenerator.generate() is the LIVE
trading pipeline: it only ever returns a signal when confidence >= 85, RR
>= 2.0, a liquidity sweep or order block is present, AND the setup isn't
against the 1D/4H trend - otherwise it returns None (no signal today).
That's correct behaviour for a system that places real testnet orders, but
wrong for an interactive "scan this pair for me" tool - a user typing
"BTCUSDT" into the Token Scanner expects to always see an analysis, even
when there's currently no A+ setup, not a blank screen.

This module therefore builds the EXACT SAME feature set (SMC structure,
order blocks, FVGs, liquidity, supply/demand zone, confirmation
indicators, candlestick/chart patterns, multi-timeframe trend, funding
rate, open interest, fear & greed, BTC dominance) and scores it with the
SAME AIScorer used live - it just never hard-gates the result. Instead,
`_gate_status()` reports whether the setup WOULD have cleared the live
Signal Generator's bar, as one of the "reasons" - real signal, transparent
about it either way, never guessed.

This is intentionally a separate, read-only, non-persisting code path -
it never touches the Signal/Coin tables and never places or queues any
order. Duplicating the feature-building block from generate() (rather
than importing and calling it) is deliberate: generate()'s hard gates are
tuned/calibrated for the live system and must not be touched or bypassed
by adding a "skip gates" parameter to that function.
--------------------------------------------------------------------------
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
import pandas as pd
from loguru import logger

from app.legacy.trend import ema_trend_from_df
from app.ai.calibration_profiles import effective_min_confidence
from app.ai.scorer import AIScorer
from app.legacy.candlestick_patterns import detect_latest_pattern
from app.legacy.chart_patterns import detect_chart_pattern
from app.legacy.confirmation import ConfirmationIndicators
from app.services.binance_service import BinanceDataManager
from app.services.fundamentals_service import FundamentalsService
from app.services.smart_money_service import build_market_smart_money_dashboard
from app.services.ta_dashboard import build_ta_dashboard
from app.smc.fvg import FVGDetector
from app.smc.liquidity_engine import LiquidityEngine, LiquidityType
from app.smc.market_structure_engine import MarketStructureEngine
from app.smc.order_block_engine import OrderBlockEngine
from app.smc.supply_demand_engine import SupplyDemandEngine

# Same institutional-grade bar the live Signal Generator uses (see
# app/strategy/signal_generator.py) - duplicated as a plain constant here
# (not imported) so this read-only scan path has zero coupling to the
# live class, matching the isolation goal explained in the module
# docstring. MIN_CONFIDENCE is the Mainnet/Backtest value (85). This is
# advisory-only (never gates an order or a persisted Signal), so where it
# is actually USED below, it is passed through
# app.ai.calibration_profiles.effective_min_confidence() - the SAME
# environment check UniversalScanner's live gate already applies - so the
# displayed "would this pass the live bar" verdict matches the live
# scanner's ACTUAL current threshold (60 under Testnet, 85 otherwise)
# instead of silently going stale whenever Testnet mode is active. No
# scoring, weight, or calculation logic is affected - this only changes
# which number the existing pass/fail comparison is measured against.
MIN_CONFIDENCE = 85
MIN_RISK_REWARD = 2.0
MIN_TARGET_DISTANCE_ATR = 1.5
TREND_MIN_SEPARATION = 0.001

_EVM_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
_SOLANA_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


class MarketScanError(Exception):
    """Raised when `symbol` isn't a valid/liquid enough Binance USD-M Futures pair to analyze."""


def detect_input_type(raw_input: str) -> str:
    """
    Classifies what the user typed into the Token Scanner's single input
    box, so the same field can drive either on-chain or market analysis
    automatically:
      - "contract_address": EVM (0x + 40 hex chars) or Solana (32-44 char
        base58 mint address) - routed to the existing on-chain scanner.
      - "trading_pair": anything else (e.g. "BTCUSDT", "bnbusdt") - routed
        to this module's Binance market scan. The actual validity check
        (does this symbol really exist on Binance Futures) happens when
        candles are fetched, not here - this function only distinguishes
        *shape*, it doesn't guess whether a pair is real.
    """
    value = raw_input.strip()
    if _EVM_ADDRESS_RE.match(value):
        return "contract_address"
    if _SOLANA_ADDRESS_RE.match(value):
        return "contract_address"
    return "trading_pair"


async def _fetch_24h_quote_volume(symbol: str) -> Optional[float]:
    """Real 24h USDT quote volume from Binance's free public ticker endpoint -
    used to label liquidity honestly instead of assuming every pair is deep."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{BinanceDataManager.REST_URL}/fapi/v1/ticker/24hr", params={"symbol": symbol}
            )
            resp.raise_for_status()
            return float(resp.json().get("quoteVolume", 0.0))
    except Exception as e:
        logger.warning(f"[market-scan] Could not fetch 24h volume for {symbol}: {e}")
        return None


def _liquidity_label(quote_volume: Optional[float]) -> str:
    if quote_volume is None:
        return "Unknown"
    if quote_volume >= 100_000_000:
        return "Healthy"
    if quote_volume >= 20_000_000:
        return "Moderate"
    if quote_volume >= 5_000_000:
        return "Thin"
    return "Very Thin"


async def build_market_scan_report(raw_symbol: str) -> Dict[str, Any]:
    symbol = raw_symbol.strip().upper()

    manager = BinanceDataManager(list({symbol, "BTCUSDT"}))
    fundamentals = FundamentalsService()
    try:
        await manager.initialise_historical_data()

        df = manager.get_dataframe(symbol, "15m", limit=500)
        if len(df) < 60:
            raise MarketScanError(
                f"'{symbol}' doesn't look like a valid/active Binance USD-M Futures symbol "
                "(no candle history came back for it). Double-check the symbol, e.g. BTCUSDT, ETHUSDT."
            )

        df_1m = manager.get_dataframe(symbol, "1m", limit=5)
        live_price = float(df_1m["close"].iloc[-1]) if not df_1m.empty else float(df["close"].iloc[-1])

        df_5m = manager.get_dataframe(symbol, "5m", limit=100)
        df_1h = manager.get_dataframe(symbol, "1h", limit=100)
        df_4h = manager.get_dataframe(symbol, "4h", limit=100)
        daily_df = await manager.get_daily_dataframe(symbol, limit=100)

        btc_trend = ema_trend_from_df(manager.get_dataframe("btcusdt", "1h", limit=100), TREND_MIN_SEPARATION)
        htf_trend_1h = ema_trend_from_df(df_1h, TREND_MIN_SEPARATION)
        htf_trend_4h = ema_trend_from_df(df_4h, TREND_MIN_SEPARATION)
        entry_trend_5m = ema_trend_from_df(df_5m, TREND_MIN_SEPARATION)
        htf_trend_1d = ema_trend_from_df(daily_df, TREND_MIN_SEPARATION)
        funding_rate = await manager.get_funding_rate(symbol)

        oi_data = await fundamentals.get_open_interest_trend(symbol)
        long_short_ratio = await fundamentals.get_long_short_ratio(symbol)
        fear_greed = await fundamentals.get_fear_greed_index()
        btc_dominance = await fundamentals.get_btc_dominance()
        fundamentals_data = {
            "oi_trend": oi_data.get("oi_trend", "flat"),
            "oi_change_pct": oi_data.get("oi_change_pct", 0.0),
            "long_short_ratio": long_short_ratio,
            "fear_greed_value": fear_greed.get("value", 50),
            "btc_dominance": btc_dominance,
        }

        quote_volume = await _fetch_24h_quote_volume(symbol)
    finally:
        await manager.stop()
        await fundamentals.close()

    mtf_dfs = {"5m": df_5m, "15m": df, "1h": df_1h, "4h": df_4h, "1D": daily_df}

    analysis = _analyze(
        df=df,
        symbol=symbol,
        current_price=live_price,
        btc_trend=btc_trend,
        funding_rate=funding_rate,
        htf_trend_1h=htf_trend_1h,
        htf_trend_4h=htf_trend_4h,
        htf_trend_1d=htf_trend_1d,
        entry_trend_5m=entry_trend_5m,
        fundamentals_data=fundamentals_data,
        mtf_dfs=mtf_dfs,
    )

    # Institutional-grade Smart Money dashboard (whale activity, net flow,
    # accumulation/distribution, large transactions - built from Binance's
    # real aggregated trade prints, see app/services/smart_money_service.py).
    sm_dashboard = await build_market_smart_money_dashboard(symbol)
    smart_money_result = {
        "available": True,
        "error": None,
        "source": "Binance aggregated trade prints",
        "window_description": "most recent 1000 trades (not a fixed time window)",
        "dashboard": sm_dashboard["sections"],
        "ai_summary": sm_dashboard["ai_summary"],
    }

    liquidity_label = _liquidity_label(quote_volume)
    reasons = list(analysis["reasons"])
    if quote_volume is not None:
        reasons.append(f"24h Binance Futures volume: ${quote_volume:,.0f} ({liquidity_label.lower()} liquidity).")
    reasons.append(f"Funding rate: {funding_rate:+.4%} | Open interest trend: {fundamentals_data['oi_trend']}.")

    final_decision = _final_decision(analysis["trend"], analysis["confidence"], analysis["passes_all_gates"])

    report = {
        "chain": "binance_futures",
        "token_address": symbol,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "safety_score": 100,
        "confidence_pct": analysis["confidence"],
        "trend": analysis["trend"],
        "whales": "Not Available (requires Arkham Intelligence)",
        "smart_money": "Not Available (requires Nansen)",
        "liquidity": liquidity_label,
        "holder_distribution": "Not Applicable (Centralized Exchange Asset)",
        "contract_risk": "Not Applicable (Centralized Exchange Listing)",
        "manipulation_risk": "Low (top-tier centralized exchange - on-chain manipulation vectors like LP pulls/honeypots don't apply)",
        "best_entry": analysis["entry"],
        "stop_loss": analysis["stop_loss"],
        "take_profit_1": analysis["take_profit"],
        "take_profit_2": None,
        "take_profit_3": None,
        "risk_reward": f"1:{analysis['rr']}" if analysis["rr"] else None,
        "final_decision": final_decision,
        "reasons": reasons,
        # Phase 4 (Evidence & Reasoning Engine) - see _analyze()'s own
        # comment. None for the no-clear-structure fallback path, where
        # AIScorer never ran.
        "score_breakdown": analysis.get("score_breakdown"),
        "score_weights": analysis.get("score_weights"),
        "dexscreener": {
            "available": False,
            "error": f"Not applicable - '{symbol}' is traded on Binance (centralized exchange), not a DEX contract.",
        },
        "token_security": {
            "available": False,
            "error": (
                f"Not applicable - '{symbol}' is a centralized-exchange trading pair; on-chain contract "
                "checks (honeypot/mint/tax/ownership) don't apply."
            ),
        },
        "technical_analysis": analysis["ta_section"],
        "smart_money_analysis": smart_money_result,
        "contract_security_analysis": {
            "available": False,
            "error": (
                f"Not applicable - '{symbol}' is a centralized-exchange trading pair with no smart "
                "contract to inspect (mint authority/ownership/tax/LP checks only apply to on-chain tokens)."
            ),
            "sources_used": [],
            "dashboard": [],
            "ai_summary": None,
        },
        "sentiment": {
            "available": True,
            "fear_greed_value": fear_greed.get("value"),
            "fear_greed_classification": fear_greed.get("classification"),
        },
        "unavailable_data": [
            {"field": "whales", "reason": "Requires Arkham Intelligence (no free API tier)."},
            {"field": "smart_money", "reason": "Requires Nansen (paid subscription for API access)."},
            {"field": "holder_clusters", "reason": "Not applicable to a centralized-exchange asset (no on-chain holder set to cluster)."},
            {
                "field": "social_sentiment",
                "reason": (
                    "Twitter/Telegram/Reddit/news sentiment require a paid data provider - "
                    "only the free Crypto Fear & Greed Index (market-wide, not pair-specific) is used."
                ),
            },
        ],
    }
    return report


def _analyze(
    df: pd.DataFrame,
    symbol: str,
    current_price: float,
    btc_trend: str,
    funding_rate: float,
    htf_trend_1h: str,
    htf_trend_4h: str,
    htf_trend_1d: str,
    entry_trend_5m: str,
    fundamentals_data: Dict[str, Any],
    mtf_dfs: Optional[Dict[str, pd.DataFrame]] = None,
) -> Dict[str, Any]:
    """
    Mirrors SignalGenerator.generate()'s feature-building exactly (same
    modules, same order), but always returns a result - no hard-gate
    None-return - and reports gate pass/fail as information rather than a
    reject/accept decision.
    """
    # ICT Market Structure Engine (2026-07-28 cutover - see
    # MARKET_STRUCTURE_CUTOVER_REPORT.md). external_pivot_window=5 mirrors
    # SignalGenerator.generate()'s own migration (this module intentionally
    # duplicates its feature-building block - see module docstring), so
    # both paths read structure at the identical swing granularity.
    ms = MarketStructureEngine(df, external_pivot_window=5)
    snapshot = ms.analyze()
    breaks = snapshot.external_breaks
    conf = ConfirmationIndicators(df)
    latest = conf.get_latest()

    if latest is None:
        raise MarketScanError(
            f"'{symbol}': confirmation indicators came back with NaN values - not enough clean "
            "warm-up history yet to analyze this pair reliably."
        )

    if not breaks:
        # No BOS/CHoCH at all - fall back to a plain EMA20/50 read, same
        # honest "Neutral, no clear structure" shape as the on-chain
        # scanner's fallback (see token_scorer.py's _score_confidence).
        ema20, ema50 = latest.get("ema20"), latest.get("ema50")
        fallback_trend = "neutral"
        if ema20 and ema50 and not pd.isna(ema20) and not pd.isna(ema50):
            fallback_trend = "bullish" if ema20 > ema50 else "bearish"

        ta_section = _ta_section(df, latest, current_price, breaks, None, [], [])
        dashboard = build_ta_dashboard(df, current_price, mtf_dfs=mtf_dfs, latest=latest, trade_levels={})
        ta_section["dashboard"] = dashboard["sections"]
        ta_section["ai_summary"] = dashboard["ai_summary"]

        return {
            "confidence": 0,
            "trend": "Neutral" if fallback_trend == "neutral" else fallback_trend.capitalize(),
            "reasons": ["No clear directional structure break (BOS/CHoCH) on the 15m chart yet."],
            "entry": None,
            "stop_loss": None,
            "take_profit": None,
            "rr": None,
            "passes_all_gates": False,
            "ta_section": ta_section,
            # AIScorer never ran in this fallback branch (no direction to
            # score yet) - None here, not a guessed/empty breakdown.
            "score_breakdown": None,
            "score_weights": None,
        }

    latest_break = breaks[-1]
    direction = "LONG" if latest_break.direction == "bullish" else "SHORT"
    is_long = direction == "LONG"

    # FVG detection moved ahead of Order Block detection (was after it) so
    # the new ICT Order Block Engine can check FVG confluence - see
    # ORDER_BLOCK_ENGINE_IMPLEMENTATION_REPORT.md. FVGDetector itself is
    # unmodified, only the call order changed.
    fvg_detector = FVGDetector(df)
    fvgs = fvg_detector.detect_fvg()
    relevant_fvgs = [f for f in fvgs if not f.filled]

    # ICT Order Block Engine (2026-07-28 cutover). `breaks`/`fvgs` (real,
    # already-computed above) thread through so the engine can tag genuine
    # BOS/CHoCH causation and FVG confluence. `fvgs` (not `relevant_fvgs`)
    # is used here since confluence cares whether a gap FORMED in the same
    # displacement leg, not whether it has since been filled.
    ob_engine = OrderBlockEngine(df, structure_breaks=breaks, fvgs=fvgs)
    ob = ob_engine.find_bullish_order_block() if is_long else ob_engine.find_bearish_order_block()
    ob_dict = None
    if ob:
        ob_dict = {"type": ob.type.value, "high": ob.high, "low": ob.low, "mitigated": ob.mitigated}

    # ICT Liquidity Engine (2026-07-28 cutover - see
    # LIQUIDITY_ENGINE_IMPLEMENTATION_REPORT.md). Detection moved after
    # Market Structure/FVG/Order Block above (was first) so sweeps can be
    # tagged with real reversal_break_level/has_ob_confluence/
    # has_fvg_confluence, mirroring SignalGenerator.generate()'s migration.
    liq_engine = LiquidityEngine(
        df, snapshot.swing_highs, snapshot.swing_lows,
        internal_swing_highs=snapshot.internal_swing_highs,
        internal_swing_lows=snapshot.internal_swing_lows,
        structure_breaks=breaks,
        order_blocks=[ob] if ob else [],
        fvgs=fvgs,
    )
    equal_highs = liq_engine.detect_buyside_liquidity()
    equal_lows = liq_engine.detect_sellside_liquidity()
    all_levels = equal_highs + equal_lows
    swept = liq_engine.detect_liquidity_sweeps(all_levels)

    candlestick = detect_latest_pattern(df)

    sd = SupplyDemandEngine(df)
    sd.calculate_recent_range()
    zone = sd.get_zone(current_price)
    zone_position = None
    if sd.range_high is not None and sd.range_low is not None and sd.range_high > sd.range_low:
        zone_position = (current_price - sd.range_low) / (sd.range_high - sd.range_low)

    chart_pattern = detect_chart_pattern(df, snapshot.swing_highs, snapshot.swing_lows, latest["atr"])

    volatility = _classify_volatility(conf.atr)

    inst = {
        "btc_trend": btc_trend,
        "funding_rate": funding_rate,
        "volatility": volatility,
        "htf_trend_1h": htf_trend_1h,
        "htf_trend_4h": htf_trend_4h,
        "htf_trend_1d": htf_trend_1d,
        "entry_trend_5m": entry_trend_5m,
        "long_liquidated_usd": 0.0,
        "short_liquidated_usd": 0.0,
    }

    features = {
        "direction": direction,
        "symbol": symbol,
        "current_price": current_price,
        "market_structure": {"bos_choch": breaks},
        "liquidity": {"levels": swept},
        "order_block": ob_dict,
        "fvg": relevant_fvgs,
        "candlestick_pattern": candlestick,
        "chart_pattern": chart_pattern,
        "supply_demand_zone": zone,
        "zone_position": zone_position,
        "confirmation": latest,
        "institutional": inst,
        "fundamentals": fundamentals_data,
    }

    # Phase 4 (Evidence & Reasoning Engine, 2026-07-27): previously this
    # discarded AIScorer.assess()'s third return value (`_score_breakdown`)
    # entirely - the per-category 0-100 scores (market_structure,
    # liquidity_sweep, order_block_quality, ... - see app/ai/scorer.py) that
    # ALREADY get computed and weighted into `confidence` every time this
    # runs. Nothing new is calculated here: `confidence` has always been
    # `sum(weight[k] * score[k] for k in scores)` (see scorer.py's own
    # `assess()`), this just keeps the per-category terms of that SAME sum
    # (plus the weights used) instead of throwing them away, so the Evidence
    # Engine can honestly explain which categories drove/reduced the final
    # number - see app/agent/evidence_engine.py.
    _scorer = AIScorer(symbol)
    confidence, reason, score_breakdown = _scorer.assess(features)

    atr = latest["atr"]
    entry = stop_loss = take_profit = None
    rr = None
    if not pd.isna(atr) and atr > 0:
        if is_long:
            swing_low = min(sw.price for sw in snapshot.swing_lows[-3:]) if snapshot.swing_lows else current_price - 2 * atr
            stop_candidates = [swing_low, current_price - 1.5 * atr]
            if ob_dict and ob_dict.get("type") == "bullish_ob":
                stop_candidates.append(ob_dict["low"] - 0.1 * atr)
            stop_loss = min(stop_candidates)

            targets = sorted(
                lvl.price for lvl in all_levels
                if lvl.type == LiquidityType.BUYSIDE and lvl.price > current_price + MIN_TARGET_DISTANCE_ATR * atr
            )
            take_profit = targets[0] if targets else current_price + 3 * atr
        else:
            swing_high = max(sw.price for sw in snapshot.swing_highs[-3:]) if snapshot.swing_highs else current_price + 2 * atr
            stop_candidates = [swing_high, current_price + 1.5 * atr]
            if ob_dict and ob_dict.get("type") == "bearish_ob":
                stop_candidates.append(ob_dict["high"] + 0.1 * atr)
            stop_loss = max(stop_candidates)

            targets = sorted(
                (lvl.price for lvl in all_levels
                 if lvl.type == LiquidityType.SELLSIDE and lvl.price < current_price - MIN_TARGET_DISTANCE_ATR * atr),
                reverse=True,
            )
            take_profit = targets[0] if targets else current_price - 3 * atr

        entry = round(current_price, 8)
        stop_loss = round(stop_loss, 8)
        take_profit = round(take_profit, 8)
        risk = abs(current_price - stop_loss)
        reward = abs(take_profit - current_price)
        rr = round(reward / risk, 2) if risk > 0 else None

    # Resolved fresh on every call (this function has no persistent state
    # to go stale, unlike UniversalScanner's per-symbol generators which
    # resolve it once at startup) so the advisory verdict below always
    # matches whatever the live scanner is ACTUALLY gating on right now -
    # see effective_min_confidence()'s own docstring.
    live_min_confidence = effective_min_confidence(MIN_CONFIDENCE)

    passes_confidence = confidence >= live_min_confidence
    passes_liquidity_ob = bool(swept) or bool(ob_dict)

    def _opposes_higher_tf(trend: str) -> bool:
        return (is_long and trend == "down") or (not is_long and trend == "up")

    passes_htf = not (_opposes_higher_tf(htf_trend_1d) or _opposes_higher_tf(htf_trend_4h))
    passes_rr = rr is not None and rr >= MIN_RISK_REWARD
    passes_all_gates = passes_confidence and passes_liquidity_ob and passes_htf and passes_rr

    reasons = [reason]
    if passes_all_gates:
        reasons.append(
            f"Meets the live Signal Generator's institutional-grade bar "
            f"(confidence >= {live_min_confidence}, RR >= {MIN_RISK_REWARD}, HTF-aligned, liquidity/OB confirmed)."
        )
    else:
        failed = []
        if not passes_confidence:
            failed.append(f"confidence {confidence} < {live_min_confidence}")
        if not passes_liquidity_ob:
            failed.append("no liquidity level or order block detected")
        if not passes_htf:
            failed.append("against the 1D/4H higher-timeframe trend")
        if not passes_rr:
            failed.append(f"risk:reward {rr if rr is not None else 'n/a'} < {MIN_RISK_REWARD}")
        reasons.append("Would NOT qualify as a live Signal right now: " + "; ".join(failed) + ".")

    trend_label = "Bullish" if is_long else "Bearish"

    ta_section = _ta_section(df, latest, current_price, breaks, ob_dict, relevant_fvgs, swept)
    dashboard = build_ta_dashboard(
        df, current_price, mtf_dfs=mtf_dfs, latest=latest,
        trade_levels={"entry": entry, "stop_loss": stop_loss, "take_profit": take_profit,
                      "risk_reward": f"1:{rr}" if rr else None},
    )
    ta_section["dashboard"] = dashboard["sections"]
    ta_section["ai_summary"] = dashboard["ai_summary"]

    return {
        "confidence": confidence,
        "trend": trend_label,
        "reasons": reasons,
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "rr": rr,
        "passes_all_gates": passes_all_gates,
        "ta_section": ta_section,
        # Phase 4: the same per-category scores/weights AIScorer already
        # multiplied together to produce `confidence` above - see this
        # function's `_scorer.assess(features)` call.
        "score_breakdown": score_breakdown,
        "score_weights": dict(_scorer.weights),
    }


def _ta_section(
    df: pd.DataFrame,
    latest: Optional[Dict[str, Any]],
    current_price: float,
    breaks,
    ob_dict: Optional[Dict[str, Any]],
    relevant_fvgs: List[Any],
    swept_levels: List[Any],
) -> Dict[str, Any]:
    latest_break = breaks[-1] if breaks else None
    return {
        "available": True,
        "error": None,
        "candles_used": len(df),
        "timeframe": "15m",
        "trend": (
            "bullish" if (latest_break and latest_break.direction == "bullish")
            else "bearish" if latest_break else "neutral"
        ),
        "latest_structure_break": (
            f"{latest_break.type} ({latest_break.direction})" if latest_break else None
        ),
        "order_block_present": bool(ob_dict),
        "unfilled_fvg_count": len(relevant_fvgs),
        "liquidity_swept": any(getattr(lvl, "swept", False) for lvl in swept_levels),
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


def _classify_volatility(atr_series: pd.Series, lookback: int = 50) -> str:
    """Same logic as SignalGenerator._classify_volatility - duplicated (not
    imported) to keep this module fully decoupled from the live class."""
    recent = atr_series.dropna().iloc[-lookback:]
    if len(recent) < 10:
        return "normal"
    current = recent.iloc[-1]
    baseline = recent.mean()
    if baseline <= 0 or pd.isna(current):
        return "normal"
    ratio = current / baseline
    if ratio > 1.5:
        return "high"
    elif ratio < 0.7:
        return "low"
    return "normal"


def _final_decision(trend: str, confidence: int, passes_all_gates: bool) -> str:
    if trend == "Bullish":
        if passes_all_gates:
            return "STRONG BUY"
        if confidence >= 60:
            return "BUY"
        return "NEUTRAL"
    if trend == "Bearish":
        if passes_all_gates:
            return "STRONG SELL"
        if confidence >= 60:
            return "SELL"
        return "NEUTRAL"
    return "NEUTRAL"
