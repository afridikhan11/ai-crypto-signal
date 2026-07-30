"""
SMC Frequency & Confluence Impact Analysis - Commodities vs Crypto
====================================================================

WHY THIS SCRIPT EXISTS (2026-07-26)
--------------------------------------------------------------------------
The Gold Signal tab was reported empty. Investigation (see production
logs) showed this is NOT a bug: Gold/Silver/Oil have simply never cleared
the shared MIN_CONFIDENCE=85 bar. A first pass at the REJECTED log lines
showed "No unfilled FVG" in 100% of commodity rejections and a full
OB+FVG+liquidity "confluence zone" in only 0.4% - suggesting SMC
confluence (specifically the FVG+OB overlap) may simply form less often
on these 4 symbols' charts than on crypto's.

The user's explicit instruction: do NOT relax any rule without real
statistical evidence. This script produces that evidence - it does NOT
change any live scoring/threshold. Nothing in app/ai or app/strategy is
touched by running this. It is a pure, read-only measurement tool.

WHAT IT MEASURES
--------------------------------------------------------------------------
For a crypto comparison basket (BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT,
XRPUSDT) and all 4 commodities (XAUUSDT, XAGUSDT, CLUSDT, BZUSDT), over
the last `--days` days of REAL 15m candles (same primary timeframe as
live), replayed with the exact same rolling 500-candle window live uses
(CryptoScanner.analyze_symbol / BacktestEngine.WARMUP_CANDLES):

  1. RAW SMC EVENT FREQUENCY (per 100 candle-steps, so different history
     lengths compare fairly) - completely independent of AIScorer/
     confidence, using the exact same SMC detector classes as live:
       - BOS events / CHoCH events (MarketStructureEngine.analyze)
       - Order Block formations (OrderBlockEngine.find_*_order_block)
       - Unfilled FVG formations + presence (FVGDetector.detect_fvg)
       - Liquidity sweep events (LiquidityEngine.detect_liquidity_sweeps)
       - Full 3-of-3 confluence steps (OB + overlapping unfilled FVG +
         swept liquidity in the same zone) - EXACTLY AIScorer.assess()'s
         current confluence rule, duplicated read-only for measurement.
       - Candidate 2-of-2 confluence steps (OB + swept liquidity, FVG NOT
         required) - the smallest possible relaxation being evaluated.

  2. CONFIDENCE/SIGNAL IMPACT of the 2-of-2 candidate vs the current 3-of-3
     rule, using the REAL, unmodified AIScorer weights/scores for every
     OTHER category - only the confluence category's value is swapped
     between the current rule and the candidate rule. For every step
     where the candidate rule would have newly cleared this symbol's
     actual MIN_CONFIDENCE (unchanged, still 85), a real signal is
     constructed with the SAME structure-based SL/TP logic
     SignalGenerator.generate() uses, and resolved against the real
     historical price path (BacktestEngine._resolve_trade - the same
     candle-by-candle high/low walk used everywhere else in this project)
     to get a genuine win/loss outcome, not a guess.

  3. A precision/recall-style summary: how many additional signals the
     candidate rule would have produced ("recall impact") and what their
     real historical win rate would have been ("precision impact"),
     compared to the current rule's baseline (today: ~0 commodity signals
     ever, per the production logs).

HOW TO RUN
--------------------------------------------------------------------------
This performs REAL Binance API calls (public klines endpoint, no API key
needed) - it must be run somewhere with real network access to
fapi.binance.com (the live backend server, NOT a sandboxed dev
environment with proxy-restricted egress).

    cd "FastAPI Backend"
    python scripts/analyze_smc_frequency.py --days 60

Prints a formatted report to stdout and writes the same data as JSON to
data/smc_frequency_report_<UTC-timestamp>.json for archival/sharing.

A 60-day, 9-symbol, 15m-candle run replays roughly 500-600 candle-steps
per symbol (after the 500-candle warmup) with a handful of SMC detector
calls per step - expect several minutes total, not seconds.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

# Allow `python scripts/analyze_smc_frequency.py` to resolve `app.*`
# imports the same way it would if run as `python -m scripts.analyze_smc_frequency`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.backtest.engine import BacktestEngine, _candles_to_df  # noqa: E402
from app.smc.market_structure_engine import MarketStructureEngine  # noqa: E402
from app.smc.liquidity_engine import LiquidityEngine  # noqa: E402
from app.smc.order_block_engine import OrderBlockEngine  # noqa: E402
from app.smc.fvg import FVGDetector  # noqa: E402
from app.smc.supply_demand_engine import SupplyDemandEngine  # noqa: E402
from app.indicators.confirmation import ConfirmationIndicators  # noqa: E402
from app.ai.calibration_profiles import get_profile_for_symbol  # noqa: E402
from app.ai.scorer import AIScorer  # noqa: E402

CRYPTO_SAMPLE = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
COMMODITIES = ["XAUUSDT", "XAGUSDT", "CLUSDT", "BZUSDT"]
WARMUP = BacktestEngine.WARMUP_CANDLES  # 500 - matches live's analyze_symbol exactly


# ==========================================================================
# PART 1 - RAW SMC EVENT FREQUENCY (no AIScorer involved at all)
# ==========================================================================

def measure_smc_frequency(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Rolling 500-candle-window replay, one SMC detection pass per step -
    identical cadence to live's analyze_symbol(). Pure structural counting,
    zero scoring/weighting. Read-only - does not import or touch
    app.ai.scorer or app.strategy.signal_generator.
    """
    n = len(df)
    start_i = WARMUP - 1
    if n <= start_i:
        return {"steps": 0, "note": f"Not enough candles ({n}) for a single {WARMUP}-candle window."}

    steps = 0
    bos_events = 0
    choch_events = 0
    ob_formations = 0
    fvg_new_formations = 0
    fvg_present_steps = 0
    sweep_events = 0
    confluence_3of3_steps = 0
    confluence_2of2_steps = 0

    prev_break_ts = None
    prev_fvg_keys: set = set()

    for i in range(start_i, n):
        window = df.iloc[max(0, i - WARMUP + 1): i + 1]
        steps += 1

        # ICT Market Structure Engine (2026-07-28 cutover - see
        # MARKET_STRUCTURE_CUTOVER_REPORT.md). external_pivot_window=5
        # preserves the old MarketStructure default's swing granularity.
        # NOTE: the `latest_break.timestamp != prev_break_ts` dedup check
        # below was PROVEN BROKEN against the old engine
        # (BOS_Duplicate_Investigation_Report.md) because
        # StructureBreak.timestamp always equaled "now" regardless of when
        # a break actually first happened, so this comparison was
        # mathematically almost always True. The new engine's
        # StructureBreak.timestamp is the break's real first-occurrence
        # candle (Event Identity fix), which makes this SAME comparison
        # correct without any change to the comparison itself - it was the
        # data, not this line, that was wrong.
        ms = MarketStructureEngine(window, external_pivot_window=5)
        snapshot = ms.analyze()
        breaks = snapshot.external_breaks
        latest_break = breaks[-1] if breaks else None

        if latest_break is not None and latest_break.timestamp != prev_break_ts:
            if latest_break.type == "CHoCH":
                choch_events += 1
            else:
                bos_events += 1
            prev_break_ts = latest_break.timestamp

        if latest_break is None:
            continue

        direction = "LONG" if latest_break.direction == "bullish" else "SHORT"

        # FVG detection moved ahead of Order Block/Liquidity detection (was
        # after Order Block) so both the new ICT Order Block Engine and the
        # new ICT Liquidity Engine can check FVG confluence - see
        # ORDER_BLOCK_ENGINE_IMPLEMENTATION_REPORT.md and
        # LIQUIDITY_ENGINE_IMPLEMENTATION_REPORT.md.
        fvgs = FVGDetector(window).detect_fvg()
        unfilled = [f for f in fvgs if not f.filled]
        fvg_keys = {(round(f.top, 8), round(f.bottom, 8)) for f in unfilled}
        if unfilled:
            fvg_present_steps += 1
        fvg_new_formations += len(fvg_keys - prev_fvg_keys)
        prev_fvg_keys = fvg_keys

        # ICT Order Block Engine (2026-07-28 cutover). `breaks`/`fvgs`
        # (real, already-computed above) thread through for BOS/CHoCH
        # causation + FVG confluence tagging.
        ob_engine = OrderBlockEngine(window, structure_breaks=breaks, fvgs=fvgs)
        ob = ob_engine.find_bullish_order_block() if direction == "LONG" else ob_engine.find_bearish_order_block()
        ob_present = bool(ob)
        if ob_present:
            ob_formations += 1

        # ICT Liquidity Engine (2026-07-28 cutover - see
        # LIQUIDITY_ENGINE_IMPLEMENTATION_REPORT.md). Detection moved after
        # Order Block/FVG above (was first) so sweeps can be tagged with
        # real reversal_break_level/has_ob_confluence/has_fvg_confluence.
        liq_engine = LiquidityEngine(
            window, snapshot.swing_highs, snapshot.swing_lows,
            structure_breaks=breaks, order_blocks=[ob] if ob else [], fvgs=fvgs,
        )
        all_levels = liq_engine.detect_buyside_liquidity() + liq_engine.detect_sellside_liquidity()
        # NOTE: detect_liquidity_sweeps() returns ALL levels (mutating each
        # one's .swept flag in place), NOT just the swept ones - matches
        # signal_generator.py's own `swept = liq_engine.detect_liquidity_sweeps(...)`
        # naming exactly (its "no_confirmation" hard gate checks whether this
        # raw list is non-empty at all, not whether a real sweep happened).
        # AIScorer.assess() is the one that actually filters for real sweeps
        # via `[l for l in liq.get("levels", []) if l.swept]` - this script
        # mirrors THAT filtering (the more meaningful "did a sweep really
        # happen" signal) for its own frequency/confluence counting below.
        liq_engine.detect_liquidity_sweeps(all_levels)
        swept_levels = [l for l in all_levels if l.swept]
        if swept_levels:
            sweep_events += 1

        # Zone construction + liq_in_zone check below duplicates
        # AIScorer.assess()'s "confluence" section EXACTLY (same OB+FVG
        # zone widening, same ATR*0.5 tolerance) so confluence_3of3_steps
        # here means precisely what the live confidence score's confluence
        # category means - not an approximation.
        atr_estimate = float((window["high"] - window["low"]).tail(14).mean())

        overlapping_fvg = None
        zone_low = zone_high = None
        if ob_present:
            zone_low, zone_high = ob.low, ob.high
            for f in unfilled:
                if f.bottom <= ob.high and ob.low <= f.top:
                    overlapping_fvg = f
                    break
            if overlapping_fvg is not None:
                zone_low = min(zone_low, overlapping_fvg.bottom)
                zone_high = max(zone_high, overlapping_fvg.top)

        liq_in_zone = False
        if swept_levels and zone_low is not None and zone_high is not None:
            tolerance = atr_estimate * 0.5 if atr_estimate > 0 else 0
            liq_in_zone = any((zone_low - tolerance) <= lvl.price <= (zone_high + tolerance) for lvl in swept_levels)

        if ob_present and liq_in_zone:
            confluence_2of2_steps += 1
        if ob_present and overlapping_fvg is not None and liq_in_zone:
            confluence_3of3_steps += 1

    def per_100(count: int) -> float:
        return round(count / steps * 100, 2) if steps else 0.0

    return {
        "steps": steps,
        "bos_events": bos_events, "bos_per_100_steps": per_100(bos_events),
        "choch_events": choch_events, "choch_per_100_steps": per_100(choch_events),
        "ob_formations": ob_formations, "ob_per_100_steps": per_100(ob_formations),
        "fvg_new_formations": fvg_new_formations, "fvg_new_per_100_steps": per_100(fvg_new_formations),
        "fvg_present_steps": fvg_present_steps, "fvg_present_per_100_steps": per_100(fvg_present_steps),
        "sweep_events": sweep_events, "sweep_per_100_steps": per_100(sweep_events),
        "confluence_3of3_steps": confluence_3of3_steps, "confluence_3of3_per_100_steps": per_100(confluence_3of3_steps),
        "confluence_2of2_steps": confluence_2of2_steps, "confluence_2of2_per_100_steps": per_100(confluence_2of2_steps),
    }


