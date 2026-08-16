"""
Signal Generator - THE Universal ICT Pipeline.

STATUS (2026-07-30): PRODUCTION. This is the platform's single strategy.
Every supported asset - crypto, gold, silver, oil, and any asset class
added later - runs through this exact pipeline. There is no per-asset
strategy, no per-asset scanner and no per-asset AI anywhere in this
codebase.

Orchestrates every ICT engine in `app/smc/` plus the AI Confidence Engine
(`app/ai/scorer.py`) and the AI Decision Engine
(`app/ai/ict_decision_engine.py`) into one fully-explained decision per
candle.

WHAT VARIES PER ASSET, AND WHAT NEVER DOES
--------------------------------------------------------------------------
Everything about HOW a trade is found is fixed here and shared by all
assets. The ONLY per-asset variation enters through the `AssetProfile`
(`app/assets/asset_profile.py`):

  - session / kill-zone windows      -> SessionEngine is built from them
  - optional ICT filters             -> can make these shared gates
                                        STRICTER for a market, never
                                        different and never looser
  - scoring config (`.calibration`)  -> the existing CalibrationProfile:
                                        confidence/RR gates, ATR multiples

Adding Forex, Indices or Stocks therefore means adding an `AssetProfile` -
never a new pipeline.

NO RETAIL STRATEGY CODE IN THIS PATH
--------------------------------------------------------------------------
This module imports nothing from `app/legacy/`. Market-data primitives
come from `app.smc.order_flow.OrderFlowMetrics` (ATR, CVD, Volume Profile
POC, relative volume computed from raw OHLCV with pandas/numpy) rather
than from the retail `ta`-backed indicator suite the pipeline used before
2026-07-30. `tests/test_legacy_isolation.py` enforces this mechanically.

TWO PUBLIC ENTRY POINTS
--------------------------------------------------------------------------
  - `generate_decision(...) -> TradeDecision`
        The primary entry point. ALWAYS returns a decision - including a
        fully-explained `NO_TRADE` carrying every blocking gate, the
        evidence that was found, and the ICT components that were missing.
        Nothing is ever silently dropped.

  - `generate(...) -> Optional[Dict]`
        The pre-existing signal-dict entry point, byte-for-byte
        signature-compatible with `app/backtest/engine.py`'s unmodified
        call. Returns the signal dict on a tradeable decision, `None`
        otherwise - a thin wrapper over the same single evaluation path,
        NOT a second implementation.

Both share one `_evaluate()` pass, so a decision and its signal dict can
never disagree.

GATE EVALUATION
--------------------------------------------------------------------------
Every hard gate (min confidence, confirmation present, HTF opposition,
min risk:reward, entry validation) is now evaluated on EVERY run and its
outcome recorded, rather than short-circuiting on the first failure. The
set of signals that fire is unchanged - a trade still requires all gates
to pass - but a rejection now reports every reason it failed instead of
only the first one encountered, which is what makes `why_no_trade`
actually actionable.
"""
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from loguru import logger

from app.smc.market_structure_engine import MarketStructureEngine
from app.smc.liquidity_engine import LiquidityEngine, LiquidityType
from app.smc.order_block_engine import OrderBlockEngine
from app.smc.fvg import FVGDetector
from app.smc.supply_demand_engine import SupplyDemandEngine
from app.smc.session_engine import SessionEngine
from app.smc.inducement_engine import InducementEngine
from app.smc.ote_engine import OTEEngine
from app.smc.htf_structure_engine import HTFStructureEngine
from app.smc.institutional_bias_engine import InstitutionalBiasEngine
from app.smc.latest_ict_confluence import LatestICTConfluenceEngine
from app.strategy.entry_engine import EntryEngine
from app.strategy.entry_validation_engine import EntryValidationEngine
from app.strategy.exit_engine import ExitEngine
from app.ai.evidence_engine import ICTEvidenceEngine, ICTEvidenceInputs
from app.ai.ict_decision_engine import (
    BlockingReason,
    DecisionInputs,
    DecisionType,
    ICTDecisionEngine,
    RejectionGate,
    TradeDecision,
)
from app.smc.order_flow import OrderFlowMetrics
from app.ai.scorer import AIScorer
from app.ai.calibration_profiles import CalibrationProfile
from app.assets.asset_profile import AssetProfile, get_asset_profile
from app.models.signal import Direction
from app.services.trading_settings import is_ict_pending_entry


def aligned_confluence_bonus(signals, direction, weights, cap) -> float:
    """Confidence points from proven latest-ICT engines that agree with the
    trade direction.

    Sums each engine's per-asset weight (`weights`, from the symbol's
    CalibrationProfile - populated only for engines whose forward-return edge
    was proven on that asset class in the matrix backtest) for every signal
    whose bias equals `direction`, then bounds the total by `cap`.

    Pure and total: an empty weight map (an asset with no measured edge) or no
    signals always returns 0.0, so those paths are byte-for-byte unchanged.
    """
    if not weights or not signals:
        return 0.0
    bonus = sum(
        weights.get(s.name, 0.0)
        for s in signals
        if s.bias == direction
    )
    return min(max(0.0, bonus), cap)


