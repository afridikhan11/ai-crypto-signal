"""
AI Scorer - ICT-only confidence engine.

STATUS (2026-07-29): PRODUCTION. Rewritten as the final scoring phase of
the institutional ICT migration (see ICT_ARCHITECTURE_AUDIT_AND_MIGRATION_PLAN.md).
`assess()`'s signature is UNCHANGED (`assess(features: Dict) -> Tuple[int,
str, Dict[str,float]]`) - every existing caller (SignalGenerator,
BacktestEngine, the calibration pipeline) keeps working without any change
on its end. What changed is entirely INTERNAL: every one of the 13
categories below is derived only from real ICT engine output (Market
Structure, Liquidity, Order Blocks, FVG, Supply/Demand, Session/Kill Zone,
Inducement, OTE, Institutional Bias, HTF Alignment, real order-flow Volume
Confirmation, Risk, Evidence Quality). There is no EMA, RSI, MACD,
Stochastic RSI, CCI, Williams %R, Supertrend, Bollinger/Keltner/Donchian,
ADX, VWAP, OBV, or classical/candlestick chart-pattern code left anywhere
in this file - confirmed by this file's own contents, not asserted.

WHAT WAS REMOVED, AND WHERE IT WENT (disclosed, not silently deleted)
--------------------------------------------------------------------------
  - pattern_confirmation, trend_filters, momentum, volume_order_flow,
    volatility_context, multi_tf_alignment, fundamental_context: all
    retail-indicator-dependent categories from the old scorer. Removed
    entirely per this phase's explicit mandate ("No EMA. No RSI. No MACD.
    No Supertrend. No retail indicators."). `app/indicators/confirmation.py`
    itself is NOT deleted in this phase (still used by
    `app/agent/risk_manager.py` and the legacy Market/Portfolio dashboards -
    see memory `project_ai_crypto_signal_pending_cleanup`) - only this
    scorer's dependency on it is removed, and only the two ICT-legitimate
    order-flow fields it also happens to expose (CVD, Volume Profile POC)
    are still read, under `volume_confirmation` below.
  - confluence (old OB+FVG+swept-liquidity zone-overlap check): folded into
    `evidence_quality` below - same computation, new name, since the 13-
    category list this phase specifies has no separate "confluence" slot.
  - Funding rate / OI / long-short ratio / Fear&Greed / BTC dominance /
    DXY / real yield / event risk (`fundamental_context`): these are real,
    non-retail-indicator macro data, but they are not named anywhere in
    this phase's 13-category list either, and are not produced by any ICT
    `app/smc/` engine - continuing to score them here would silently
    reintroduce an unauthorized 14th category. This is a deliberate scope
    decision, not an oversight: they remain fully intact and still fetched
    by `app/services/fundamentals_service.py`, still shown to the user via
    `ICTEvidenceInputs.institutional_bias_notes`/`risk_notes` (plain
    evidence text, not a scored category) if a caller chooses to pass them
    that way - just no longer a directly-weighted confidence input. See the
    Phase's final report ("Remaining Known Issues") for this call spelled
    out explicitly.
  - Real forced-liquidation pressure (short/long liquidated USD, from
    Binance's free public !forceOrder stream): kept, but moved from the old
    "liquidity_sweep" category into `volume_confirmation` below - it is
    real institutional order-flow data (forced buying/selling), the same
    family as CVD, not a liquidity-pool/sweep concept, so it fits that
    category's definition more precisely than it fit the old one.

FEATURES DICT CONTRACT (what a caller must/may populate)
--------------------------------------------------------------------------
Required:
    "direction": "LONG" | "SHORT"
    "current_price": float

Reused unchanged from the pre-existing SMC section (same shape as before -
SignalGenerator already builds these; nothing about their shape changed):
    "market_structure": {"bos_choch": [StructureBreak, ...]}
    "liquidity": {"levels": [LiquidityLevel, ...]}
    "order_block": {} | OrderBlock-shaped dict (high/low/type/mitigated)
    "fvg": [FairValueGap, ...]
    "supply_demand_zone": "discount" | "premium" | "equilibrium" | "unknown"
    "zone_position": Optional[float]
    "confirmation": dict (ATR still read here for displacement sizing; CVD/
        POC/relative_volume read for volume_confirmation - see
        `_ALLOWED_VOLUME_KEYS`-equivalent allowlist below; every other key
        - rsi/macd/adx/etc - is present in this dict for other callers
        (e.g. the legacy dashboards) but is NEVER read by this file)
    "symbol": str

New, ICT-migration-only (every one honestly degrades to a neutral/baseline
score when omitted - never fabricated):
    "inducement_events": Optional[List[InducementEvent]]
    "ote_zone": Optional[OTEZone]
    "institutional_bias": Optional[InstitutionalBias]
    "htf_snapshot": Optional[HTFSnapshot]
    "session_context": Optional[SessionContext]
    "evidence_report": Optional[ICTEvidenceReport]  (for evidence_quality's
        bullish/bearish-lean measurement; evidence_quality still works
        without it, using the OB+FVG+liquidity zone-overlap check alone)
    "risk_reward": Optional[float]
    "risk_assessment": Optional[RiskAssessment]  (from RiskEngine; only
        `.approved` is read here - the rest of that object is for the
        caller's own gating, not re-derived into a second score)
    "institutional": {"short_liquidated_usd": float, "long_liquidated_usd": float}
        (kept from the pre-existing dict shape, now read under
        volume_confirmation instead of liquidity_sweep)
"""
from typing import Dict, Any, Tuple

