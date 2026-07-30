"""
Institutional-grade Technical Analysis dashboard - shared by both the
on-chain Token Scanner (token_scorer.py) and the Binance market scanner
(market_scorer.py) so a single implementation produces the SAME dashboard
shape for either kind of scan.

Every section below is a REAL, deterministic read of the SMC/indicator
modules already used elsewhere in this project (app/smc/*,
app/indicators/confirmation.py) - nothing here is a placeholder or a
random number. Where a signal genuinely can't be computed (e.g. Volume
Delta needs Binance's taker-buy-volume field, which DEX/GeckoTerminal
candles don't carry), the section says so explicitly via
status="Not Available" and confidence=0, per this project's "never
hallucinate" rule.

Each section is scored with:
  - status: a short human-readable read (e.g. "Bullish BOS", "No Sweep Detected")
  - strength (0-100): how significant/large the underlying signal is
  - confidence (0-100): how much weight to put on it (0 = not available)
Both are deliberately simple, explainable arithmetic on real values (ATR
ratios, cluster sizes, distances) - not another ML/black-box layer on top
of the AI Scorer that already exists.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from app.legacy.trend import ema_trend_from_df
from app.legacy.confirmation import ConfirmationIndicators
from app.smc.fvg import FVGDetector, FVGType
from app.smc.liquidity_engine import LiquidityEngine, LiquidityType
from app.smc.market_structure_engine import MarketStructureEngine, StructureBreak
from app.smc.order_block_engine import OrderBlockEngine
from app.smc.supply_demand_engine import SupplyDemandEngine, ZoneState, ZoneStrength

MTF_TREND_MIN_SEPARATION = 0.001


def _section(
    name: str, status: str, strength: int, confidence: int, detail: Optional[str] = None
) -> Dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "strength": max(0, min(100, round(strength))),
        "confidence": max(0, min(100, round(confidence))),
        "detail": detail,
    }


# 2026-07-28 (Order Block cutover): `_is_momentum_candle()` and
# `_find_broken_order_block()` used to live here as a standalone,
# duplicate copy of OrderBlockDetector's momentum-candle check and an
# independent breaker-block finder - already flagged as duplicate logic
# in SMC_ICT_MASTER_MIGRATION_PLAN.md's Phase 2. Both are removed: the
# new OrderBlockEngine's own `find_bullish_breaker_block()`/
# `find_bearish_breaker_block()` (see ORDER_BLOCK_ENGINE_IMPLEMENTATION_
# REPORT.md) now does this job as a first-class, correctly life-cycled
# ICT concept instead of an ad-hoc local reimplementation.


def _classify_volatility(atr_series: pd.Series, lookback: int = 50) -> str:
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
    if ratio < 0.7:
        return "low"
    return "normal"


def build_ta_dashboard(
    df: pd.DataFrame,
    current_price: float,
    mtf_dfs: Optional[Dict[str, pd.DataFrame]] = None,
    latest: Optional[Dict[str, Any]] = None,
    trade_levels: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    `df` is the primary structure timeframe. `mtf_dfs` is an optional
    {"5m": df, "15m": df, "1h": df, "4h": df, "1D": df} map (whichever
    timeframes the caller could fetch - missing ones are reported as
    Unavailable, not guessed). `latest` lets the caller pass in an
    already-computed ConfirmationIndicators.get_latest() to avoid
    recomputing every indicator a second time; if omitted this computes it
    itself. `trade_levels` (optional {"entry", "stop_loss", "take_profit",
    "risk_reward"}) lets the AI Technical Summary reuse the SAME
    structure-anchored levels already shown in the report's main "Suggested
    Trade Levels" card, instead of computing a second, possibly-conflicting
    set of numbers.
    """
    conf = ConfirmationIndicators(df)
    if latest is None:
        latest = conf.get_latest()

    # ICT Market Structure Engine (2026-07-28 cutover - see
    # MARKET_STRUCTURE_CUTOVER_REPORT.md). Two calls, not three: the
    # engine natively models "internal" (finer pivots) and "external"
    # (bigger swing pivots) as its own two built-in tiers - what used to
    # be three separate MarketStructure(pivot_window=...) instances is now
    # one MarketStructureEngine(internal_pivot_window=3,
    # external_pivot_window=8) call for the Internal/External BOS cards,
    # plus a second call at external_pivot_window=5 to preserve the old
    # "default" tier's exact swing granularity for the BOS/CHoCH cards and
    # for Liquidity below (unchanged from before this migration).
    ms_default = MarketStructureEngine(df, external_pivot_window=5)
    snap_default = ms_default.analyze()
    breaks_default = snap_default.external_breaks

    ms_tiers = MarketStructureEngine(df, internal_pivot_window=3, external_pivot_window=8)
    snap_tiers = ms_tiers.analyze()
    breaks_internal = snap_tiers.internal_breaks
    breaks_external = snap_tiers.external_breaks

    def latest_of_type(breaks: List[StructureBreak], break_type: str) -> Optional[StructureBreak]:
        matches = [b for b in breaks if b.type == break_type]
        return matches[-1] if matches else None

    atr = latest.get("atr") if latest else None
    has_atr = atr is not None and not pd.isna(atr) and atr > 0
    volume_backed = bool(latest.get("volume_spike")) if latest else False

    def structure_break_section(name: str, brk: Optional[StructureBreak], base_confidence: int) -> Dict[str, Any]:
        if not brk:
            return _section(name, "No Break Detected", 0, 0)
        distance = abs(current_price - brk.level)
        strength = round(distance / atr * 25) if has_atr else 50
        confidence = base_confidence + (10 if volume_backed else 0)
        return _section(
            name,
            f"{brk.direction.capitalize()} {brk.type}",
            strength,
            confidence,
            f"Broke {brk.direction} through {brk.level:.8g}.",
        )

    sections: List[Dict[str, Any]] = []
    sections.append(structure_break_section("BOS", latest_of_type(breaks_default, "BOS"), 65))
    sections.append(structure_break_section("CHoCH", latest_of_type(breaks_default, "CHoCH"), 70))
    sections.append(structure_break_section("Internal BOS", latest_of_type(breaks_internal, "BOS"), 50))
    sections.append(structure_break_section("External BOS", latest_of_type(breaks_external, "BOS"), 75))

    # --- Fair Value Gaps (detection moved ahead of Order Blocks/Liquidity ---
    # below so both the new ICT Order Block Engine and ICT Liquidity Engine
    # can check FVG confluence - see ORDER_BLOCK_ENGINE_IMPLEMENTATION_
    # REPORT.md and LIQUIDITY_ENGINE_IMPLEMENTATION_REPORT.md. FVGDetector
    # itself is unmodified; the "Fair Value Gaps"/"Inverse FVG" report cards
    # further down still build from these same `unfilled`/`inverse_fvgs`
    # variables at their original position in the dashboard's card order.
    fvg_detector = FVGDetector(df)
    fvgs = fvg_detector.detect_fvg()
    unfilled = [f for f in fvgs if not f.filled]
    inverse_fvgs = fvg_detector.detect_inverse_fvg()

    # --- Order Blocks (detection moved ahead of Liquidity below so the ---
    # new ICT Liquidity Engine can check OB confluence on sweeps). ICT
    # Order Block Engine (2026-07-28 cutover). `breaks_default`/`fvgs`
    # (real, already-computed above) thread through so the engine can tag
    # genuine BOS/CHoCH causation and FVG confluence, and its own lifecycle
    # tracking (fresh/mitigated/breaker/invalidated) replaces the old
    # single-price-snapshot `check_mitigation()` and the separate, now-
    # removed `_find_broken_order_block()` duplicate breaker detector. The
    # "Order Blocks"/"Breaker Blocks"/"Mitigation Blocks" report cards
    # further down still build from these same `bullish_ob`/`bearish_ob`
    # variables at their original position in the dashboard's card order.
    ob_engine = OrderBlockEngine(df, structure_breaks=breaks_default, fvgs=fvgs)
    bullish_ob = ob_engine.find_bullish_order_block()
    bearish_ob = ob_engine.find_bearish_order_block()

    # --- Liquidity -----------------------------------------------------
    # ICT Liquidity Engine (2026-07-28 cutover - see
    # LIQUIDITY_ENGINE_IMPLEMENTATION_REPORT.md). Detection moved after
    # Order Blocks/FVG above (was first) so sweeps can be tagged with real
    # reversal_break_level/has_ob_confluence/has_fvg_confluence. Both
    # `bullish_ob`/`bearish_ob` (not just one direction) are passed since
    # this dashboard, unlike the directional scoring paths, always
    # evaluates both sides at once.
    liq_engine = LiquidityEngine(
        df, snap_default.swing_highs, snap_default.swing_lows,
        internal_swing_highs=snap_tiers.internal_swing_highs,
        internal_swing_lows=snap_tiers.internal_swing_lows,
        structure_breaks=breaks_default,
        order_blocks=[ob for ob in (bullish_ob, bearish_ob) if ob],
        fvgs=fvgs,
    )
    equal_highs = liq_engine.detect_buyside_liquidity()
    equal_lows = liq_engine.detect_sellside_liquidity()
    all_levels = equal_highs + equal_lows
    swept_levels = liq_engine.detect_liquidity_sweeps(all_levels)

    buyside_swept = any(l.type == LiquidityType.BUYSIDE and l.swept for l in swept_levels)
    sellside_swept = any(l.type == LiquidityType.SELLSIDE and l.swept for l in swept_levels)
    if buyside_swept and sellside_swept:
        sweep_status = "Both Sides Swept"
    elif buyside_swept:
        sweep_status = "Buyside Liquidity Swept"
    elif sellside_swept:
        sweep_status = "Sellside Liquidity Swept"
    else:
        sweep_status = "No Sweep Detected"
    max_cluster = max((len(l.swing_points) for l in swept_levels if l.swept), default=0)
    sections.append(_section(
        "Liquidity Sweep", sweep_status, max_cluster * 25, 70 if (buyside_swept or sellside_swept) else 25,
        f"{len(swept_levels)} equal-high/low level(s) tracked, {sum(1 for l in swept_levels if l.swept)} swept.",
    ))

    def equal_levels_section(name: str, levels: List[Any]) -> Dict[str, Any]:
        if not levels:
            return _section(name, "None Detected", 0, 20)
        max_size = max(len(l.swing_points) for l in levels)
        swept_count = sum(1 for l in levels if l.swept)
        return _section(
            name, f"{len(levels)} Cluster(s) Found", max_size * 25, 55 + swept_count * 10,
            f"{swept_count} of {len(levels)} cluster(s) already swept.",
        )

    sections.append(equal_levels_section("Equal Highs", equal_highs))
    sections.append(equal_levels_section("Equal Lows", equal_lows))

    # --- Order Blocks / Breaker / Mitigation report cards --------------
    def ob_range_strength(ob) -> int:
        return round((ob.high - ob.low) / atr * 30) if has_atr else 50

    if bullish_ob and bearish_ob:
        ob_status = "Bullish + Bearish OB Present"
    elif bullish_ob:
        ob_status = "Bullish OB Active"
    elif bearish_ob:
        ob_status = "Bearish OB Active"
    else:
        ob_status = "No Active Order Block"
    active_ob = bullish_ob or bearish_ob
    ob_strength = ob_range_strength(active_ob) if active_ob else 0
    ob_confidence = 0
    if bullish_ob:
        ob_confidence = max(ob_confidence, 40 if bullish_ob.mitigated else 80)
    if bearish_ob:
        ob_confidence = max(ob_confidence, 40 if bearish_ob.mitigated else 80)
    sections.append(_section("Order Blocks", ob_status, ob_strength, ob_confidence))

    bullish_breaker = ob_engine.find_bullish_breaker_block()
    bearish_breaker = ob_engine.find_bearish_breaker_block()
    if bullish_breaker and bearish_breaker:
        breaker_status = "Bullish + Bearish Breaker Present"
    elif bullish_breaker:
        breaker_status = "Bullish Breaker Block"
    elif bearish_breaker:
        breaker_status = "Bearish Breaker Block"
    else:
        breaker_status = "No Breaker Block Detected"
    active_breaker = bullish_breaker or bearish_breaker
    breaker_strength = round((active_breaker.high - active_breaker.low) / atr * 30) if (active_breaker and has_atr) else 0
    sections.append(_section("Breaker Blocks", breaker_status, breaker_strength, 60 if active_breaker else 0))

    mitigating = None
    if bullish_ob and bullish_ob.mitigated:
        mitigating = "bullish"
    elif bearish_ob and bearish_ob.mitigated:
        mitigating = "bearish"
    # Wording updated from "Price Inside X Order Block" to "X Order Block
    # Mitigated": `.mitigated` now means "has EVER traded back into this
    # zone since it formed" (a real forward scan of the whole window -
    # see OrderBlockEngine._lifecycle()), not "is price inside it RIGHT
    # NOW" - the old check_mitigation() only checked the latter, which
    # under-reported OBs that were tested and left days ago.
    mitigation_status = (
        f"{mitigating.capitalize()} Order Block Mitigated" if mitigating else "Not Yet Mitigated"
    )
    sections.append(_section(
        "Mitigation Blocks", mitigation_status, 70 if mitigating else 0, 65 if mitigating else 20,
    ))

    # --- Supply / Demand / Premium-Discount ---------------------------
    # ICT Supply & Demand Engine (2026-07-28 cutover - see
    # SUPPLY_DEMAND_ENGINE_IMPLEMENTATION_REPORT.md). `breaks_default`/
    # `bullish_ob`+`bearish_ob`/`swept_levels`/`fvgs` (all real, already
    # computed above for the Market Structure/Order Block/Liquidity/FVG
    # sections) thread through so zones can be tagged with genuine
    # BOS/CHoCH causation and Order Block/Liquidity/FVG confluence.
    # `calculate_recent_range()`/`get_zone()` reproduce the OLD engine's
    # exact Premium/Discount formula byte-for-byte (see the engine's own
    # module docstring's "BACKWARD-COMPATIBLE CONTRACT" section) so the
    # Premium/Discount card below is completely unchanged.
    sd = SupplyDemandEngine(
        df, structure_breaks=breaks_default,
        order_blocks=[ob for ob in (bullish_ob, bearish_ob) if ob],
        liquidity_levels=swept_levels,
        fvgs=fvgs,
    )
    sd.calculate_recent_range()
    zone = sd.get_zone(current_price)

    # "Supply Zones"/"Demand Zones" cards upgraded from the old engine's
    # single flat range_high/range_low reference point to a REAL nearest
    # non-broken zone (with its own Fresh/Tested/Mitigated state and
    # Strength) - see the engine's module docstring's "WHAT WAS WRONG
    # WITH THE OLD IMPLEMENTATION" section for the exact gap this fixes.
    _ZONE_STRENGTH_CONFIDENCE = {ZoneStrength.STRONG: 85, ZoneStrength.MODERATE: 60, ZoneStrength.WEAK: 35}

    def zone_section(name: str, zones: List[Any], noun: str) -> Dict[str, Any]:
        active = [z for z in zones if z.state != ZoneState.BROKEN]
        if not active or not has_atr:
            return _section(name, "No Active Zone Detected", 0, 20)
        nearest = min(active, key=lambda z: abs((z.top + z.bottom) / 2 - current_price))
        state_label = nearest.state.value.capitalize()
        confluence_bits = []
        if nearest.caused_choch:
            confluence_bits.append("CHoCH-confirmed")
        elif nearest.caused_bos:
            confluence_bits.append("BOS-confirmed")
        if nearest.has_ob_confluence:
            confluence_bits.append("OB confluence")
        if nearest.has_liquidity_confluence:
            confluence_bits.append("Liquidity confluence")
        if nearest.has_fvg_confluence:
            confluence_bits.append("FVG confluence")
        detail = f"{state_label} {noun} zone {nearest.bottom:.8g}-{nearest.top:.8g}, strength={nearest.strength.value}"
        if confluence_bits:
            detail += " (" + ", ".join(confluence_bits) + ")"
        strength_score = min(100, round(nearest.displacement_ratio * 20))
        return _section(name, f"{state_label} Zone at {nearest.bottom:.8g}-{nearest.top:.8g}",
                         strength_score, _ZONE_STRENGTH_CONFIDENCE[nearest.strength], detail)

    sections.append(zone_section("Supply Zones", sd.find_supply_zones(), "supply"))
    sections.append(zone_section("Demand Zones", sd.find_demand_zones(), "demand"))

    if zone == "unknown":
        sections.append(_section("Premium/Discount", "Unknown", 0, 0))
    else:
        range_size = (sd.range_high - sd.range_low) if (sd.range_high and sd.range_low) else 0
        position = (current_price - sd.range_low) / range_size if range_size > 0 else 0.5
        pd_strength = round(abs(position - 0.5) * 200)
        sections.append(_section(
            "Premium/Discount", zone.capitalize(), pd_strength, 70,
            f"Price sits at {position:.0%} of the recent range (0%=low, 100%=high).",
        ))

    # --- Fair Value Gaps / Inverse FVG ---------------------------------
    # `fvg_detector`/`fvgs`/`unfilled`/`inverse_fvgs` are already computed
    # earlier (moved up so the Order Block Engine could use them for FVG
    # confluence) - this section just builds the report cards from them.
    if unfilled:
        nearest = min(unfilled, key=lambda f: min(abs(f.top - current_price), abs(f.bottom - current_price)))
        gap_size = nearest.top - nearest.bottom
        fvg_strength = round(gap_size / atr * 40) if has_atr else 50
        bullish_count = sum(1 for f in unfilled if f.type == FVGType.BULLISH)
        bearish_count = len(unfilled) - bullish_count
        sections.append(_section(
            "Fair Value Gaps",
            f"{len(unfilled)} Unfilled ({bullish_count} Bullish / {bearish_count} Bearish)",
            fvg_strength, 60,
            f"Nearest gap: {nearest.bottom:.8g} - {nearest.top:.8g}.",
        ))
    else:
        sections.append(_section("Fair Value Gaps", "No Unfilled Gaps", 0, 30))

    if inverse_fvgs:
        latest_inv = inverse_fvgs[-1]
        sections.append(_section(
            "Inverse FVG",
            f"{len(inverse_fvgs)} Inverse Gap(s) - Now {latest_inv.type.value.capitalize()}",
            60, 55,
            "A filled FVG that flipped polarity - the old gap now acts as the opposite kind of level.",
        ))
    else:
        sections.append(_section("Inverse FVG", "None Detected", 0, 20))

    # --- Volatility / Volume -------------------------------------------
    if has_atr and current_price:
        atr_pct = atr / current_price * 100
        vol_regime = _classify_volatility(conf.atr)
        sections.append(_section(
            "ATR", f"{vol_regime.capitalize()} Volatility", min(100, round(atr_pct * 20)), 80,
            f"ATR is {atr_pct:.2f}% of price.",
        ))
    else:
        sections.append(_section("ATR", "Unavailable", 0, 0))

    rel_vol = latest.get("relative_volume") if latest else None
    if rel_vol is not None:
        rv_status = "Above Average" if rel_vol > 1.2 else "Below Average" if rel_vol < 0.8 else "Average"
        sections.append(_section(
            "Relative Volume", rv_status, min(100, round(abs(rel_vol - 1) * 100)), 65,
            f"Current volume is {rel_vol:.2f}x its 20-period average.",
        ))
    else:
        sections.append(_section("Relative Volume", "Unavailable", 0, 0))

    obv_rising = latest.get("obv_rising") if latest else None
    if obv_rising is None:
        sections.append(_section("OBV", "Unavailable", 0, 0))
    else:
        sections.append(_section(
            "OBV", "Rising (Accumulation)" if obv_rising else "Falling (Distribution)", 60, 60,
        ))

    has_taker_data = "taker_buy_volume" in df.columns and df["taker_buy_volume"].fillna(0).abs().sum() > 0
    cvd_rising = latest.get("cvd_rising") if latest else None
    if not has_taker_data or cvd_rising is None:
        sections.append(_section(
            "Volume Delta", "Not Available", 0, 0,
            "Requires per-candle taker-buy-volume (available for Binance pairs, not DEX/GeckoTerminal candles).",
        ))
    else:
        sections.append(_section(
            "Volume Delta", "Buy-Dominant (Net Aggressive Buying)" if cvd_rising else "Sell-Dominant (Net Aggressive Selling)",
            60, 65,
        ))

    # --- Multi-Timeframe Analysis ---------------------------------------
    mtf_labels = ["5m", "15m", "1h", "4h", "1D"]
    mtf_dfs = mtf_dfs or {}
    tf_reads: Dict[str, str] = {}
    for label in mtf_labels:
        tf_df = mtf_dfs.get(label)
        if tf_df is None or tf_df.empty:
            tf_reads[label] = "N/A"
            continue
        trend = ema_trend_from_df(tf_df, MTF_TREND_MIN_SEPARATION)
        tf_reads[label] = {"up": "Bullish", "down": "Bearish", "neutral": "Neutral"}[trend]

    available_reads = [v for v in tf_reads.values() if v != "N/A"]
    bullish_count = sum(1 for v in available_reads if v == "Bullish")
    bearish_count = sum(1 for v in available_reads if v == "Bearish")
    if available_reads:
        majority = max(bullish_count, bearish_count)
        mtf_status = (
            f"{bullish_count}/{len(available_reads)} Bullish" if bullish_count >= bearish_count
            else f"{bearish_count}/{len(available_reads)} Bearish"
        )
        mtf_strength = round(majority / len(available_reads) * 100)
        mtf_confidence = round(len(available_reads) / len(mtf_labels) * 100)
    else:
        mtf_status, mtf_strength, mtf_confidence = "Unavailable", 0, 0

    detail = " | ".join(f"{label}: {tf_reads[label]}" for label in mtf_labels)
    sections.append(_section("Multi-Timeframe Analysis", mtf_status, mtf_strength, mtf_confidence, detail))

    # --- AI Technical Summary --------------------------------------------
    scored = [s for s in sections if s["confidence"] > 0]
    technical_score = (
        round(sum(s["strength"] * s["confidence"] / 100 for s in scored) / len(scored)) if scored else 0
    )

    latest_choch = latest_of_type(breaks_default, "CHoCH")
    latest_bos = latest_of_type(breaks_default, "BOS")
    driver = latest_choch or latest_bos
    trend_label = driver.direction.capitalize() if driver else "Neutral"

    macd_bullish = bool((latest.get("macd_hist") or 0) > 0) if latest else False
    rsi = latest.get("rsi") if latest else None
    if rsi is not None and not pd.isna(rsi):
        momentum_label = "Bullish" if (macd_bullish and rsi > 50) else "Bearish" if (not macd_bullish and rsi < 50) else "Mixed"
    else:
        momentum_label = "Unknown"

    internal_dir = latest_of_type(breaks_internal, "BOS")
    external_dir = latest_of_type(breaks_external, "BOS")
    if internal_dir and external_dir and internal_dir.direction == external_dir.direction:
        structure_label = f"{external_dir.direction.capitalize()} - Internal & External Aligned"
    elif external_dir:
        structure_label = f"{external_dir.direction.capitalize()} (External Structure)"
    else:
        structure_label = "Ranging / No Clear Structure"

    trade_levels = trade_levels or {}
    entry = trade_levels.get("entry")
    best_entry_zone = None
    if entry is not None and has_atr:
        zone_source = None
        if driver and driver.direction == "bullish" and bullish_ob and not bullish_ob.mitigated:
            zone_source = (bullish_ob.low, bullish_ob.high)
        elif driver and driver.direction == "bearish" and bearish_ob and not bearish_ob.mitigated:
            zone_source = (bearish_ob.low, bearish_ob.high)
        elif unfilled:
            nearest = min(unfilled, key=lambda f: min(abs(f.top - current_price), abs(f.bottom - current_price)))
            zone_source = (nearest.bottom, nearest.top)
        low, high = zone_source if zone_source else (entry - 0.5 * atr, entry + 0.5 * atr)
        best_entry_zone = f"{min(low, high):.8g} - {max(low, high):.8g}"

    if technical_score >= 75 and trend_label == "Bullish":
        verdict = "STRONG BULLISH"
    elif technical_score >= 55 and trend_label == "Bullish":
        verdict = "BULLISH"
    elif technical_score >= 75 and trend_label == "Bearish":
        verdict = "STRONG BEARISH"
    elif technical_score >= 55 and trend_label == "Bearish":
        verdict = "BEARISH"
    else:
        verdict = "NEUTRAL"

    ai_summary = {
        "technical_score": technical_score,
        "trend": trend_label,
        "momentum": momentum_label,
        "structure": structure_label,
        "best_entry_zone": best_entry_zone,
        "stop_loss": trade_levels.get("stop_loss"),
        "take_profit": trade_levels.get("take_profit"),
        "risk_reward": trade_levels.get("risk_reward"),
        "verdict": verdict,
    }

    return {"sections": sections, "ai_summary": ai_summary}