class SignalGenerator:
    def __init__(self, symbol: str, asset_profile: Optional[AssetProfile] = None):
        self.symbol = symbol
        self.ai_scorer = AIScorer(symbol)

        # 2026-07-30: one resolved AssetProfile carries BOTH this market's
        # characteristics (sessions, kill zones, ICT filters) and its
        # existing scoring configuration. `asset_profile` is injectable so
        # UniversalScanner can resolve it once per symbol and hand the same
        # object to the pipeline rather than re-resolving per candle.
        self.asset_profile: AssetProfile = asset_profile or get_asset_profile(symbol)

        # Every threshold that used to be a hardcoded class constant here
        # (MIN_CONFIDENCE, MIN_RISK_REWARD, the ATR multiples used to build
        # stop-loss/take-profit, the volatility regime thresholds) comes
        # from this symbol's CalibrationProfile - now reached through the
        # AssetProfile so there is a single object per symbol.
        self.profile: CalibrationProfile = self.asset_profile.calibration

        # Every engine below is stateless (see each module's own docstring:
        # two calls with the same inputs return equivalent output), so each
        # is constructed once per SignalGenerator instance and reused
        # across every call - one less allocation per candle during a
        # walk-forward backtest. InducementEngine is the one exception - it
        # needs this call's own `df`/`structure_breaks`, so it is built
        # inside `_evaluate()` (its documented usage pattern).
        #
        # SessionEngine is driven by THIS ASSET'S windows: crypto keeps the
        # standard three-session model, commodities use the London/New York
        # + COMEX-open model. Same engine, same ICT logic, different market
        # hours - which is exactly the Asset Profile contract.
        self.session_engine = SessionEngine(
            session_windows=self.asset_profile.session_windows,
            kill_zone_windows=self.asset_profile.kill_zone_windows,
            london_ny_overlap=self.asset_profile.london_ny_overlap,
        )
        self.ote_engine = OTEEngine()
        self.htf_structure_engine = HTFStructureEngine(external_pivot_window=5)
        self.institutional_bias_engine = InstitutionalBiasEngine()
        self.entry_engine = EntryEngine()
        self.entry_validation_engine = EntryValidationEngine(min_risk_reward=self.profile.min_risk_reward)
        self.exit_engine = ExitEngine()
        self.evidence_engine = ICTEvidenceEngine()
        self.decision_engine = ICTDecisionEngine()
        # Additive latest-ICT confluence layer (does not alter scoring; feeds
        # supplementary evidence notes only).
        self.latest_ict_confluence = LatestICTConfluenceEngine()

    # ------------------------------------------------------------------
    # Volatility regime
    # ------------------------------------------------------------------
    def _classify_volatility(self, atr_series: pd.Series) -> str:
        """
        Classify current volatility relative to THIS symbol's own recent ATR
        baseline, rather than a fixed global threshold - raw ATR% varies a
        lot between assets (e.g. a low-priced altcoin vs BTC), so comparing
        against the symbol's own recent history is more meaningful than an
        absolute cutoff.

        Lookback/high/low ratios come from self.profile - same values as
        the old hardcoded constants for every asset type until a profile is
        explicitly retuned.
        """
        recent = atr_series.dropna().iloc[-self.profile.volatility_lookback:]
        if len(recent) < 10:
            return "normal"
        current = recent.iloc[-1]
        baseline = recent.mean()
        if baseline <= 0 or pd.isna(current):
            return "normal"
        ratio = current / baseline
        if ratio > self.profile.volatility_high_ratio:
            return "high"
        elif ratio < self.profile.volatility_low_ratio:
            return "low"
        return "normal"

    # ------------------------------------------------------------------
    # ICT entry usability (2026-07-30)
    # ------------------------------------------------------------------
    @staticmethod
    def _is_usable_entry(
        direction: Direction, entry: Optional[float], stop_loss: float, take_profit: float
    ) -> bool:
        """
        Whether an ICT anchor price can actually be traded from.

        `EntryPlan.entry_price` is the MIDPOINT of a real zone (OTE, order
        block, FVG, supply/demand), so it carries no guarantee of sitting
        between the stop and the target - price may already have run through
        the zone by the time the structure break is confirmed. Trading from a
        midpoint on the wrong side of the stop would invert the risk
        distance and produce a meaningless (or negative) Risk:Reward, so the
        entry is only accepted when the ordering is genuinely tradeable.

        This is a validity test, not a filter: it never rejects a SETUP, it
        only decides whether the ICT anchor or the live price is used to
        price it. No gate, threshold or ICT rule is involved.
        """
        if entry is None:
            return False
        try:
            entry = float(entry)
        except (TypeError, ValueError):
            return False
        if entry != entry or entry <= 0:  # NaN or non-positive
            return False

        if direction == Direction.LONG:
            ordered = stop_loss < entry < take_profit
        else:
            ordered = take_profit < entry < stop_loss
        return ordered and abs(entry - stop_loss) > 0

    # ------------------------------------------------------------------
    # Early-exit decisions (before enough data exists to explain more)
    # ------------------------------------------------------------------
    def _abort(self, gate: RejectionGate, detail: str) -> TradeDecision:
        """Build a fully-formed NO_TRADE decision for a pre-analysis abort.

        Even here the decision is complete rather than a bare `None`: the
        ICT component checklist still runs (everything is honestly reported
        as missing, because nothing was detected yet), so the caller gets
        the same object shape on every path.
        """
        return self.decision_engine.decide(DecisionInputs(
            symbol=self.symbol,
            direction=None,
            confidence=0,
            blocking_reasons=[BlockingReason(gate=gate, detail=detail)],
            timeframe="15m",
        ))

    # ------------------------------------------------------------------
    # Public entry point 1 - full explainability
    # ------------------------------------------------------------------
    def evaluate(
        self,
        df: pd.DataFrame,
        funding_rate: float = 0.0,
        entry_price: Optional[float] = None,
        liquidation_pressure: Optional[Dict[str, float]] = None,
        fundamentals: Optional[Dict[str, Any]] = None,
        htf_dataframes: Optional[Dict[str, pd.DataFrame]] = None,
        reference_df: Optional[pd.DataFrame] = None,
        reference_symbol: str = "REFERENCE",
    ) -> Tuple[TradeDecision, Optional[Dict[str, Any]]]:
        """
        The primary entry point for live scanning: runs the pipeline ONCE
        and returns both the fully-explained decision and, when tradeable,
        the signal dict.

        `generate_decision()` and `generate()` exist for callers that want
        only one of the two (and for `BacktestEngine`'s fixed signature).
        Anything needing both - notably `UniversalScanner` - must use this
        method rather than calling those two in sequence, which would run
        the entire ICT pipeline twice per candle.

        Note this signature carries none of the EMA trend parameters: they
        were never consumed by the pipeline and exist on `generate()` only
        for backward compatibility (see that method's docstring).
        """
        return self._evaluate(
            df,
            btc_trend="neutral",
            funding_rate=funding_rate,
            htf_trend_1h="neutral",
            htf_trend_4h="neutral",
            entry_price=entry_price,
            liquidation_pressure=liquidation_pressure,
            fundamentals=fundamentals,
            htf_trend_1d="neutral",
            entry_trend_5m="neutral",
            htf_dataframes=htf_dataframes,
            reference_df=reference_df,
            reference_symbol=reference_symbol,
        )

    def generate_decision(
        self,
        df: pd.DataFrame,
        btc_trend: str = "neutral",
        funding_rate: float = 0.0,
        htf_trend_1h: str = "neutral",
        htf_trend_4h: str = "neutral",
        entry_price: Optional[float] = None,
        liquidation_pressure: Optional[Dict[str, float]] = None,
        fundamentals: Optional[Dict[str, Any]] = None,
        htf_trend_1d: str = "neutral",
        entry_trend_5m: str = "neutral",
        htf_dataframes: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> TradeDecision:
        """Always returns a `TradeDecision` - never None. See module docstring."""
        decision, _ = self._evaluate(
            df, btc_trend, funding_rate, htf_trend_1h, htf_trend_4h, entry_price,
            liquidation_pressure, fundamentals, htf_trend_1d, entry_trend_5m, htf_dataframes,
        )
        return decision

    # ------------------------------------------------------------------
    # Public entry point 2 - backward-compatible signal dict
    # ------------------------------------------------------------------
    def generate(
        self,
        df: pd.DataFrame,
        btc_trend: str,
        funding_rate: float,
        htf_trend_1h: str = "neutral",
        htf_trend_4h: str = "neutral",
        entry_price: Optional[float] = None,
        liquidation_pressure: Optional[Dict[str, float]] = None,
        fundamentals: Optional[Dict[str, Any]] = None,
        htf_trend_1d: str = "neutral",
        entry_trend_5m: str = "neutral",
        htf_dataframes: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        `df` is the PRIMARY structure timeframe (15m) - BOS/CHoCH, order
        blocks, FVG, and supply/demand zones are all detected from it,
        since SMC concepts represent institutional footprint that shows up
        on 15m+ charts, not 1m retail noise. `entry_price`, if given, is a
        more current live price (e.g. latest 1m close) used only for the
        actual entry/zone-classification price so the signal isn't stale
        by up to one 15m candle - it does NOT affect structure detection.

        `htf_dataframes` is an OPTIONAL `{"1w"/"1d"/"4h"/"1h": DataFrame}`
        bundle of REAL higher-timeframe OHLCV, used to build one
        `HTFSnapshot` that feeds the `institutional_bias`/`htf_alignment`
        scoring categories and the Entry Validation gate. It is the LAST
        parameter and always optional, so `BacktestEngine`'s existing
        unmodified call (positional args for the first 5 parameters, the
        rest by keyword - see `app/backtest/engine.py`) keeps working
        byte-for-byte unchanged; a caller that omits it honestly degrades
        those two categories to a neutral baseline rather than guessing.

        COMPATIBILITY-ONLY PARAMETERS: `btc_trend`, `htf_trend_1h`,
        `htf_trend_4h`, `htf_trend_1d` and `entry_trend_5m` are EMA20/50
        trend strings produced by `app/legacy/trend.py`. As of 2026-07-30
        the Universal ICT Pipeline no longer consumes them - higher-
        timeframe context now comes from real Market Structure via
        `HTFStructureEngine`/`InstitutionalBiasEngine`. They remain in the
        signature, in this exact position, solely so `app/backtest/engine.py`
        (which is explicitly out of scope for modification) keeps calling
        this method unchanged. They are accepted and ignored; nothing
        downstream reads them.

        Returns the signal dict, or None when the decision was NO_TRADE.
        Callers wanting the reason a signal was NOT produced should use
        `generate_decision()` instead - it never returns None.
        """
        _, signal = self._evaluate(
            df, btc_trend, funding_rate, htf_trend_1h, htf_trend_4h, entry_price,
            liquidation_pressure, fundamentals, htf_trend_1d, entry_trend_5m, htf_dataframes,
        )
        return signal

    # ------------------------------------------------------------------
    # The single evaluation path shared by both entry points.
    # ------------------------------------------------------------------
    def _evaluate(
        self,
        df: pd.DataFrame,
        btc_trend: str,
        funding_rate: float,
        htf_trend_1h: str,
        htf_trend_4h: str,
        entry_price: Optional[float],
        liquidation_pressure: Optional[Dict[str, float]],
        fundamentals: Optional[Dict[str, Any]],
        htf_trend_1d: str,
        entry_trend_5m: str,
        htf_dataframes: Optional[Dict[str, pd.DataFrame]],
        reference_df: Optional[pd.DataFrame] = None,
        reference_symbol: str = "REFERENCE",
    ) -> Tuple[TradeDecision, Optional[Dict[str, Any]]]:
        if len(df) < 50:
            return self._abort(
                RejectionGate.INSUFFICIENT_DATA,
                f"Only {len(df)} candles available - at least 50 are required for reliable structure detection.",
            ), None

        # ICT Market Structure Engine (2026-07-28 cutover - see
        # MARKET_STRUCTURE_CUTOVER_REPORT.md). external_pivot_window=5
        # preserves the exact swing granularity Liquidity/OrderBlocks/SL-TP
        # were all built against.
        ms = MarketStructureEngine(df, external_pivot_window=5)
        snapshot = ms.analyze()
        breaks = snapshot.external_breaks
        if not breaks:
            return self._abort(
                RejectionGate.NO_STRUCTURE_BREAK,
                "No confirmed BOS or CHoCH on the primary timeframe - there is no structural event to trade from.",
            ), None

        latest_break = breaks[-1]
        direction = Direction.LONG if latest_break.direction == "bullish" else Direction.SHORT

        current_price = entry_price if entry_price is not None else df["close"].iloc[-1]

        # FVG detection runs ahead of Order Block detection so the Order
        # Block Engine can check FVG confluence - FVGDetector itself is
        # unmodified, only the call order matters.
        fvg_detector = FVGDetector(df)
        fvgs = fvg_detector.detect_fvg()
        relevant_fvgs = [f for f in fvgs if not f.filled]

        # ICT Order Block Engine. `breaks` and `fvgs` are threaded through
        # so the engine can tag genuine BOS/CHoCH causation and FVG
        # confluence instead of returning a bare candle pair.
        ob_engine = OrderBlockEngine(df, structure_breaks=breaks, fvgs=fvgs)
        ob = (
            ob_engine.find_bullish_order_block() if direction == Direction.LONG
            else ob_engine.find_bearish_order_block()
        )
        ob_dict = None
        if ob:
            ob_dict = {
                "type": ob.type.value,
                "high": ob.high,
                "low": ob.low,
                "mitigated": ob.mitigated,
            }

        # ICT Liquidity Engine. Runs after Market Structure/FVG/Order Block
        # so sweeps can be tagged with real reversal_break_level /
        # has_ob_confluence / has_fvg_confluence.
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

        sd = SupplyDemandEngine(df)
        sd.calculate_recent_range()
        zone = sd.get_zone(current_price)
        zone_position = None
        if sd.range_high is not None and sd.range_low is not None and sd.range_high > sd.range_low:
            zone_position = (current_price - sd.range_low) / (sd.range_high - sd.range_low)

        # ICT-native market-data primitives (ATR, CVD, Volume Profile POC,
        # relative volume) computed from raw OHLCV. Replaces the retail
        # `ta`-backed indicator suite this pipeline used before 2026-07-30 -
        # see app/smc/order_flow.py.
        order_flow = OrderFlowMetrics(df)
        conf_latest = order_flow.get_latest()
        if conf_latest is None:
            logger.warning(f"{self.symbol}: Order-flow metrics unavailable (ATR NaN), skipping.")
            return self._abort(
                RejectionGate.INDICATOR_NAN,
                "Order-flow/ATR inputs are unavailable on the latest candle - refusing to score on incomplete data.",
            ), None

        atr = conf_latest["atr"]
        if pd.isna(atr) or atr <= 0:
            logger.warning(f"{self.symbol}: Invalid ATR value, skipping signal.")
            return self._abort(
                RejectionGate.INVALID_ATR,
                f"ATR is {atr} - cannot size a structure-anchored stop or target without a valid volatility read.",
            ), None

        volatility = self._classify_volatility(order_flow.atr)

        # ------------------------------------------------------------------
        # ICT interpretation layers - pure reads over the objects above.
        # ------------------------------------------------------------------
        session_ctx = self.session_engine.latest_context(df)
        inducement_events = InducementEngine(df, structure_breaks=breaks).detect(swept)

        ote_zone = self.ote_engine.detect(
            snapshot,
            order_blocks=[ob] if ob else None,
            fvgs=relevant_fvgs,
            liquidity_levels=all_levels,
            premium_discount_zone=zone,
            current_price=current_price,
        )

        htf_snapshot = self.htf_structure_engine.build_snapshot(htf_dataframes)
        institutional_bias = self.institutional_bias_engine.assess(
            htf_snapshot, premium_discount_zone=zone,
        )

        # ------------------------------------------------------------------
        # ICT ENTRY ACTIVATION (2026-07-30).
        #
        # The entry plan is built HERE, before SL/TP/Risk:Reward, rather
        # than after the decision as it previously was. Every input it
        # needs (`ote_zone`, `ob`, `relevant_fvgs`, `zone`, `current_price`)
        # is already computed above, so this is purely a reordering of an
        # existing call - EntryEngine itself is unmodified and no new zone
        # detection happens. Building it here is what lets the trade be
        # priced from the ICT anchor (OTE / Order Block / FVG / Supply-
        # Demand zone) instead of from wherever price happens to be when
        # the structure break was spotted.
        #
        # `market_entry_price` (the live price) is still used for
        # everything it was used for before: the stop/target CONSTRUCTION
        # below is unchanged and still anchored to structure and to the
        # live price, exactly as it was. Only which price the trade is
        # ENTERED at can change.
        # ------------------------------------------------------------------
        market_entry_price = current_price
        entry_plan = self.entry_engine.build(
            direction.value,
            current_price=market_entry_price,
            ote_zone=ote_zone,
            order_block=ob,
            fvg=(relevant_fvgs[-1] if relevant_fvgs else None),
            # `supply_demand_zone` is deliberately NOT passed: this
            # pipeline's `zone` is SupplyDemandEngine.get_zone()'s premium/
            # discount/equilibrium STRING, not the zone OBJECT with
            # .type/.top/.bottom that EntryEngine's supply-demand branch
            # needs. Passing it would silently never match and imply a
            # capability that does not exist here.
        )

        # ------------------------------------------------------------------
        # Structure-based SL/TP. Computed BEFORE scoring so the real
        # Risk:Reward can feed AIScorer's `risk` category. The stop is
        # anchored to real invalidation points (the structure swing, and
        # the order block boundary when one exists); the target prefers the
        # nearest real unswept liquidity level over an arbitrary ATR
        # multiple, since price gravitates toward resting liquidity. ATR
        # multiples (all profile-driven) remain the fallback.
        #
        # UNCHANGED by the pending-entry migration: every line below still
        # measures from `current_price` (the live price) and uses the same
        # profile-driven ATR multiples and the same nearest-liquidity target
        # selection it always did.
        # ------------------------------------------------------------------
        min_target_distance_atr = self.profile.min_target_distance_atr
        sl_atr_mult = self.profile.stop_loss_atr_multiple
        ob_buffer_atr = self.profile.ob_invalidation_atr_buffer
        tp_fallback_atr_mult = self.profile.take_profit_fallback_atr_multiple

        if direction == Direction.LONG:
            swing_low = min(sw.price for sw in snapshot.swing_lows[-3:]) if snapshot.swing_lows else current_price - 2 * atr
            stop_candidates = [swing_low, current_price - sl_atr_mult * atr]
            # BlockType's real enum values are lowercase ("bullish_ob" /
            # "bearish_ob") - see app/smc/order_block_engine.py.
            if ob_dict and ob_dict.get("type") == "bullish_ob":
                stop_candidates.append(ob_dict["low"] - ob_buffer_atr * atr)
            stop_loss = min(stop_candidates)

            liquidity_targets = sorted(
                lvl.price for lvl in all_levels
                if lvl.type == LiquidityType.BUYSIDE
                and lvl.price > current_price + min_target_distance_atr * atr
            )
            take_profit = liquidity_targets[0] if liquidity_targets else current_price + tp_fallback_atr_mult * atr
            structure_targets = snapshot.swing_highs
        else:
            swing_high = max(sw.price for sw in snapshot.swing_highs[-3:]) if snapshot.swing_highs else current_price + 2 * atr
            stop_candidates = [swing_high, current_price + sl_atr_mult * atr]
            if ob_dict and ob_dict.get("type") == "bearish_ob":
                stop_candidates.append(ob_dict["high"] + ob_buffer_atr * atr)
            stop_loss = max(stop_candidates)

            liquidity_targets = sorted(
                (lvl.price for lvl in all_levels
                 if lvl.type == LiquidityType.SELLSIDE
                 and lvl.price < current_price - min_target_distance_atr * atr),
                reverse=True,
            )
            take_profit = liquidity_targets[0] if liquidity_targets else current_price - tp_fallback_atr_mult * atr
            structure_targets = snapshot.swing_lows

        # ------------------------------------------------------------------
        # Which price is this trade actually entered at?
        #
        # The ICT anchor is used when it is USABLE - meaning it sits strictly
        # between the stop and the target on the correct side, and produces a
        # finite, positive risk distance. Anything else (a zone the stop or
        # target has already passed through, a degenerate zone, or no zone at
        # all) falls back to the live market price, which is exactly the
        # pre-migration behaviour. `EntryEngine` itself already ends its
        # priority chain with a MARKET plan, so the fallback below is a
        # second, arithmetic safety net rather than the only one.
        #
        # Under entry_mode "market" this deliberately always resolves to the
        # live price, so the two modes never blend.
        # ------------------------------------------------------------------
        entry_price_used = market_entry_price
        entry_type_used = "market"
        entry_zone_top = entry_zone_bottom = None

        if is_ict_pending_entry() and entry_plan is not None:
            candidate = entry_plan.entry_price
            if self._is_usable_entry(direction, candidate, stop_loss, take_profit):
                entry_price_used = candidate
                entry_type_used = entry_plan.entry_type.value
                entry_zone_top = entry_plan.anchor_top
                entry_zone_bottom = entry_plan.anchor_bottom

        risk_distance = abs(entry_price_used - stop_loss)
        reward_distance = abs(take_profit - entry_price_used)
        rr = round(reward_distance / risk_distance, 2) if risk_distance > 0 else 0

        # ------------------------------------------------------------------
        # Evidence Engine - compiles every ICT fact collected above into one
        # report BEFORE scoring, so `evidence_quality` can read it and so
        # the decision can explain itself. Collection only, no scoring.
        # ------------------------------------------------------------------
        risk_notes: List[str] = []
        if volatility == "high":
            risk_notes.append(
                "Volatility is elevated versus this symbol's own recent ATR baseline - "
                "expect wider swings and a higher chance of the stop being tagged by noise."
            )
        elif volatility == "low":
            risk_notes.append(
                "Volatility is compressed versus this symbol's own recent ATR baseline - "
                "targets may take longer to reach, and a sudden expansion can move price quickly."
            )

        htf_1d = htf_snapshot.get("1d")

        # Additive: run the fourteen newer ICT engines and surface their reads
        # as supplementary evidence notes. Never raises into the signal path,
        # and never touches the calibrated scorer.
        confluence_notes: List[str] = []
        _dir = "bullish" if direction == Direction.LONG else "bearish"
        _confluence = None
        try:
            _confluence = self.latest_ict_confluence.analyze(
                df, direction=_dir, fvgs=relevant_fvgs, order_blocks=[ob] if ob else None,
                reference_df=reference_df, symbol=self.symbol,
                reference_symbol=reference_symbol,
            )
            confluence_notes = (
                [f"[Latest-ICT] {s.name}: {s.detail}" for s in _confluence.signals]
                + list(_confluence.notes)
            )
        except Exception as _e:  # noqa: BLE001 - confluence is advisory only
            logger.debug(f"{self.symbol}: latest-ICT confluence skipped: {_e}")

        evidence_report = self.evidence_engine.compile(ICTEvidenceInputs(
            symbol=self.symbol,
            timeframe="15m",
            as_of=df.index[-1],
            current_price=current_price,
            direction_context="bullish" if direction == Direction.LONG else "bearish",
            structure_breaks=breaks,
            protected_high=snapshot.protected_high,
            protected_low=snapshot.protected_low,
            liquidity_levels=swept,
            inducement_events=inducement_events,
            order_blocks=[ob] if ob else None,
            fvgs=relevant_fvgs,
            premium_discount_zone=zone,
            premium_discount_position=zone_position,
            session_context=session_ctx,
            volume_confirmation=conf_latest,
            institutional_bias_notes=list(institutional_bias.supporting_evidence) + confluence_notes,
            risk_notes=risk_notes,
            htf_structure_alignment=htf_1d.structure_alignment if htf_1d else None,
            htf_timeframe_label="1D",
        ))

        # Only real, non-retail institutional data is threaded into the
        # Confidence Engine. The EMA-derived trend strings this method still
        # accepts for signature compatibility (btc_trend, htf_trend_1h/4h/1d,
        # entry_trend_5m) are deliberately NOT passed on - the ICT pipeline
        # gets its higher-timeframe read from real Market Structure, and
        # carrying dead retail values through the feature dict would be
        # exactly the hidden coupling this phase removes.
        liq_pressure = liquidation_pressure or {}
        inst = {
            "funding_rate": funding_rate,
            "volatility": volatility,
            "long_liquidated_usd": liq_pressure.get("long_liquidated_usd", 0.0),
            "short_liquidated_usd": liq_pressure.get("short_liquidated_usd", 0.0),
        }

        features = {
            "direction": direction.value,
            "symbol": self.symbol,
            "current_price": current_price,
            "market_structure": {"bos_choch": breaks},
            "liquidity": {"levels": swept},
            "order_block": ob_dict,
            "fvg": relevant_fvgs,
            "supply_demand_zone": zone,
            "zone_position": zone_position,
            "confirmation": conf_latest,
            "institutional": inst,
            "fundamentals": fundamentals or {},
            "inducement_events": inducement_events,
            "ote_zone": ote_zone,
            "institutional_bias": institutional_bias,
            "htf_snapshot": htf_snapshot,
            "session_context": session_ctx,
            "evidence_report": evidence_report,
            "risk_reward": rr,
        }

        # AI Confidence Engine.
        confidence, reason, score_breakdown = self.ai_scorer.assess(features)

        # Data-driven latest-ICT confluence bonus. Engines whose forward-return
        # edge was proven on THIS asset class in the matrix backtest
        # (scripts/backtest_matrix.py) nudge confidence up when their signal
        # agrees with the trade direction. Per-asset and bounded via the
        # calibration profile; assets/engines with no measured edge carry an
        # empty weight map, so their behavior is unchanged. Advisory-safe: any
        # failure leaves confidence exactly as the scorer produced it.
        try:
            if _confluence is not None:
                bonus = aligned_confluence_bonus(
                    _confluence.signals, _dir,
                    self.profile.confluence_weights,
                    self.profile.confluence_bonus_cap,
                )
                if bonus > 0:
                    adjusted = min(100, int(round(confidence + bonus)))
                    if adjusted != confidence:
                        logger.info(
                            f"{self.symbol}: +{bonus:.1f} confluence bonus "
                            f"(proven ICT engines aligned) -> confidence "
                            f"{confidence} -> {adjusted}"
                        )
                        confidence = adjusted
        except Exception as _e:  # noqa: BLE001 - confluence bonus is advisory
            logger.debug(f"{self.symbol}: confluence bonus skipped: {_e}")

        # ------------------------------------------------------------------
        # Hard gates - ALL evaluated, none short-circuited, so a rejection
        # reports every reason rather than only the first. The set of
        # signals that fire is unchanged: a trade still requires all gates
        # to pass.
        # ------------------------------------------------------------------
        blocking: List[BlockingReason] = []

        if confidence < self.profile.min_confidence:
            blocking.append(BlockingReason(
                gate=RejectionGate.MIN_CONFIDENCE,
                detail=(
                    f"Confidence {confidence} is below this asset's minimum of "
                    f"{self.profile.min_confidence}."
                ),
                measured=float(confidence),
                threshold=float(self.profile.min_confidence),
            ))

        # Real institutional confluence has to be PRESENT, not merely
        # scored well. A clean structural break with no footprint behind it
        # is not a setup. The required count comes from this asset's
        # profile: the shared default is 1, and a market whose profile asks
        # for more gets a stricter version of this same gate.
        confluence_signals = sum([bool(swept), bool(ob_dict), bool(relevant_fvgs)])
        required_confluence = max(1, self.asset_profile.ict_filters.min_confluence_signals)
        if confluence_signals < required_confluence:
            blocking.append(BlockingReason(
                gate=RejectionGate.NO_CONFIRMATION,
                detail=(
                    f"Only {confluence_signals} of the required {required_confluence} confluence "
                    f"signals present (liquidity sweep / order block / FVG)."
                ),
                measured=float(confluence_signals),
                threshold=float(required_confluence),
            ))

        # HIGHER-TIMEFRAME OPPOSITION - now ICT-native.
        #
        # This gate previously read `htf_trend_1d`/`htf_trend_4h`, which are
        # EMA20/50-derived (app/legacy/trend.py). Under the Universal ICT
        # mandate the production path must not depend on retail strategy
        # code, so the EMA read is gone and the same protection is now
        # provided by real Market Structure: `InstitutionalBiasEngine`'s
        # weighted Weekly/Daily/4H structure verdict. Those EMA parameters
        # are still ACCEPTED by `generate()` for signature compatibility
        # with the untouched BacktestEngine, but nothing in this pipeline
        # consumes them any more.
        #
        # `EntryValidationEngine` below independently re-checks HTF
        # alignment from the same structure snapshot; this gate exists as
        # the explicit, separately-reportable "do not fight the higher
        # timeframe" rule so a rejection names it directly.
        wanted_bias = "bullish" if direction == Direction.LONG else "bearish"
        if institutional_bias.direction in ("bullish", "bearish") and institutional_bias.direction != wanted_bias:
            blocking.append(BlockingReason(
                gate=RejectionGate.HTF_OPPOSITION,
                detail=(
                    f"Trade direction {direction.value} opposes higher-timeframe institutional "
                    f"bias ({institutional_bias.direction}), derived from Weekly/Daily/4H market "
                    f"structure."
                ),
            ))

        # ------------------------------------------------------------------
        # Asset-Profile filters. These make the SAME shared pipeline
        # stricter for a market whose profile asks for it - they never
        # introduce a different way of finding a trade. Crypto sets none of
        # them, so its behaviour is unchanged.
        # ------------------------------------------------------------------
        filters = self.asset_profile.ict_filters
        if session_ctx is not None:
            if filters.reject_off_session and session_ctx.is_off_session:
                blocking.append(BlockingReason(
                    gate=RejectionGate.OFF_SESSION,
                    detail=(
                        f"No primary session active for {self.asset_profile.display_name}. "
                        f"{self.asset_profile.liquidity.description}"
                    ),
                ))
            if filters.require_kill_zone and not session_ctx.in_kill_zone:
                blocking.append(BlockingReason(
                    gate=RejectionGate.KILL_ZONE_REQUIRED,
                    detail=(
                        f"{self.asset_profile.display_name} requires the setup to form inside a "
                        f"Kill Zone; current session context is '{session_ctx.label}'."
                    ),
                ))

        if rr < self.profile.min_risk_reward:
            blocking.append(BlockingReason(
                gate=RejectionGate.MIN_RISK_REWARD,
                detail=(
                    f"Risk:Reward {rr}:1 is below this asset's minimum of "
                    f"{self.profile.min_risk_reward}:1."
                ),
                measured=float(rr),
                threshold=float(self.profile.min_risk_reward),
            ))

        # Entry Validation - the explicit "reject incomplete setups" gate:
        # institutional bias, HTF alignment, confluence, session/kill zone,
        # risk, and invalidation checked together. Independent confirmation
        # built purely from ICT structure, not a duplicate detector.
        entry_validation = self.entry_validation_engine.validate(
            direction.value,
            institutional_bias=institutional_bias,
            htf_snapshot=htf_snapshot,
            liquidity_present=bool(swept),
            order_block_present=bool(ob_dict),
            fvg_present=bool(relevant_fvgs),
            session_context=session_ctx,
            risk_reward=rr,
            invalidation_level=stop_loss,
        )
        if not entry_validation.valid:
            failed = entry_validation.failed_checks
            details = "; ".join(
                f"{name}: {entry_validation.detail_for(name)}" for name in failed
            )
            blocking.append(BlockingReason(
                gate=RejectionGate.ENTRY_VALIDATION,
                detail=f"Entry validation failed on {', '.join(failed)}. {details}",
            ))

        # ------------------------------------------------------------------
        # AI Decision Engine - one fully-explained decision, trade or not.
        # ------------------------------------------------------------------
        decision = self.decision_engine.decide(DecisionInputs(
            symbol=self.symbol,
            direction=direction.value,
            confidence=confidence,
            score_breakdown=score_breakdown,
            evidence_report=evidence_report,
            blocking_reasons=blocking,
            has_market_structure=bool(breaks),
            has_liquidity=bool(swept),
            has_inducement=bool(inducement_events),
            has_order_block=bool(ob_dict),
            has_fvg=bool(relevant_fvgs),
            has_supply_demand=zone not in (None, "unknown"),
            has_premium_discount=zone_position is not None,
            has_ote=ote_zone is not None,
            has_institutional_bias=institutional_bias.direction in ("bullish", "bearish"),
            has_htf_alignment=not htf_snapshot.is_empty(),
            has_session_killzone=bool(session_ctx and session_ctx.in_kill_zone),
            has_volume_confirmation=conf_latest.get("cvd_rising") is not None,
            entry_price=entry_price_used,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward=rr,
            risk_notes=risk_notes,
            timeframe="15m",
            session_label=session_ctx.label if session_ctx is not None else None,
            institutional_bias=institutional_bias.direction,
        ))

        if decision.decision is DecisionType.NO_TRADE:
            logger.info(
                f"{self.symbol}: REJECTED | gates={decision.blocking_gates} | "
                f"Confidence={confidence} | RR={rr}"
            )
            return decision, None

        # ------------------------------------------------------------------
        # Exit plan.
        #
        # `entry_plan` is NO LONGER rebuilt here - it is built once, before
        # the SL/TP block above, and its price is what the trade is actually
        # entered at (see ICT ENTRY ACTIVATION there). Rebuilding it at this
        # point would have produced a second, independent plan that could
        # disagree with the one the Risk:Reward was computed from.
        #
        # The exit plan is measured from the SAME entry the trade is priced
        # from, so breakeven/partial levels line up with the real entry.
        # ------------------------------------------------------------------
        exit_plan = self.exit_engine.build(
            direction.value,
            entry_price=entry_price_used,
            stop_loss=stop_loss,
            liquidity_levels=all_levels,
            structure_targets=structure_targets,
            min_target_distance=min_target_distance_atr * atr,
            atr_fallback=tp_fallback_atr_mult * atr,
        )

        signal = {
            "symbol": self.symbol,
            "direction": direction.value,
            "entry": round(entry_price_used, 8),
            "stop_loss": round(stop_loss, 8),
            "take_profit": round(take_profit, 8),
            "risk_reward": rr,
            "confidence": confidence,
            "reason": reason,
            "score_breakdown": score_breakdown,
            # Which ICT anchor priced this trade, and the real bounds of that
            # anchor zone. The bounds are what the monitor and the backtest
            # test a fill against - price trading anywhere INSIDE the zone is
            # a fill, not only an exact touch of the midpoint in `entry`.
            "entry_type": entry_type_used,
            "entry_zone_top": entry_zone_top,
            "entry_zone_bottom": entry_zone_bottom,
            # The live price at the moment this setup was found, kept
            # alongside the entry so the distance price still has to travel
            # to fill is always auditable.
            "market_price_at_signal": round(market_entry_price, 8),
            "evidence": evidence_report.as_labels(),
            "institutional_bias": institutional_bias.direction,
            "htf_alignment": {
                tf: (htf_snapshot.get(tf).structure_alignment if htf_snapshot.get(tf) else None)
                for tf in ("1w", "1d", "4h", "1h")
            },
            "session": session_ctx.label if session_ctx is not None else None,
            "exit_plan": {
                "primary_target": exit_plan.primary_target.price if exit_plan.primary_target else None,
                "partial_targets": [lvl.price for lvl in exit_plan.partial_targets],
                "breakeven_trigger": exit_plan.breakeven_trigger.price if exit_plan.breakeven_trigger else None,
                "trailing_structure_enabled": exit_plan.trailing_structure_enabled,
            },
            # Full AI explainability payload - why long, why short, what was
            # missing, risk, invalidation. Persisted on the Signal row and
            # published over Redis so the UI can show the complete reasoning
            # behind every trade rather than just a confidence number.
            "decision": decision.to_dict(),
        }
        logger.success(
            f"{self.symbol}: SIGNAL GENERATED | {direction.value} | "
            f"Entry={round(entry_price_used, 8)} ({entry_type_used}) | "
            f"Market={round(market_entry_price, 8)} | Confidence={confidence} | RR={rr}"
        )
        return decision, signal