# ==========================================================================
# PART 2 - CONFIDENCE/WIN-RATE IMPACT of the 2-of-2 candidate confluence
# rule vs the current 3-of-3 rule. Reuses the REAL AIScorer (unmodified,
# real weights) and REAL BacktestEngine._resolve_trade for outcomes - only
# the "confluence" score entry is swapped between the two rules.
# ==========================================================================

MIN_TARGET_DISTANCE_ATR_FALLBACK = 1.5  # only used if a symbol's profile is somehow missing this field


def _build_signal_data(
    symbol: str, direction: str, window: pd.DataFrame, snapshot, ob,
    all_levels: List, atr: float, current_price: float, confidence: int, reason: str,
    scores: Dict[str, float], profile,
) -> Optional[Dict[str, Any]]:
    """
    Re-derives the SAME structure-based stop-loss/take-profit
    SignalGenerator.generate() computes (see that file - stop anchored to
    swing/OB invalidation, target from the nearest real liquidity level or
    an ATR fallback), using this symbol's real CalibrationProfile ATR
    multiples. Duplicated here (not imported) because generate() computes
    its own AIScorer.assess() call internally and can't have just the
    confluence score substituted from outside - this is the smallest way
    to get a REAL, faithful SL/TP + outcome for the counterfactual
    confidence without touching the live SignalGenerator class.
    """
    sl_mult = profile.stop_loss_atr_multiple
    ob_buf = profile.ob_invalidation_atr_buffer
    min_dist = profile.min_target_distance_atr
    tp_fallback = profile.take_profit_fallback_atr_multiple

    if direction == "LONG":
        swing_low = min(sw.price for sw in snapshot.swing_lows[-3:]) if snapshot.swing_lows else current_price - 2 * atr
        stop_candidates = [swing_low, current_price - sl_mult * atr]
        if ob and ob.type.value == "bullish_ob":
            stop_candidates.append(ob.low - ob_buf * atr)
        stop_loss = min(stop_candidates)
        targets = sorted(
            lvl.price for lvl in all_levels
            if lvl.type.name == "BUYSIDE" and lvl.price > current_price + min_dist * atr
        )
        take_profit = targets[0] if targets else current_price + tp_fallback * atr
    else:
        swing_high = max(sw.price for sw in snapshot.swing_highs[-3:]) if snapshot.swing_highs else current_price + 2 * atr
        stop_candidates = [swing_high, current_price + sl_mult * atr]
        if ob and ob.type.value == "bearish_ob":
            stop_candidates.append(ob.high + ob_buf * atr)
        stop_loss = max(stop_candidates)
        targets = sorted(
            (lvl.price for lvl in all_levels
             if lvl.type.name == "SELLSIDE" and lvl.price < current_price - min_dist * atr),
            reverse=True,
        )
        take_profit = targets[0] if targets else current_price - tp_fallback * atr

    risk = abs(current_price - stop_loss)
    reward = abs(take_profit - current_price)
    rr = round(reward / risk, 2) if risk > 0 else 0.0
    if rr < profile.min_risk_reward:
        return None  # same hard gate as live - a candidate signal still has to clear real RR

    return {
        "symbol": symbol, "direction": direction, "entry": round(current_price, 8),
        "stop_loss": round(stop_loss, 8), "take_profit": round(take_profit, 8),
        "risk_reward": rr, "confidence": confidence, "reason": reason, "score_breakdown": scores,
    }


