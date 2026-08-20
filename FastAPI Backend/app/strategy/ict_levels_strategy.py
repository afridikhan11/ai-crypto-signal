"""
ICT Levels strategy - the first Smart AI strategy (app/strategy/base_strategy.py).

WHAT THIS IS
--------------------------------------------------------------------------
A top-down ICT setup that fires only when the higher timeframe, a liquidity
raid, an impulsive displacement, and a discounted/premium retracement into a
confluence zone (OTE / FVG / Order Block) all line up in the SAME direction.
It is a thin orchestrator: every piece of detection it needs already exists,
verified and unit-tested, in `app/smc/`. This module deliberately adds NO new
detection - it reuses:

  - MarketStructureEngine  (swings, BOS/CHoCH)          app/smc/market_structure_engine.py
  - FVGDetector            (fair value gaps + fill)     app/smc/fvg.py
  - OrderBlockEngine       (order blocks + lifecycle)   app/smc/order_block_engine.py
  - LiquidityEngine        (BSL/SSL pools + sweeps)     app/smc/liquidity_engine.py
  - HTFStructureEngine     (multi-timeframe structure)  app/smc/htf_structure_engine.py
  - InstitutionalBiasEngine(HTF directional bias)       app/smc/institutional_bias_engine.py

The call/construction patterns mirror the production
`app/strategy/signal_generator.py` (its `_evaluate()`), which wires the same
engines - this strategy is a separate, self-contained consumer of them, not a
modification of that pipeline.

The only local logic is a handful of PURE, individually unit-testable helpers
for the ICT "levels" primitives that are not already discrete functions in the
engines: dealing range, equilibrium, premium/discount, equal-level clustering,
and the OTE retracement band. They take plain numbers / pandas and return plain
numbers / lists, so they can be tested with hand-built inputs.

Every evaluation logs EVERY checked rule as a `ConditionResult` (pass AND fail),
so a `None` return is always explainable from the conditions plus the loguru
log line - the whole point of the Smart AI attribution layer.
"""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

import pandas as pd
from loguru import logger

from app.models.signal import Direction
from app.smc.fvg import FVGDetector, FVGType
from app.smc.htf_structure_engine import HTFStructureEngine
from app.smc.institutional_bias_engine import InstitutionalBiasEngine
from app.smc.liquidity_engine import LiquidityEngine, LiquidityType, SweepOutcome
from app.smc.market_structure_engine import MarketStructureEngine
from app.smc.order_block_engine import BlockType, OrderBlockEngine
from app.strategy.base_strategy import (
    BaseStrategy,
    ConditionResult,
    MarketData,
    StrategySignal,
    register_strategy,
)

# Minimum candles before the swing/structure engines can say anything reliable.
# Matches the spirit of SignalGenerator's own `len(df) < 50` guard - ICT
# structure on a too-short window is noise, so refuse rather than guess.
MIN_CANDLES = 50

# Same external pivot granularity SignalGenerator and HTFStructureEngine use, so
# a "swing"/"break" means the same thing at every timeframe this strategy reads.
EXTERNAL_PIVOT_WINDOW = 5


# ----------------------------------------------------------------------
# PURE, individually unit-testable ICT level primitives.
# Dependency-free (plain floats / lists / pandas) so tests can call them
# with hand-built inputs.
# ----------------------------------------------------------------------
def dealing_range(df: pd.DataFrame, lookback: int) -> Tuple[float, float]:
    """The ICT dealing range over the last `lookback` candles: (swing high,
    swing low) = (highest high, lowest low). Uses whatever is available when
    the frame is shorter than `lookback`. Raises on an empty frame - a range
    with no candles is undefined, and returning a guess would be dishonest."""
    if df is None or len(df) == 0:
        raise ValueError("dealing_range requires at least one candle")
    window = df.tail(max(1, int(lookback)))
    high = float(window["high"].max())
    low = float(window["low"].min())
    return high, low


def equilibrium(high: float, low: float) -> float:
    """The 50% midpoint of a dealing range - ICT's premium/discount divider."""
    return (float(high) + float(low)) / 2.0


def premium_discount(price: float, high: float, low: float) -> Tuple[str, float]:
    """Where `price` sits in the [low, high] dealing range.

    Returns (zone, pct) where `pct` is the position in the range as a
    percentage (0.0 at the low, 100.0 at the high) and `zone` is "premium"
    (above equilibrium), "discount" (below), or "equilibrium" (exactly at the
    midpoint, or a degenerate zero-width range)."""
    high_f, low_f, price_f = float(high), float(low), float(price)
    span = high_f - low_f
    if span <= 0:
        return "equilibrium", 50.0
    pct = (price_f - low_f) / span * 100.0
    eq = equilibrium(high_f, low_f)
    if price_f > eq:
        zone = "premium"
    elif price_f < eq:
        zone = "discount"
    else:
        zone = "equilibrium"
    return zone, pct


