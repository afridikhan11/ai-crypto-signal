"""
SMT Divergence (Smart Money Technique / Smart Money Divergence).

ICT's cross-asset confirmation tool. Two positively-correlated instruments
(e.g. BTC vs ETH, or a crypto vs a correlated index) should make their highs and
lows together. When they DON'T - one prints a higher high while the other fails
to and prints a lower high - smart money is not supporting that move, and it
often marks a reversal:

  - BEARISH SMT: at the highs, one asset makes a HIGHER HIGH but the correlated
    asset makes a LOWER HIGH (fails to confirm) -> the up-move is unsupported ->
    lean bearish.
  - BULLISH SMT: at the lows, one asset makes a LOWER LOW but the correlated
    asset makes a HIGHER LOW (fails to confirm) -> the down-move is unsupported
    -> lean bullish.

This is the first cross-asset engine in the suite; every other engine reads a
single symbol. It reuses MarketStructureEngine's (external) swing detection on
BOTH frames so "what counts as a swing" is identical to the rest of the project,
then compares the two most recent, time-aligned swings.

Pure and stateless-per-call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import pandas as pd

from app.smc.market_structure_engine import MarketStructureEngine, SwingPoint


@dataclass
class SMTDivergence:
    bias: str                 # "bullish" | "bearish"
    kind: str                 # "bearish_smt_high" | "bullish_smt_low"
    primary_symbol: str
    reference_symbol: str
    detail: str
    # The two swings that diverged (prices), for transparency / notes.
    primary_prev: float
    primary_last: float
    reference_prev: float
    reference_last: float


def _median_spacing(index: pd.Index) -> pd.Timedelta:
    if len(index) < 2:
        return pd.Timedelta(minutes=15)
    diffs = pd.Series(index[1:]) - pd.Series(index[:-1])
    return diffs.median()


def _aligned(a: SwingPoint, b: SwingPoint, tol: pd.Timedelta) -> bool:
    """The two swings describe the SAME move only if they occur at ~the same
    time - otherwise comparing them is meaningless."""
    return abs(a.timestamp - b.timestamp) <= tol


class SMTDivergenceEngine:
    """Stateless. `analyze()` called twice with the same inputs returns an
    equivalent result."""

    def __init__(self, align_bars: int = 5):
        # How far apart (in candles) the two assets' compared swings may be and
        # still count as "the same" swing event.
        self.align_bars = align_bars

    def analyze(
        self,
        primary_df: pd.DataFrame,
        reference_df: pd.DataFrame,
        primary_symbol: str = "PRIMARY",
        reference_symbol: str = "REFERENCE",
    ) -> Optional[SMTDivergence]:
        if primary_df is None or reference_df is None:
            return None
        if len(primary_df) < 10 or len(reference_df) < 10:
            return None

        p = MarketStructureEngine(primary_df).analyze()
        r = MarketStructureEngine(reference_df).analyze()

        tol = _median_spacing(primary_df.index) * self.align_bars

        # --- Bearish SMT at the highs ---
        bear = self._divergence_at_highs(
            p.swing_highs, r.swing_highs, tol,
            primary_symbol, reference_symbol,
        )
        # --- Bullish SMT at the lows ---
        bull = self._divergence_at_lows(
            p.swing_lows, r.swing_lows, tol,
            primary_symbol, reference_symbol,
        )

        # If both fire (rare), prefer the one whose divergence is most recent.
        candidates = [c for c in (bear, bull) if c is not None]
        if not candidates:
            return None
        return candidates[0] if len(candidates) == 1 else max(
            candidates,
            key=lambda c: c.kind == "bearish_smt_high",  # arbitrary, stable tiebreak
        )

    def _divergence_at_highs(
        self, p_highs: List[SwingPoint], r_highs: List[SwingPoint],
        tol: pd.Timedelta, p_sym: str, r_sym: str,
    ) -> Optional[SMTDivergence]:
        if len(p_highs) < 2 or len(r_highs) < 2:
            return None
        p_prev, p_last = p_highs[-2], p_highs[-1]
        r_prev, r_last = r_highs[-2], r_highs[-1]
        if not _aligned(p_last, r_last, tol):
            return None
        p_hh = p_last.price > p_prev.price
        r_hh = r_last.price > r_prev.price
        if p_hh == r_hh:
            return None  # both confirmed (or both failed) -> no divergence
        # One made a higher high, the other a lower high -> bearish SMT.
        which = p_sym if not p_hh else r_sym
        return SMTDivergence(
            bias="bearish", kind="bearish_smt_high",
            primary_symbol=p_sym, reference_symbol=r_sym,
            detail=f"{which} failed to confirm the higher high (bearish SMT)",
            primary_prev=p_prev.price, primary_last=p_last.price,
            reference_prev=r_prev.price, reference_last=r_last.price,
        )

    def _divergence_at_lows(
        self, p_lows: List[SwingPoint], r_lows: List[SwingPoint],
        tol: pd.Timedelta, p_sym: str, r_sym: str,
    ) -> Optional[SMTDivergence]:
        if len(p_lows) < 2 or len(r_lows) < 2:
            return None
        p_prev, p_last = p_lows[-2], p_lows[-1]
        r_prev, r_last = r_lows[-2], r_lows[-1]
        if not _aligned(p_last, r_last, tol):
            return None
        p_ll = p_last.price < p_prev.price
        r_ll = r_last.price < r_prev.price
        if p_ll == r_ll:
            return None  # both confirmed (or both failed) -> no divergence
        # One made a lower low, the other a higher low -> bullish SMT.
        which = p_sym if not p_ll else r_sym
        return SMTDivergence(
            bias="bullish", kind="bullish_smt_low",
            primary_symbol=p_sym, reference_symbol=r_sym,
            detail=f"{which} failed to confirm the lower low (bullish SMT)",
            primary_prev=p_prev.price, primary_last=p_last.price,
            reference_prev=r_prev.price, reference_last=r_last.price,
        )


# Default positively-correlated reference per symbol, for callers that want a
# sensible SMT partner without hard-coding one. BTC<->ETH is the canonical
# crypto SMT pair; others fall back to BTC as the market beta reference.
_DEFAULT_REFERENCE = {
    "BTCUSDT": "ETHUSDT",
    "ETHUSDT": "BTCUSDT",
    "SOLUSDT": "ETHUSDT",
}


def default_reference_symbol(symbol: str) -> str:
    """A reasonable correlated partner for SMT. Anything not mapped uses BTCUSDT
    as the market-beta reference (and BTC itself uses ETH)."""
    symbol = symbol.upper()
    return _DEFAULT_REFERENCE.get(symbol, "ETHUSDT" if symbol == "BTCUSDT" else "BTCUSDT")