def measure_confidence_impact(symbol: str, df: pd.DataFrame) -> Dict[str, Any]:
    """
    Replays the same rolling window as measure_smc_frequency, but this
    time runs the REAL AIScorer for this symbol (real weights, real
    profile) at every step where a break exists, using minimal/neutral
    stand-ins for the live-only context fields (btc_trend, HTF trends,
    fundamentals) that a pure historical-structure pass can't reconstruct
    without the full BacktestEngine machinery - see the module docstring's
    "known simplifications" note in app/backtest/engine.py, same idea
    applied here. This means the absolute confidence numbers below are not
    perfectly identical to a full live/backtest run, but the DELTA between
    the current-rule and candidate-rule confidence (which is what this
    analysis is actually measuring) is unaffected by that simplification,
    since both use the exact same neutral context.
    """
    n = len(df)
    start_i = WARMUP - 1
    if n <= start_i:
        return {"steps": 0}

    scorer = AIScorer(symbol)
    profile = get_profile_for_symbol(symbol)

    steps_with_break = 0
    current_rule_qualifying: List[Dict[str, Any]] = []
    candidate_rule_new_qualifying: List[Dict[str, Any]] = []

    for i in range(start_i, n):
        window = df.iloc[max(0, i - WARMUP + 1): i + 1]

        # ICT Market Structure Engine (2026-07-28 cutover - see
        # MARKET_STRUCTURE_CUTOVER_REPORT.md). external_pivot_window=5
        # preserves the old MarketStructure default's swing granularity.
        ms = MarketStructureEngine(window, external_pivot_window=5)
        snapshot = ms.analyze()
        breaks = snapshot.external_breaks
        if not breaks:
            continue
        latest_break = breaks[-1]
        direction = "LONG" if latest_break.direction == "bullish" else "SHORT"
        current_price = float(window["close"].iloc[-1])
        steps_with_break += 1

        # FVG detection moved ahead of Order Block/Liquidity detection (was
        # after Order Block) so both engines can check FVG confluence.
        fvgs = FVGDetector(window).detect_fvg()
        unfilled = [f for f in fvgs if not f.filled]

        # ICT Order Block Engine (2026-07-28 cutover). `breaks`/`fvgs`
        # thread through for BOS/CHoCH causation + FVG confluence tagging.
        ob_engine = OrderBlockEngine(window, structure_breaks=breaks, fvgs=fvgs)
        ob = ob_engine.find_bullish_order_block() if direction == "LONG" else ob_engine.find_bearish_order_block()
        ob_dict = None
        if ob:
            ob_dict = {"type": ob.type.value, "high": ob.high, "low": ob.low, "mitigated": ob.mitigated}

        # ICT Liquidity Engine (2026-07-28 cutover - see
        # LIQUIDITY_ENGINE_IMPLEMENTATION_REPORT.md). Detection moved after
        # Order Block/FVG above (was first) so sweeps can be tagged with
        # real reversal_break_level/has_ob_confluence/has_fvg_confluence.
        liq_engine = LiquidityEngine(
            window, snapshot.swing_highs, snapshot.swing_lows,
            structure_breaks=breaks, order_blocks=[ob] if ob else [], fvgs=fvgs,
        )
        all_levels = liq_engine.detect_buyside_liquidity() + liq_engine.detect_sellside_liquidity()
        swept = liq_engine.detect_liquidity_sweeps(all_levels)

        from app.indicators.confirmation import ConfirmationIndicators
        conf = ConfirmationIndicators(window, volume_spike_multiplier=profile.volume_spike_multiplier)
        conf_latest = conf.get_latest()
        if conf_latest is None:
            continue
        atr = conf_latest["atr"]
        if pd.isna(atr) or atr <= 0:
            continue

        from app.smc.supply_demand_engine import SupplyDemandEngine
        sd = SupplyDemandEngine(window)
        sd.calculate_recent_range()
        zone = sd.get_zone(current_price)
        zone_position = None
        if sd.range_high is not None and sd.range_low is not None and sd.range_high > sd.range_low:
            zone_position = (current_price - sd.range_low) / (sd.range_high - sd.range_low)

        features = {
            "direction": direction, "symbol": symbol, "current_price": current_price,
            "market_structure": {"bos_choch": breaks}, "liquidity": {"levels": swept},
            "order_block": ob_dict, "fvg": unfilled, "candlestick_pattern": None,
            "chart_pattern": None, "supply_demand_zone": zone, "zone_position": zone_position,
            "confirmation": conf_latest,
            # Neutral live-only context (see docstring) - identical for
            # both the current-rule and candidate-rule pass, so it cannot
            # bias the DELTA between them, only the absolute level.
            "institutional": {
                "btc_trend": "neutral", "funding_rate": 0.0, "volatility": "normal",
                "htf_trend_1h": "neutral", "htf_trend_4h": "neutral", "htf_trend_1d": "neutral",
                "entry_trend_5m": "neutral", "long_liquidated_usd": 0.0, "short_liquidated_usd": 0.0,
            },
            "fundamentals": {},
        }

        confidence_actual, reason, scores = scorer.assess(features)

        if confidence_actual >= profile.min_confidence:
            sig = _build_signal_data(
                symbol, direction, window, snapshot, ob, all_levels, atr, current_price,
                confidence_actual, reason, scores, profile,
            )
            if sig:
                current_rule_qualifying.append({"index": i, "signal": sig})

        # --- Candidate 2-of-2 confluence rule (FVG not required) ---
        # Uses only REAL swept levels (.swept == True) within the OB's zone
        # (ATR*0.5 tolerance) - same liq_in_zone check AIScorer.assess()
        # itself uses for the current 3-of-3 rule, just without requiring
        # an overlapping unfilled FVG on top of it.
        ob_present = bool(ob_dict)
        swept_levels = [l for l in all_levels if l.swept]
        liq_in_zone_candidate = False
        if ob_present and swept_levels:
            tolerance = atr * 0.5 if atr > 0 else 0
            liq_in_zone_candidate = any(
                (ob.low - tolerance) <= lvl.price <= (ob.high + tolerance) for lvl in swept_levels
            )
        candidate_confluence_achieved = ob_present and liq_in_zone_candidate
        if candidate_confluence_achieved and scores.get("confluence", 0) < 95:
            scores_candidate = dict(scores)
            scores_candidate["confluence"] = 95.0  # top tier, matching the current rule's own 3-of-3 ceiling
            confidence_candidate = int(round(sum(scorer.weights.get(k, 0) * scores_candidate[k] for k in scores_candidate)))
            if confidence_candidate >= profile.min_confidence and confidence_actual < profile.min_confidence:
                sig = _build_signal_data(
                    symbol, direction, window, snapshot, ob, all_levels, atr, current_price,
                    confidence_candidate, reason, scores_candidate, profile,
                )
                if sig:
                    candidate_rule_new_qualifying.append({"index": i, "signal": sig})

    def _resolve_and_summarize(qualifying: List[Dict[str, Any]]) -> Dict[str, Any]:
        engine = BacktestEngine(symbol)
        trades = []
        for entry in qualifying:
            outcome, _ts, _idx, rr = engine._resolve_trade(df, entry["index"] + 1, entry["signal"])
            trades.append({"outcome": outcome, "realized_rr": rr, "confidence": entry["signal"]["confidence"]})
        wins = sum(1 for t in trades if t["outcome"] == "TP_HIT")
        losses = sum(1 for t in trades if t["outcome"] == "STOPPED")
        resolved = wins + losses
        win_rate = round(wins / resolved * 100, 2) if resolved else None
        return {
            "signal_count": len(trades), "wins": wins, "losses": losses,
            "unresolved": len(trades) - resolved, "win_rate_pct": win_rate,
        }

    return {
        "steps_with_break": steps_with_break,
        "current_rule": _resolve_and_summarize(current_rule_qualifying),
        "candidate_rule_additional_signals": _resolve_and_summarize(candidate_rule_new_qualifying),
    }