def equal_levels(prices: List[float], tolerance_pct: float) -> List[List[float]]:
    """Cluster near-equal price levels (equal highs / equal lows) within
    `tolerance_pct` PERCENT of each other.

    Two levels belong to the same pool when `abs(a - b) / avg * 100 <=
    tolerance_pct`. Returns only real pools (clusters of >= 2 levels) - a lone,
    isolated level is resting liquidity but not an "equal levels" pattern. The
    input order is preserved; each returned cluster keeps the prices in
    ascending order. Empty / single-element input returns []."""
    pts = [float(p) for p in (prices or [])]
    if len(pts) < 2:
        return []
    ordered = sorted(pts)
    clusters: List[List[float]] = []
    current = [ordered[0]]
    for price in ordered[1:]:
        anchor = current[0]
        avg = (anchor + price) / 2.0
        within = avg > 0 and abs(price - anchor) / avg * 100.0 <= float(tolerance_pct)
        if within:
            current.append(price)
        else:
            if len(current) >= 2:
                clusters.append(current)
            current = [price]
    if len(current) >= 2:
        clusters.append(current)
    return clusters


def ote_zone(
    leg_start: float, leg_end: float, low: float = 0.62, high: float = 0.79
) -> Tuple[float, float]:
    """The Optimal Trade Entry retracement band of an impulse leg.

    `leg_start` -> `leg_end` is the impulsive displacement leg (start is its
    origin, end is its extreme). `low`/`high` are the retracement fractions
    (ICT's 0.62 and 0.79). Returns (lo, hi) price band, always low-to-high,
    for a leg in either direction. A zero-length leg returns a degenerate
    band at the leg price."""
    start, end = float(leg_start), float(leg_end)
    span = end - start
    a = end - float(low) * span
    b = end - float(high) * span
    return (min(a, b), max(a, b))


