"""
Latest-ICT Confluence Layer.

Runs the fourteen newer ICT strategy engines together over a single OHLCV
frame and rolls their output into one structured confluence report: a list of
directional signals, a bullish/bearish tally, key draw-on-liquidity targets,
and human-readable notes. This is the integration surface the signal pipeline
consumes - it does NOT change the calibrated 13-category scorer; it adds an
independent, additive confluence read (evidence/context), consistent with the
"honest degradation" contract (an engine that finds nothing simply contributes
nothing).

Pure and stateless-per-call. Engines that need pre-computed FVGs / order
blocks / an impulse leg accept them; everything else is derived from `df`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd

from app.smc.key_levels_engine import KeyLevelsEngine
from app.smc.silver_bullet import SilverBulletEngine
from app.smc.judas_swing import JudasSwingEngine, JudasSession
from app.smc.consequent_encroachment import ConsequentEncroachmentEngine
from app.smc.balanced_price_range import BalancedPriceRangeEngine
from app.smc.unicorn_model import UnicornModelEngine
from app.smc.rejection_mitigation_blocks import RejectionBlockEngine, MitigationBlockEngine
from app.smc.turtle_soup import TurtleSoupEngine
from app.smc.power_of_three import PowerOfThreeEngine
from app.smc.opening_gaps import OpeningGapEngine
from app.smc.liquidity_void import LiquidityVoidEngine
from app.smc.ipda_ranges import IPDAEngine
from app.smc.smt_divergence import SMTDivergenceEngine


@dataclass
class ConfluenceSignal:
    name: str
    bias: Optional[str]     # "bullish" | "bearish" | None (neutral/context)
    detail: str


@dataclass
class LatestICTConfluenceReport:
    signals: List[ConfluenceSignal] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def bullish_count(self) -> int:
        return sum(1 for s in self.signals if s.bias == "bullish")

    @property
    def bearish_count(self) -> int:
        return sum(1 for s in self.signals if s.bias == "bearish")

    @property
    def net_bias(self) -> Optional[str]:
        if self.bullish_count > self.bearish_count:
            return "bullish"
        if self.bearish_count > self.bullish_count:
            return "bearish"
        return None

    def aligned_count(self, direction: Optional[str]) -> int:
        """How many signals agree with a candidate direction ('bullish'/'bearish')."""
        if direction is None:
            return 0
        return sum(1 for s in self.signals if s.bias == direction)


class LatestICTConfluenceEngine:
    def __init__(self):
        self.key_levels = KeyLevelsEngine()
        self.silver_bullet = SilverBulletEngine()
        self.judas = JudasSwingEngine()
        self.ce = ConsequentEncroachmentEngine()
        self.bpr = BalancedPriceRangeEngine()
        self.unicorn = UnicornModelEngine()
        self.rejection = RejectionBlockEngine()
        self.mitigation = MitigationBlockEngine()
        self.turtle = TurtleSoupEngine()
        self.power3 = PowerOfThreeEngine()
        self.opening_gaps = OpeningGapEngine()
        self.void = LiquidityVoidEngine()
        self.ipda = IPDAEngine()
        self.smt = SMTDivergenceEngine()

    def analyze(
        self,
        df: pd.DataFrame,
        direction: Optional[str] = None,
        fvgs: Optional[List] = None,
        order_blocks: Optional[List] = None,
        reference_df: Optional[pd.DataFrame] = None,
        symbol: str = "PRIMARY",
        reference_symbol: str = "REFERENCE",
    ) -> LatestICTConfluenceReport:
        report = LatestICTConfluenceReport()
        if df is None or df.empty:
            return report

        add = report.signals.append
        note = report.notes.append
        price = float(df["close"].iloc[-1])

        # --- Draw-on-liquidity targets (Key Levels) ---
        kl = self.key_levels.analyze(df)
        if kl.nearest_unswept_above is not None:
            note(f"Draw above: {kl.nearest_unswept_above.kind.value} @ {kl.nearest_unswept_above.price:.4f}")
        if kl.nearest_unswept_below is not None:
            note(f"Draw below: {kl.nearest_unswept_below.kind.value} @ {kl.nearest_unswept_below.price:.4f}")

        # --- Timing / manipulation ---
        for session in (JudasSession.LONDON, JudasSession.NEW_YORK):
            j = self.judas.detect(df, session)
            if j is not None:
                add(ConfluenceSignal(f"Judas Swing ({session.value})", j.bias_direction,
                                     f"false {j.false_direction} push, true bias {j.bias_direction}"))

        if direction is not None and fvgs:
            sb = self.silver_bullet.detect(df, direction, fvgs=fvgs)
            if sb is not None:
                add(ConfluenceSignal(f"Silver Bullet ({sb.window.value})", direction,
                                     f"entry @ CE {sb.entry_price:.4f}"))

        p3 = self.power3.analyze(df)
        if p3 is not None and p3.distribution_bias:
            add(ConfluenceSignal("Power of 3", p3.distribution_bias,
                                 f"{p3.phase.value}, manipulation {p3.manipulation_direction}"))

        ts = self.turtle.detect(df)
        if ts is not None:
            add(ConfluenceSignal("Turtle Soup", ts.direction.value,
                                 f"raided {ts.reference_level:.4f} then reversed"))

        # --- Entry models ---
        if fvgs:
            for bpr in self.bpr.analyze(fvgs, current_price=price):
                if bpr.reaction_bias:
                    add(ConfluenceSignal("Balanced Price Range", bpr.reaction_bias,
                                         f"BPR [{bpr.bottom:.4f}, {bpr.top:.4f}]"))
            if order_blocks:
                for uni in self.unicorn.analyze(order_blocks, fvgs):
                    add(ConfluenceSignal("Unicorn", uni.direction,
                                         f"breaker+FVG @ {uni.entry_price:.4f}"))

        rb = self.rejection.latest(df)
        if rb is not None:
            add(ConfluenceSignal("Rejection Block", rb.direction.value,
                                 f"wick zone [{rb.bottom:.4f}, {rb.top:.4f}]"))
        mb = self.mitigation.latest(df)
        if mb is not None:
            add(ConfluenceSignal("Mitigation Block", mb.direction.value,
                                 f"zone [{mb.bottom:.4f}, {mb.top:.4f}]"))

        for v in self.void.unfilled(df):
            add(ConfluenceSignal("Liquidity Void", v.direction.value,
                                 f"{v.atr_multiple}x ATR void [{v.bottom:.4f}, {v.top:.4f}]"))

        # --- Context (non-directional) ---
        gap = self.opening_gaps.day_gap(df)
        if gap is not None and not gap.filled:
            note(f"Unfilled NDOG [{gap.bottom:.4f}, {gap.top:.4f}] (CE {gap.midpoint:.4f})")

        consensus = self.ipda.consensus_zone(df)
        if consensus:
            note(f"IPDA consensus: {consensus}")
            # premium favors shorts, discount favors longs
            if consensus in ("premium", "discount"):
                add(ConfluenceSignal("IPDA Range", "bearish" if consensus == "premium" else "bullish",
                                     f"{consensus} of 20/40/60 dealing range"))

        # --- Cross-asset (SMT divergence) ---
        # Only runs when a correlated reference frame is supplied; single-symbol
        # callers pass none and this contributes nothing, exactly like any other
        # engine that finds no setup.
        if reference_df is not None:
            smt = self.smt.analyze(df, reference_df, symbol, reference_symbol)
            if smt is not None:
                add(ConfluenceSignal("SMT Divergence", smt.bias, smt.detail))

        return report