# 2026-07-28: Part 3 (the detect_fvg() vs detect_fvg_ict() side-by-side
# comparison that used to live here - measure_fvg_algorithm_comparison(),
# measure_fvg_confidence_distribution(), FVG_ALGO_METHODS, and the
# corresponding _print_fvg_comparison_report()/_fmt() helpers below - has
# been removed. Its entire purpose was comparing the legacy 2-candle FVG
# detector against the ICT 3-candle candidate; now that the ICT detector has
# been promoted to be the one and only detect_fvg() implementation (see
# app/smc/fvg.py and BOS_ICT_Audit_Report.md / BOS_Duplicate_Investigation_
# Report.md for the investigation that led here), there is only one
# algorithm left, so that comparison no longer has anything to compare.
# Part 1 (measure_smc_frequency, below) and Part 2 (measure_confidence_
# impact, above) are unaffected - both already called FVGDetector.detect_fvg()
# by name and automatically pick up the promoted ICT implementation.


async def analyze_symbol(symbol: str, days: int) -> Dict[str, Any]:
    engine = BacktestEngine(symbol)
    primary, *_ = await engine._load_all_series(days)
    df = _candles_to_df(primary)
    freq = measure_smc_frequency(df)
    impact = measure_confidence_impact(symbol, df)

    return {
        "symbol": symbol, "candles_loaded": len(df), "frequency": freq, "confidence_impact": impact,
    }