# ----------------------------------------------------------------------
# The strategy.
# ----------------------------------------------------------------------
@register_strategy
class IctLevelsStrategy(BaseStrategy):
    """ICT Levels - see module docstring. Reads its knobs off `self.settings`
    and is otherwise side-effect free (no exchange, no DB)."""

    strategy_id = "ict_levels"
    display_name = "ICT Levels"

    @property
    def enabled(self) -> bool:
        return bool(
            getattr(self.settings, "smartai_enabled", False)
            and getattr(self.settings, "strategy_ict_levels_enabled", False)
        )

    async def evaluate(self, market_data: MarketData) -> Optional[StrategySignal]:
        symbol = market_data.symbol
        conditions: List[ConditionResult] = []

        def record(name: str, passed: bool, detail: str = "") -> bool:
            conditions.append(ConditionResult(name=name, passed=passed, detail=detail))
            return passed

        def reject(reason: str) -> None:
            logger.info(f"{symbol}: ict_levels NO SIGNAL - {reason}")
            return None

        df = market_data.dataframe
        if df is None or len(df) < MIN_CANDLES:
            n = 0 if df is None else len(df)
            record("sufficient_data", False, f"{n} candles (need >= {MIN_CANDLES})")
            return reject(f"insufficient data ({n} candles)")
        record("sufficient_data", True, f"{len(df)} candles on entry timeframe")

        lookback = int(self.settings.ict_dealing_range_lookback)
        current_price = (
            market_data.current_price
            if market_data.current_price is not None
            else float(df["close"].iloc[-1])
        )

        # --- Dealing range / equilibrium / premium-discount (pure helpers) ---
        high, low = dealing_range(df, lookback)
        eq = equilibrium(high, low)
        zone, zone_pct = premium_discount(current_price, high, low)
        record(
            "dealing_range",
            high > low,
            f"high={high:.6g} low={low:.6g} eq={eq:.6g}",
        )
        record(
            "premium_discount",
            True,
            f"price in {zone} ({zone_pct:.1f}% of range)",
        )

        # --- LTF market structure: swings + BOS/CHoCH displacement ---
        ms = MarketStructureEngine(df, external_pivot_window=EXTERNAL_PIVOT_WINDOW).analyze()
        breaks = ms.external_breaks
        latest_break = max(breaks, key=lambda b: b.timestamp) if breaks else None
        displacement_dir = latest_break.direction if latest_break else None
        has_displacement = record(
            "displacement",
            latest_break is not None,
            (
                f"{latest_break.type} {latest_break.direction} "
                f"(disp {latest_break.displacement_ratio:.2f} ATR)"
                if latest_break
                else "no BOS/CHoCH on entry timeframe"
            ),
        )

        # --- HTF bias (HTFStructureEngine -> InstitutionalBiasEngine) ---
        htf_tf = self.settings.ict_htf_timeframe
        htf_df = market_data.htf_dataframes.get(htf_tf)
        htf_input = {htf_tf: htf_df} if htf_df is not None else {}
        htf_snapshot = HTFStructureEngine(
            external_pivot_window=EXTERNAL_PIVOT_WINDOW
        ).build_snapshot(htf_input)
        bias = InstitutionalBiasEngine().assess(
            htf_snapshot, premium_discount_zone=zone
        )
        htf_dir = bias.direction  # "bullish" | "bearish" | "neutral" | "conflicted"

        # Trade direction is set by the LTF displacement; the HTF bias is the
        # directional filter it must agree with (unless counter-bias is
        # explicitly allowed).
        allow_counter = bool(self.settings.ict_allow_counter_bias)
        aligned = (
            displacement_dir is not None
            and htf_dir in ("bullish", "bearish")
            and htf_dir == displacement_dir
        )
        bias_ok = record(
            "htf_bias_aligned",
            aligned or (allow_counter and displacement_dir is not None),
            (
                f"HTF({htf_tf})={htf_dir}, displacement={displacement_dir}"
                + ("" if not allow_counter else " [counter-bias allowed]")
            ),
        )

        # --- Liquidity pools + sweep (LiquidityEngine) ---
        liq = LiquidityEngine(
            df,
            ms.swing_highs,
            ms.swing_lows,
            internal_swing_highs=ms.internal_swing_highs,
            internal_swing_lows=ms.internal_swing_lows,
            structure_breaks=breaks,
            timeframe=self.settings.ict_ltf_timeframe,
        )
        buyside = liq.detect_liquidity_sweeps(liq.detect_buyside_liquidity())
        sellside = liq.detect_liquidity_sweeps(liq.detect_sellside_liquidity())

        # A long is triggered by a SELLSIDE raid (a grab of lows that reverses
        # up); a short by a BUYSIDE raid. When there is no displacement yet we
        # still look for the sweep so the condition is always logged.
        want_long = displacement_dir == "bullish"
        raid_side = sellside if want_long else buyside
        swept_grabs = [
            lvl
            for lvl in raid_side
            if lvl.swept and lvl.sweep_outcome == SweepOutcome.GRAB and lvl.swept_at is not None
        ]
        sweep_level = (
            max(swept_grabs, key=lambda lvl: lvl.swept_at) if swept_grabs else None
        )
        sweep_ok = record(
            "liquidity_sweep",
            sweep_level is not None,
            (
                f"{sweep_level.type.value} pool at {sweep_level.price:.6g} grabbed "
                f"@ {sweep_level.swept_at}"
                if sweep_level
                else "no liquidity grab in the trade direction"
            ),
        )

        # --- FVG + Order Block confluence (FVGDetector, OrderBlockEngine) ---
        fvgs = FVGDetector(df).detect_fvg()
        wanted_fvg = FVGType.BULLISH if want_long else FVGType.BEARISH
        fvg_hit = next(
            (
                f
                for f in fvgs
                if not f.filled
                and f.type == wanted_fvg
                and f.bottom <= current_price <= f.top
            ),
            None,
        )

        ob_engine = OrderBlockEngine(df, structure_breaks=breaks, fvgs=fvgs)
        ob = (
            ob_engine.find_bullish_order_block()
            if want_long
            else ob_engine.find_bearish_order_block()
        )
        want_ob = BlockType.BULLISH_OB if want_long else BlockType.BEARISH_OB
        ob_hit = ob is not None and ob.type == want_ob and ob.low <= current_price <= ob.high

        # --- OTE retracement band (pure helper), built from the sweep leg ---
        ote_hit = False
        ote_lo = ote_hi = None
        if sweep_level is not None:
            swept_idx = int(df.index.get_indexer([sweep_level.swept_at])[0])
            if want_long:
                leg_start = float(df["low"].iloc[swept_idx:].min())
                leg_end = float(df["high"].iloc[swept_idx:].max())
            else:
                leg_start = float(df["high"].iloc[swept_idx:].max())
                leg_end = float(df["low"].iloc[swept_idx:].min())
            ote_lo, ote_hi = ote_zone(
                leg_start, leg_end, self.settings.ict_ote_low, self.settings.ict_ote_high
            )
            ote_hit = ote_lo <= current_price <= ote_hi

        in_zone = ote_hit or (fvg_hit is not None) or ob_hit
        zone_ok = record(
            "confluence_zone",
            in_zone,
            (
                "retracement into "
                + ", ".join(
                    part
                    for part, on in (
                        (f"OTE[{ote_lo:.6g},{ote_hi:.6g}]" if ote_lo is not None else "OTE", ote_hit),
                        ("FVG", fvg_hit is not None),
                        ("OrderBlock", ob_hit),
                    )
                    if on
                )
                if in_zone
                else "price not inside any OTE/FVG/OB confluence zone"
            ),
        )

        # ------------------------------------------------------------------
        # Gate: every hard condition must pass before pricing the trade.
        # All conditions above are ALREADY logged (pass and fail).
        # ------------------------------------------------------------------
        if not (has_displacement and bias_ok and sweep_ok and zone_ok):
            failed = [c.name for c in conditions if not c.passed]
            return reject(f"gates not met: {failed}")

        direction = Direction.LONG if want_long else Direction.SHORT

        # --- Stops & targets ---
        # Stop just beyond the extreme of the wick that swept liquidity.
        swept_idx = int(df.index.get_indexer([sweep_level.swept_at])[0])
        buffer = current_price * 0.0005
        if want_long:
            wick_extreme = float(df["low"].iloc[swept_idx])
            stop_loss = wick_extreme - buffer
            # Final target: nearest UNSWEPT opposing (buyside) pool above entry -
            # resting liquidity price gravitates toward. A pool the rally already
            # traded through is spent, not a draw, so it is skipped. Falls back to
            # the dealing-range high when no resting pool sits above.
            pool_prices = sorted(
                lvl.price for lvl in buyside if not lvl.swept and lvl.price > current_price
            )
            final_target = pool_prices[0] if pool_prices else high
        else:
            wick_extreme = float(df["high"].iloc[swept_idx])
            stop_loss = wick_extreme + buffer
            pool_prices = sorted(
                (lvl.price for lvl in sellside if not lvl.swept and lvl.price < current_price),
                reverse=True,
            )
            final_target = pool_prices[0] if pool_prices else low

        risk = abs(current_price - stop_loss)
        reward = abs(final_target - current_price)
        rr = round(reward / risk, 2) if risk > 0 else 0.0
        min_rr = float(self.settings.ict_min_risk_reward)
        rr_ok = record(
            "risk_reward",
            rr >= min_rr,
            f"RR {rr}:1 (min {min_rr}:1), entry={current_price:.6g} "
            f"stop={stop_loss:.6g} target={final_target:.6g}",
        )
        if not rr_ok:
            return reject(f"risk:reward {rr} below minimum {min_rr}")

        # Target ladder: equilibrium as the partial-take, opposing pool as final.
        targets = [eq, final_target]

        # --- Confidence: base + points per confluence, explainable ---
        confluence_flags = [
            aligned,
            sweep_level is not None,
            has_displacement,
            ote_hit,
            fvg_hit is not None,
            ob_hit,
        ]
        confidence = min(100, 40 + 10 * sum(1 for f in confluence_flags if f))

        reason = (
            f"{direction.value} ICT setup: HTF {htf_dir} bias, "
            f"{sweep_level.type.value} liquidity grab, "
            f"{latest_break.type} {latest_break.direction} displacement, "
            f"retracement into confluence ({zone}). RR {rr}:1."
        )

        logger.info(f"{symbol}: ict_levels SIGNAL {direction.value} conf={confidence} RR={rr}")

        return StrategySignal(
            strategy_id=self.strategy_id,
            symbol=symbol,
            direction=direction,
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit=final_target,
            risk_reward=rr,
            confidence=confidence,
            conditions=conditions,
            targets=targets,
            reason=reason,
            metadata={
                "htf_bias": htf_dir,
                "htf_timeframe": htf_tf,
                "dealing_range_high": high,
                "dealing_range_low": low,
                "equilibrium": eq,
                "premium_discount_zone": zone,
                "premium_discount_pct": zone_pct,
                "sweep_price": sweep_level.price,
                "sweep_side": sweep_level.type.value,
                "ote_zone": [ote_lo, ote_hi],
                "fvg_confluence": fvg_hit is not None,
                "ob_confluence": ob_hit,
            },
        )
