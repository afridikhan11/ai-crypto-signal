"""
Higher Timeframe Structure Engine - one reusable multi-timeframe Market
Structure snapshot (Weekly / Daily / 4H / 1H).

STATUS (2026-07-29): PRODUCTION. New module - final phase of the
institutional ICT migration (see ICT_ARCHITECTURE_AUDIT_AND_MIGRATION_PLAN.md).
This engine detects NOTHING new: it is a thin, reusable wrapper that runs
the existing, unmodified `MarketStructureEngine` once per caller-supplied
higher-timeframe OHLCV dataframe and bundles the results into one
`HTFSnapshot`. This is exactly the "one reusable HTF snapshot... aggregate
only ICT evidence" requirement - no EMA, no retail indicator of any kind
appears anywhere in this file.

WHY A SEPARATE ENGINE RATHER THAN INLINING THIS INTO SIGNAL GENERATOR
--------------------------------------------------------------------------
Both `InstitutionalBiasEngine` (Weekly/Daily/4H bias) and the AI Scorer's
`htf_alignment` category need the SAME multi-timeframe structure read.
Building it once, here, and handing both consumers the same `HTFSnapshot`
avoids computing Market Structure twice for the same higher-timeframe data
- one responsibility (build the snapshot), reused by two different
downstream interpreters (bias vs. scoring).

HONEST DEGRADATION
--------------------------------------------------------------------------
`build_snapshot()` takes a plain `Dict[str, pd.DataFrame]` keyed by
timeframe label ("1w"/"1d"/"4h"/"1h"). Any subset may be supplied - a
caller that only has Daily and 4H data (e.g. today's live scanner, which
does not yet fetch Weekly candles) simply gets an `HTFSnapshot` with only
those two keys populated; `HTFSnapshot.get()` returns `None` for anything
not supplied, never a guess. `BacktestEngine` (unmodified in this phase)
does not construct real HTF dataframes at all today, so any caller that
doesn't pass `htf_dataframes` gets an empty `HTFSnapshot` - see
`SignalGenerator`'s own docstring for how it degrades from there.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import pandas as pd

from app.smc.market_structure_engine import MarketStructureEngine, MarketStructureSnapshot

# The four timeframes this engine understands, heaviest (most macro) first -
# consumers (InstitutionalBiasEngine, the rebuilt AIScorer) use this same
# ordering for their own weighted aggregation, so it's exposed here as the
# single source of truth for "which HTF timeframes exist and in what order
# of significance", per ICT's top-down bias methodology.
SUPPORTED_TIMEFRAMES = ("1w", "1d", "4h", "1h")

# A snapshot needs at least this many candles before Market Structure's own
# pivot-window swing detection has any chance of confirming a single swing
# (matches MarketStructureEngine's own internal `2 * pivot_window + 1`
# minimum) - below this, `build_snapshot` still calls the engine (which
# already degrades to empty swings/insufficient_data honestly on its own),
# this constant only controls whether this engine bothers to try at all for
# a clearly-too-short frame, saving a wasted computation.
MIN_CANDLES = 20


@dataclass(frozen=True)
class TimeframeStructure:
    """One timeframe's Market Structure read - every field copied directly
    from the real `MarketStructureSnapshot` `MarketStructureEngine.analyze()`
    already produced for this timeframe's dataframe."""

    timeframe: str
    structure_alignment: str  # "aligned_bullish" | "aligned_bearish" | "conflicted" | "insufficient_data"
    protected_high: Optional[float]
    protected_low: Optional[float]
    latest_break_direction: Optional[str]   # "bullish" | "bearish" | None
    latest_break_type: Optional[str]        # "BOS" | "CHoCH" | None
    snapshot: MarketStructureSnapshot        # full detail, for consumers that need more than the summary fields above


@dataclass(frozen=True)
class HTFSnapshot:
    """The aggregate multi-timeframe read. Only contains keys for
    timeframes actually supplied to `build_snapshot()` - see module
    docstring's honest-degradation note."""

    timeframes: Dict[str, TimeframeStructure]

    def get(self, timeframe: str) -> Optional[TimeframeStructure]:
        return self.timeframes.get(timeframe)

    @property
    def available_timeframes(self) -> Dict[str, bool]:
        return {tf: (tf in self.timeframes) for tf in SUPPORTED_TIMEFRAMES}

    def is_empty(self) -> bool:
        return not self.timeframes


class HTFStructureEngine:
    """Stateless. `build_snapshot()` called twice with the same input
    dataframes returns an equivalent `HTFSnapshot` - same design philosophy
    as every other engine in `app/smc/`."""

    def __init__(self, external_pivot_window: int = 5, internal_pivot_window: int = 3) -> None:
        # Same external_pivot_window=5 SignalGenerator already uses for the
        # primary (15m) timeframe's Market Structure read - kept consistent
        # so a "break" means the same swing granularity at every timeframe,
        # not a mismatched definition per level.
        self.external_pivot_window = external_pivot_window
        self.internal_pivot_window = internal_pivot_window

    def build_snapshot(self, dataframes: Optional[Dict[str, pd.DataFrame]]) -> HTFSnapshot:
        if not dataframes:
            return HTFSnapshot(timeframes={})

        result: Dict[str, TimeframeStructure] = {}
        for timeframe, df in dataframes.items():
            if df is None or len(df) < MIN_CANDLES:
                continue  # honestly omitted, never guessed from too little data

            snapshot = MarketStructureEngine(
                df,
                external_pivot_window=self.external_pivot_window,
                internal_pivot_window=self.internal_pivot_window,
            ).analyze()

            latest_break = snapshot.external_breaks[-1] if snapshot.external_breaks else None

            result[timeframe] = TimeframeStructure(
                timeframe=timeframe,
                structure_alignment=snapshot.structure_alignment,
                protected_high=snapshot.protected_high,
                protected_low=snapshot.protected_low,
                latest_break_direction=latest_break.direction if latest_break else None,
                latest_break_type=latest_break.type if latest_break else None,
                snapshot=snapshot,
            )

        return HTFSnapshot(timeframes=result)