def _avg(values: List[float]) -> float:
    return round(sum(values) / len(values), 2) if values else 0.0


def _print_report(results: Dict[str, Dict[str, Any]]) -> None:
    print("\n" + "=" * 78)
    print("SMC FREQUENCY & CONFLUENCE IMPACT REPORT - Commodities vs Crypto")
    print("=" * 78)

    # Only report on symbols that were actually analyzed this run (respects
    # --symbols overrides, which may be a subset of CRYPTO_SAMPLE/COMMODITIES
    # or omit one group entirely) - fixes a KeyError crash where this used
    # to unconditionally iterate the full default symbol lists regardless
    # of what was actually run.
    analyzed = list(results.keys())
    crypto_syms = [s for s in CRYPTO_SAMPLE if s in analyzed] or [s for s in analyzed if s not in COMMODITIES]
    commodity_syms = [s for s in COMMODITIES if s in analyzed]

    print("\n--- PART 1: RAW SMC EVENT FREQUENCY (per 100 candle-steps) ---\n")
    header = f"{'Symbol':<10}{'BOS':>7}{'CHoCH':>7}{'OB':>7}{'FVG-new':>9}{'FVG-pres':>10}{'Sweep':>7}{'Conf3/3':>9}{'Conf2/2':>9}"
    print(header)
    for group_name, symbols in [("CRYPTO", crypto_syms), ("COMMODITY", commodity_syms)]:
        if not symbols:
            continue
        print(f"-- {group_name} --")
        for s in symbols:
            f = results[s]["frequency"]
            if f.get("steps", 0) == 0:
                print(f"{s:<10} insufficient data")
                continue
            print(
                f"{s:<10}{f['bos_per_100_steps']:>7}{f['choch_per_100_steps']:>7}{f['ob_per_100_steps']:>7}"
                f"{f['fvg_new_per_100_steps']:>9}{f['fvg_present_per_100_steps']:>10}{f['sweep_per_100_steps']:>7}"
                f"{f['confluence_3of3_per_100_steps']:>9}{f['confluence_2of2_per_100_steps']:>9}"
            )

    crypto_fvg = _avg([results[s]["frequency"].get("fvg_present_per_100_steps", 0) for s in crypto_syms if results[s]["frequency"].get("steps")])
    comm_fvg = _avg([results[s]["frequency"].get("fvg_present_per_100_steps", 0) for s in commodity_syms if results[s]["frequency"].get("steps")])
    crypto_conf3 = _avg([results[s]["frequency"].get("confluence_3of3_per_100_steps", 0) for s in crypto_syms if results[s]["frequency"].get("steps")])
    comm_conf3 = _avg([results[s]["frequency"].get("confluence_3of3_per_100_steps", 0) for s in commodity_syms if results[s]["frequency"].get("steps")])
    comm_conf2 = _avg([results[s]["frequency"].get("confluence_2of2_per_100_steps", 0) for s in commodity_syms if results[s]["frequency"].get("steps")])

    print(f"\nCrypto avg unfilled-FVG presence: {crypto_fvg} per 100 steps")
    print(f"Commodity avg unfilled-FVG presence: {comm_fvg} per 100 steps")
    if crypto_fvg:
        print(f"  -> Commodities form unfilled FVGs at {round(comm_fvg / crypto_fvg * 100, 1)}% of crypto's rate")
    print(f"\nCrypto avg 3-of-3 confluence: {crypto_conf3} per 100 steps")
    print(f"Commodity avg 3-of-3 confluence: {comm_conf3} per 100 steps")
    print(f"Commodity avg 2-of-2 (candidate, no FVG) confluence: {comm_conf2} per 100 steps")
    if comm_conf3 or comm_conf2:
        gain = round((comm_conf2 - comm_conf3) / comm_conf3 * 100, 1) if comm_conf3 else float("inf")
        print(f"  -> Candidate rule fires {gain}% more often for commodities than the current rule")

    print("\n--- PART 2: CONFIDENCE/WIN-RATE IMPACT (current rule vs candidate 2-of-2 rule) ---\n")
    for s in commodity_syms:
        ci = results[s]["confidence_impact"]
        if ci.get("steps_with_break", 0) == 0:
            print(f"{s}: insufficient data")
            continue
        cur = ci["current_rule"]
        cand = ci["candidate_rule_additional_signals"]
        print(f"{s}:")
        print(f"  Steps with a BOS/CHoCH break: {ci['steps_with_break']}")
        print(f"  CURRENT rule   -> signals: {cur['signal_count']}, win rate: {cur['win_rate_pct']}%, wins/losses: {cur['wins']}/{cur['losses']}")
        print(f"  CANDIDATE rule -> ADDITIONAL signals unlocked: {cand['signal_count']}, "
              f"their win rate: {cand['win_rate_pct']}%, wins/losses: {cand['wins']}/{cand['losses']}, "
              f"unresolved: {cand['unresolved']}")
        print(
            f"  Recall impact: +{cand['signal_count']} signals over the analysis window. "
            f"Precision impact: candidate-only signals won {cand['win_rate_pct']}% vs "
            f"current-rule baseline {cur['win_rate_pct']}%."
        )

    print("\n" + "=" * 78)
    print("This report makes NO recommendation on its own - review the numbers above")
    print("against the smallest-possible-change principle before deciding anything.")
    print("=" * 78 + "\n")


async def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=60, help="Days of historical 15m candles to analyze (default 60)")
    parser.add_argument("--symbols", type=str, default=None, help="Comma-separated symbol override (default: full crypto sample + all 4 commodities)")
    args = parser.parse_args()

    symbols = args.symbols.split(",") if args.symbols else (CRYPTO_SAMPLE + COMMODITIES)

    results: Dict[str, Dict[str, Any]] = {}
    for s in symbols:
        print(f"Analyzing {s} ({args.days} days)...", flush=True)
        try:
            results[s] = await analyze_symbol(s, args.days)
        except Exception as e:
            print(f"  FAILED for {s}: {e}", flush=True)
            results[s] = {
                "symbol": s, "error": str(e), "frequency": {"steps": 0},
                "confidence_impact": {"steps_with_break": 0},
            }

    _print_report(results)

    out_dir = Path(__file__).resolve().parent.parent / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"smc_frequency_report_{ts}.json"
    out_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"Full JSON report written to: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