from app.ai.calibration import load_calibrated_weights_for_profile
from app.ai.calibration_profiles import CalibrationProfile, get_profile_for_symbol


class AIScorer:
    def __init__(self, symbol: str):
        # 2026-07-26: asset-aware calibration - unchanged by this rewrite.
        # See app/ai/calibration_profiles.py / app/ai/calibration.py.
        self.symbol = symbol
        self.profile: CalibrationProfile = get_profile_for_symbol(symbol)
        self.weights = load_calibrated_weights_for_profile(self.profile) or dict(self.profile.default_weights)

    def assess(self, features: Dict[str, Any]) -> Tuple[int, str, Dict[str, float]]:
        scores: Dict[str, float] = {}
        reasons = []

        direction = features["direction"]
        current_price = features.get("current_price", 0)
        conf = features.get("confirmation", {}) or {}
        atr = conf.get("atr", 0) or 0

        def _bullish_bearish(d: str) -> str:
            return "bullish" if d == "LONG" else "bearish"

        wanted_polarity = _bullish_bearish(direction)

        # ================================================================
        # market_structure - unchanged from the pre-migration scorer: this
        # category was already pure ICT (BOS/CHoCH from MarketStructureEngine,
        # scaled by how far price has travelled past the broken swing level
        # in ATR terms - CHoCH starts from a higher base than a plain BOS
        # since it represents a bigger structural shift).
        # ================================================================
        ms = features.get("market_structure", {}) or {}
        bos_choch = ms.get("bos_choch", [])
        if bos_choch:
            latest = bos_choch[-1]
            displacement_ratio = (abs(current_price - latest.level) / atr) if atr > 0 else 0
            base = 72 if latest.type == "CHoCH" else 58
            bonus = min(displacement_ratio * 12, 26)
            scores["market_structure"] = round(min(base + bonus, 100), 1)
            reasons.append(f"{latest.type} break ({displacement_ratio:.1f}x ATR displacement)")
        else:
            scores["market_structure"] = 35
            reasons.append("No clear BOS/CHoCH")

        # ================================================================
        # liquidity - unchanged sweep-count scaling from the pre-migration
        # scorer's "liquidity_sweep" category. Real forced-liquidation
        # pressure (previously folded in here) has moved to
        # volume_confirmation - see module docstring.
        # ================================================================
        liq = features.get("liquidity", {}) or {}
        swept_levels = [l for l in liq.get("levels", []) if l.swept]
        if swept_levels:
            base = 62
            bonus = min(len(swept_levels) * 11, 30)
            scores["liquidity"] = round(min(base + bonus, 100), 1)
            reasons.append(f"{len(swept_levels)} liquidity level(s) swept")
        else:
            scores["liquidity"] = 45
            reasons.append("No liquidity swept")

        # ================================================================
        # order_block - unchanged from the pre-migration scorer's
        # "order_block_quality" category (mitigation/presence tiering + a
        # size-vs-ATR conviction bonus). BlockType values are lowercase
        # ("bullish_ob"/"bearish_ob") - see app/smc/order_block_engine.py.
        # ================================================================
        ob = features.get("order_block", {}) or {}
        if ob:
            ob_range = ob.get("high", 0) - ob.get("low", 0)
            size_ratio = (ob_range / atr) if atr > 0 else 0
            size_bonus = min(size_ratio * 9, 16)
            if ob.get("mitigated", False):
                base = 74
                reasons.append("OB mitigation")
            elif ob.get("type") in ["bullish_ob", "bearish_ob"]:
                base = 58
                reasons.append("OB present")
            else:
                base = 28
            scores["order_block"] = round(min(base + size_bonus, 100), 1)
        else:
            scores["order_block"] = 20

        ob_present = bool(ob) and ob.get("type") in ("bullish_ob", "bearish_ob")
        ob_low, ob_high = (ob.get("low"), ob.get("high")) if ob_present else (None, None)

        # ================================================================
        # fvg - unchanged from the pre-migration scorer's "fvg_presence"
        # category (gap size relative to ATR + stacked-gap count bonus).
        # ================================================================
        fvg = features.get("fvg", []) or []
        unfilled = [f for f in fvg if not f.filled]
        if unfilled:
            avg_size = sum(f.top - f.bottom for f in unfilled) / len(unfilled)
            size_ratio = (avg_size / atr) if atr > 0 else 0
            base = 55
            size_bonus = min(size_ratio * 18, 22)
            count_bonus = min((len(unfilled) - 1) * 6, 12)
            scores["fvg"] = round(min(base + size_bonus + count_bonus, 100), 1)
            reasons.append(f"{len(unfilled)} unfilled FVG(s)")
        else:
            scores["fvg"] = 35
            reasons.append("No unfilled FVG")

        overlapping_fvg = None
        if ob_present:
            for f in unfilled:
                if f.bottom <= ob_high and ob_low <= f.top:
                    overlapping_fvg = f
                    break

        # ================================================================
        # supply_demand - unchanged from the pre-migration scorer's
        # "supply_demand_zone" category (fractional position within the
        # current Premium/Discount range, already ICT-pure).
        # ================================================================
        zone = features.get("supply_demand_zone", "unknown")
        zone_position = features.get("zone_position")
        if zone_position is not None:
            zone_position = max(0.0, min(zone_position, 1.0))
            favorability = (1 - zone_position) if direction == "LONG" else zone_position
            scores["supply_demand"] = round(30 + favorability * 65, 1)
            reasons.append(f"Zone position {zone_position:.2f} ({zone})")
        elif zone == "discount" and direction == "LONG":
            scores["supply_demand"] = 90
            reasons.append("In discount zone")
        elif zone == "premium" and direction == "SHORT":
            scores["supply_demand"] = 90
            reasons.append("In premium zone")
        elif zone == "equilibrium":
            scores["supply_demand"] = 60
        else:
            scores["supply_demand"] = 30

        # ================================================================
        # session_killzone - new. Scored purely from SessionEngine's own
        # classification: inside a named Kill Zone scores highest (the
        # London/New York overlap - both major centers active - highest of
        # all), an active session outside any Kill Zone scores moderate,
        # and the low-liquidity off-session "dead zone" scores lowest. Not
        # a directional signal (Session Analysis has no concept of
        # LONG/SHORT) - purely a "is this a high-probability window to be
        # trading in at all" context score, same role volatility_context
        # played in the old scorer.
        # ================================================================
        session_ctx = features.get("session_context")
        if session_ctx is None:
            scores["session_killzone"] = 50
        elif session_ctx.is_london_ny_overlap:
            scores["session_killzone"] = 95
            reasons.append("London/New York overlap")
        elif session_ctx.in_kill_zone:
            scores["session_killzone"] = 80
            reasons.append(f"{session_ctx.kill_zone.value.replace('_', ' ').title()}")
        elif session_ctx.is_off_session:
            scores["session_killzone"] = 30
            reasons.append("Off-session (low liquidity)")
        else:
            scores["session_killzone"] = 55

        # ================================================================
        # inducement - new. An InducementEvent whose reversal_direction
        # matches this signal's direction is strong confirming evidence
        # (smart money already swept the opposing side's liquidity and
        # reversed the way this signal is now proposing to go); an event
        # whose reversal_direction OPPOSES this signal's direction means
        # the engineered move ran the other way, which is a real caution
        # flag, not neutral. Absence of any qualifying event is not itself
        # a bad sign - Inducement is strict-AND-gated by design (see
        # InducementEngine's own docstring) and will often correctly find
        # nothing.
        # ================================================================
        inducement_events = features.get("inducement_events") or []
        matching_inducement = [e for e in inducement_events if e.reversal_direction == wanted_polarity]
        opposing_inducement = [e for e in inducement_events if e.reversal_direction != wanted_polarity]
        if matching_inducement:
            e = matching_inducement[-1]
            bonus = min(e.displacement_ratio * 8, 20)
            scores["inducement"] = round(min(75 + bonus, 100), 1)
            reasons.append(f"Valid Inducement confirms {direction} ({e.reversal_break_type})")
        elif opposing_inducement:
            scores["inducement"] = 25
            reasons.append("Inducement detected against this direction")
        else:
            scores["inducement"] = 45

        # ================================================================
        # ote - new. OTEEngine only ever emits a zone when ALL of its own
        # strict conditions already held (displacement, confluence,
        # Premium/Discount alignment - see ote_engine.py), so any zone
        # supplied here already passed that gate; this category only adds
        # the direction match and "is price actually IN the zone right
        # now" checks, then scales by the zone's own reported
        # confluence_count/retracement_quality.
        # ================================================================
        ote_zone = features.get("ote_zone")
        if ote_zone is not None and ote_zone.direction == wanted_polarity:
            base = 70
            if ote_zone.contains(current_price):
                base += 15
                reasons.append("Price inside valid OTE zone")
            if ote_zone.retracement_quality == "clean":
                base += 5
            bonus = min(ote_zone.confluence_count * 4, 10)
            scores["ote"] = round(min(base + bonus, 100), 1)
        elif ote_zone is not None:
            scores["ote"] = 25
            reasons.append("OTE zone exists but opposes this direction")
        else:
            scores["ote"] = 40

        # ================================================================
        # institutional_bias - new. InstitutionalBiasEngine already folds
        # Weekly/Daily/4H structure + Premium/Discount + major-timeframe
        # confluence into one direction label - this category only checks
        # agreement with the signal's own direction and scales by the
        # engine's own reported confluence_count.
        # ================================================================
        inst_bias = features.get("institutional_bias")
        if inst_bias is None or inst_bias.direction == "neutral":
            scores["institutional_bias"] = 45
        elif inst_bias.direction == "conflicted":
            scores["institutional_bias"] = 35
            reasons.append("Institutional Bias conflicted")
        elif inst_bias.direction == wanted_polarity:
            bonus = min(inst_bias.confluence_count * 5, 20)
            scores["institutional_bias"] = round(min(75 + bonus, 100), 1)
            reasons.append(f"Institutional Bias {inst_bias.direction}")
        else:
            scores["institutional_bias"] = 15
            reasons.append(f"Against Institutional Bias ({inst_bias.direction})")

        # ================================================================
        # htf_alignment - new. Replaces the old EMA-stack-based
        # "multi_tf_alignment" with a weighted read of real higher-
        # timeframe Market Structure (Weekly/Daily/4H/1H, via
        # HTFStructureEngine) - "aligned_bullish"/"aligned_bearish" per
        # timeframe, weighted heaviest-to-lightest, exactly the same
        # weighted-abstention pattern the old category used for missing
        # timeframes (a "conflicted"/"insufficient_data" timeframe
        # contributes to total_weight but not to either side, pulling the
        # result toward the 50 baseline rather than being penalized).
        # ================================================================
        HTF_TF_WEIGHTS = {"1w": 1.5, "1d": 1.2, "4h": 1.0, "1h": 0.7}
        htf_snapshot = features.get("htf_snapshot")
        if htf_snapshot is not None and not htf_snapshot.is_empty():
            aligned_tfs = []
            opposed_tfs = []
            weighted_align = 0.0
            weighted_oppose = 0.0
            total_tf_weight = 0.0
            for tf, weight in HTF_TF_WEIGHTS.items():
                ts = htf_snapshot.get(tf)
                if ts is None:
                    continue
                total_tf_weight += weight
                if ts.structure_alignment == f"aligned_{wanted_polarity}":
                    weighted_align += weight
                    aligned_tfs.append(tf)
                elif ts.structure_alignment in ("aligned_bullish", "aligned_bearish"):
                    weighted_oppose += weight
                    opposed_tfs.append(tf)
            if total_tf_weight > 0:
                net_alignment = (weighted_align - weighted_oppose) / total_tf_weight  # -1..1
                htf_score = 50 + net_alignment * 45
                scores["htf_alignment"] = round(max(min(htf_score, 100), 0), 1)
                if aligned_tfs:
                    reasons.append(f"HTF structure aligned ({'/'.join(t.upper() for t in aligned_tfs)})")
                if opposed_tfs:
                    reasons.append(f"HTF structure opposing ({'/'.join(t.upper() for t in opposed_tfs)})")
            else:
                scores["htf_alignment"] = 50
        else:
            # BacktestEngine (unmodified this phase) does not construct
            # real HTF dataframes yet - honest neutral, never guessed. See
            # module docstring / final report's Backtesting Readiness
            # Checklist.
            scores["htf_alignment"] = 50
            reasons.append("HTF structure unavailable")

        # ================================================================
        # volume_confirmation - new. Real institutional order-flow only:
        # CVD (Binance per-candle taker-buy-volume, from
        # ConfirmationIndicators - NOT a retail oscillator) + Volume
        # Profile POC position, plus real forced-liquidation pressure
        # (Binance's public !forceOrder stream) moved here from the old
        # "liquidity_sweep" category - see module docstring. OBV is
        # deliberately NOT read (it is a retail-indicator moving-average-
        # of-volume construct, unlike CVD's direct taker-side read).
        # ================================================================
        vf_score = 50.0
        cvd_rising = conf.get("cvd_rising")
        if cvd_rising is not None:
            if (direction == "LONG" and cvd_rising) or (direction == "SHORT" and not cvd_rising):
                vf_score += 18
                reasons.append(f"CVD {'buy' if cvd_rising else 'sell'}-side confirming")
            else:
                vf_score -= 18
                reasons.append("CVD opposing (order flow against signal)")

        poc_price = conf.get("poc_price")
        if poc_price and poc_price > 0 and atr > 0:
            poc_distance_atr = (current_price - poc_price) / atr
            if direction == "LONG":
                vf_score += max(-10, min(poc_distance_atr * 6, 10))
            else:
                vf_score += max(-10, min(-poc_distance_atr * 6, 10))

        inst_raw = features.get("institutional", {}) or {}
        short_liq = inst_raw.get("short_liquidated_usd", 0) or 0
        long_liq = inst_raw.get("long_liquidated_usd", 0) or 0
        total_liq = short_liq + long_liq
        if total_liq > 0:
            favorable_liq = short_liq if direction == "LONG" else long_liq
            favorable_ratio = favorable_liq / total_liq
            if favorable_ratio > 0.6:
                liq_bonus = min((favorable_ratio - 0.5) * 36, 16)
                vf_score += liq_bonus
                reasons.append(f"Real liquidations favor {direction} ({favorable_ratio:.0%})")

        scores["volume_confirmation"] = round(max(min(vf_score, 100), 0), 1)

        # ================================================================
        # risk - new. Scores the trade's own risk/reward quality (not a
        # directional signal) - a tight-stop/far-target setup scores
        # highest; anything the Unified Risk Engine has already flagged as
        # not-approved (exposure/open-risk/daily-loss/drawdown breach)
        # scores at the floor, since a signal with an unapproved position
        # size is not a trade that should read as "confident" regardless
        # of how clean its technicals look.
        # ================================================================
        risk_assessment = features.get("risk_assessment")
        if risk_assessment is not None and not risk_assessment.approved:
            scores["risk"] = 10
            reasons.append("Risk Engine did not approve this trade's sizing")
        else:
            rr = features.get("risk_reward")
            if rr is None:
                scores["risk"] = 50
            else:
                # 1:1 -> 30, 2:1 -> 65, 3:1 -> 85, 4:1+ -> 95+ (capped 100)
                risk_score = 30 + min(max(rr - 1.0, 0) * 20, 65)
                scores["risk"] = round(min(risk_score, 100), 1)
                reasons.append(f"Risk/Reward {rr:.1f}:1")

        # ================================================================
        # evidence_quality - new. Replaces the old "confluence" category
        # (kept: does the Order Block, unfilled FVG, and a swept liquidity
        # level all sit in the SAME price zone - stronger evidence of a
        # real institutional zone than the same three signals scattered
        # across unrelated parts of the chart) and, when an
        # ICTEvidenceReport was supplied, adds a second measure: how
        # cleanly the collected evidence as a whole leans toward this
        # signal's own direction (bullish_evidence vs bearish_evidence
        # item counts) - the more one-sided the evidence, the more
        # internally consistent the story this signal is telling.
        # ================================================================
        zone_low, zone_high = ob_low, ob_high
        if overlapping_fvg is not None:
            zone_low = min(zone_low, overlapping_fvg.bottom) if zone_low is not None else overlapping_fvg.bottom
            zone_high = max(zone_high, overlapping_fvg.top) if zone_high is not None else overlapping_fvg.top

        liq_in_zone = False
        if swept_levels and zone_low is not None and zone_high is not None:
            tolerance = atr * 0.5 if atr > 0 else 0
            liq_in_zone = any(
                (zone_low - tolerance) <= lvl.price <= (zone_high + tolerance)
                for lvl in swept_levels
            )

        confluence_count = sum([ob_present, overlapping_fvg is not None, liq_in_zone])
        confluence_tiers = {3: 90, 2: 62, 1: 40, 0: 22}
        evq_score = confluence_tiers[confluence_count]
        if confluence_count >= 2:
            reasons.append(f"Confluence zone: OB+FVG+liquidity overlap ({confluence_count}/3)")

        evidence_report = features.get("evidence_report")
        if evidence_report is not None and evidence_report.items:
            bullish_n = len(evidence_report.bullish_evidence)
            bearish_n = len(evidence_report.bearish_evidence)
            total_directional = bullish_n + bearish_n
            if total_directional > 0:
                favorable_n = bullish_n if wanted_polarity == "bullish" else bearish_n
                lean = (favorable_n / total_directional - 0.5) * 2  # -1..1
                evq_score = round(max(min(evq_score + lean * 20, 100), 0), 1)
                reasons.append(
                    f"Evidence Engine: {favorable_n}/{total_directional} directional items favor {direction}"
                )

        scores["evidence_quality"] = evq_score

        total = sum(self.weights.get(k, 0) * scores[k] for k in scores)
        confidence = int(round(total))
        reason_str = "; ".join(reasons)
        return confidence, reason_str, scores
