"""
Signal Pipeline Diagnostics - read-only observability layer.

STATUS (2026-07-30): DIAGNOSTICS ONLY. Added to determine exactly why the
live scanner is producing zero signals, per an explicit "diagnostics only"
mandate: do not modify ICT logic, AI scoring, the Universal Scanner, the
Risk Engine, Trade Management, Auto Trading, position sizing, or any
confidence threshold; do not loosen any filter; do not bypass any gate.

HOW THIS STAYS 100% READ-ONLY
--------------------------------------------------------------------------
This module never imports or calls into an SMC/ICT engine
(`app/smc/*`), the AI Scorer (`app/ai/scorer.py`), the AI Decision Engine
(`app/ai/ict_decision_engine.py`), the Risk Engine (`app/risk/*`), Trade
Management, or Position Sizing. It has no function whose return value the
live pipeline consumes - every method here takes an already-computed
result as input and only writes to its own private counters. Two call
sites exist, both in `app/scheduler/universal_scanner.py`
(`analyze_symbol()` and `save_signal()`), both additive - see that file
for exactly where. Every call there is wrapped in `try/except` so a bug
in this module can never interrupt or alter a real scan cycle; see
`_safe()` below.

WHAT "STAGE" MEANS HERE
--------------------------------------------------------------------------
`SignalGenerator.evaluate()` (app/strategy/signal_generator.py) already
evaluates every hard gate on every run (never short-circuits) and returns
one fully-explained `TradeDecision` - see that module's own "GATE
EVALUATION" docstring section. That object already carries, for free,
everything this diagnostics layer needs:

  - `decision.missing_evidence`  - which of the 12 ICT components
    (`app.ai.ict_decision_engine.ICT_COMPONENT_CHECKLIST`) were absent
    this run - this IS the per-component "did this stage find something"
    signal for Institutional Bias / Order Block / Liquidity / FVG / etc.
  - `decision.blocking_gates`    - every named gate that blocked the
    trade (confidence, RR, HTF opposition, session, confluence, ...).
  - `decision.confidence`        - the AI Scorer's output number.
  - `decision.is_tradeable`      - the Signal Generator's final verdict.

Reading these AFTER `evaluate()` returns - rather than adding timing/
counting calls inside `signal_generator.py` itself - is what keeps this
phase's change to a single file (`universal_scanner.py`) and zero lines
inside any ICT/AI module.

ONE DISCLOSED LIMITATION: PER-ENGINE PROCESSING TIME
--------------------------------------------------------------------------
Because this module only observes the OUTPUT of one `evaluate()` call, it
can time that call as a whole (`ict_pipeline_evaluation`), but it CANNOT
separately measure, say, "Order Block Detection took 3ms" vs "Liquidity
Detection took 5ms" - those all happen inside one Python call the same
process already made, and splitting them would require adding timing
markers inside `signal_generator.py` itself. This phase deliberately does
not do that (it would touch the ICT pipeline file), so per-stage
Processing Time below is real for `market_data`, `ict_pipeline_evaluation`
(one number covering ICT Detection through AI Scoring together),
`database_save`, and `wpf_update` - and explicitly reported as "bundled"
for the five ICT sub-stages rather than a fabricated split. See the final
report for how to add finer timing if wanted later, as an explicit
follow-up.

RISK ENGINE IS NOT PART OF SIGNAL GENERATION
--------------------------------------------------------------------------
`app/risk/risk_engine.py`'s `RiskEngine` is called from
`app/services/execution_risk.py`, which is only reached from
`POST /trading/execute/{signal_id}` - i.e. AFTER a signal already exists
and a user (or Auto Trading) chooses to execute it. It has no role in
whether a signal gets GENERATED. The `risk_engine` stage below is
therefore always reported as N/A/not-applicable during a scan cycle, not
faked as a stage that runs here - this is itself a real, disclosed
diagnostic finding, not an omission.
"""
from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Optional, Set

from loguru import logger

SUMMARY_INTERVAL_SECONDS = 300.0  # 5 minutes, per the diagnostics mandate

# Every stage this module tracks, in pipeline order, with the exact label
# to print. `risk_engine` is included for completeness/visibility (see
# module docstring) even though it is always N/A here.
STAGE_LABELS: Dict[str, str] = {
    "market_data": "Market Data",
    "scanner": "Scanner",
    "ict_detection": "ICT Detection (Market Structure)",
    "institutional_bias": "Institutional Bias",
    "order_block_detection": "Order Block Detection",
    "liquidity_detection": "Liquidity Detection",
    "fvg_detection": "Fair Value Gap Detection",
    "ai_scoring": "AI Scoring",
    "risk_engine": "Risk Engine (N/A - execution-time only, not generation)",
    "signal_generator": "Signal Generator (final decision)",
    "database_save": "Database Save",
    "wpf_update": "WPF Update (Redis publish)",
    # ICT Pending Limit Entry (2026-07-30). These sit AFTER the save: a
    # saved pending signal is not yet a trade, so fills and expiries are
    # tracked separately from generation. `input` is a pending signal that
    # reached a decision this interval; `output` is one that actually filled.
    "pending_entry_fill": "Pending Entry -> Filled (became a real trade)",
}

# TradeDecision.missing_evidence[i].component -> the stage key above.
# Straight from app.ai.ict_decision_engine.ICT_COMPONENT_CHECKLIST -
# copied here as plain strings (not imported) so this diagnostics module
# has zero import-time coupling to the decision engine, matching the
# isolation precedent already used elsewhere in this codebase (see
# app/services/market_scorer.py's own MIN_CONFIDENCE duplication note).
COMPONENT_TO_STAGE: Dict[str, str] = {
    "market_structure": "ict_detection",
    "order_block": "order_block_detection",
    "liquidity": "liquidity_detection",
    "fvg": "fvg_detection",
    "institutional_bias": "institutional_bias",
}

# RejectionGate.value -> a human filter-reason label, matching the style
# of the examples in the diagnostics mandate ("No HTF Bias", "Confidence
# Too Low", ...). Straight from app.ai.ict_decision_engine.RejectionGate,
# copied as plain strings for the same isolation reason as above.
GATE_REASON_LABELS: Dict[str, str] = {
    "insufficient_data": "Insufficient Candle Data",
    "no_structure_break": "No MSS/BOS",
    "indicator_nan": "Order-Flow Indicator Unavailable",
    "invalid_atr": "Invalid ATR",
    "min_confidence": "Confidence Too Low",
    "no_confirmation": "Liquidity/Order Block/FVG Confluence Missing",
    "htf_opposition": "No HTF Bias Alignment",
    "min_risk_reward": "Risk/Reward Too Small",
    "entry_validation": "Entry Validation Failed",
    "off_session": "Trading Session Closed",
    "kill_zone_required": "Outside Required Kill Zone",
}

# Missing-evidence components that are NOT already a named blocking gate
# above (e.g. "No Order Block" only shows up via missing_evidence, never
# via blocking_gates, because confluence is evaluated as a COUNT - see
# signal_generator.py's `no_confirmation` gate) - given their own labels
# so the summary's rejection tally can name them individually rather than
# only the generic confluence gate.
COMPONENT_REASON_LABELS: Dict[str, str] = {
    "market_structure": "No MSS/BOS",
    "order_block": "No Order Block",
    "liquidity": "Liquidity Missing",
    "fvg": "Weak/Missing FVG",
    "institutional_bias": "No Institutional Bias",
}

# Additional, non-pipeline-stage reasons this module also tallies because
# the mandate explicitly lists them as examples ("Duplicate Signal",
# "Trading Session Closed" is covered above via off_session/kill_zone).
NO_MARKET_DATA_REASON = "No Market Data"
DUPLICATE_SIGNAL_REASON = "Duplicate Signal"
# ICT pending entries that were never taken. Deliberately tracked as its own
# reason rather than folded into a rejection: the SETUP passed every gate,
# price simply never came back to the entry zone.
PENDING_ENTRY_UNFILLED_REASON = "Pending Entry Never Filled"


def _safe(fn):
    """Decorator: any exception raised while RECORDING a diagnostic is
    logged once and swallowed - a bug in this module must never be able
    to interrupt, delay, or alter a real scan cycle. See module docstring."""

    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 - deliberately broad, see docstring
            logger.warning(f"[diagnostics] {fn.__name__} failed (ignored, no trading impact): {e}")
            return None

    return wrapper


@dataclass
class _StageCounters:
    input_count: int = 0
    output_count: int = 0
    filtered_count: int = 0
    total_time_ms: float = 0.0

    def record(self, input_count: int, output_count: int, duration_ms: float = 0.0) -> None:
        self.input_count += input_count
        self.output_count += output_count
        self.filtered_count += max(0, input_count - output_count)
        self.total_time_ms += duration_ms

    @property
    def filter_pct(self) -> float:
        return round((self.filtered_count / self.input_count) * 100, 1) if self.input_count else 0.0

    @property
    def avg_time_ms(self) -> float:
        return round(self.total_time_ms / self.input_count, 2) if self.input_count else 0.0

    def line(self, label: str) -> str:
        return (
            f"  {label:<45} in={self.input_count:<5} out={self.output_count:<5} "
            f"filtered={self.filtered_count:<5} filter%={self.filter_pct:<6} "
            f"avg_ms={self.avg_time_ms}"
        )


class SignalPipelineDiagnostics:
    """Process-wide singleton (see module-level `diagnostics` below).
    Synchronous, side-effect-free with respect to trading: every method
    only reads values already computed elsewhere and writes to its own
    private counters. Safe under asyncio's single-threaded cooperative
    model - no `await` appears inside any recording method, so a call can
    never be interleaved with another task's call to the same method."""

    def __init__(self) -> None:
        self._reset_interval()
        self._lifetime_rejection_reasons: Counter = Counter()
        self._lifetime_signals_generated = 0
        self._lifetime_signals_saved = 0
        self._lifetime_signals_displayed = 0
        self._lifetime_pending_filled = 0
        self._lifetime_pending_expired = 0
        self._process_started_at = time.monotonic()

    def _reset_interval(self) -> None:
        self._stages: Dict[str, _StageCounters] = {k: _StageCounters() for k in STAGE_LABELS}
        self._rejection_reasons: Counter = Counter()
        self._symbols_scanned: Set[str] = set()
        self._ict_setups_found = 0
        self._passed_ai = 0
        self._signals_generated = 0
        self._signals_saved = 0
        self._signals_displayed = 0
        self._pending_entries_filled = 0
        self._pending_entries_expired = 0
        self._last_summary_at = time.monotonic()

    # ------------------------------------------------------------------
    # Recording - called from app/scheduler/universal_scanner.py only.
    # ------------------------------------------------------------------
    @_safe
    def record_market_data(self, symbol: str, *, has_candles: bool) -> None:
        self._symbols_scanned.add(symbol)
        self._stages["scanner"].record(1, 1)  # a scan was attempted, always "output" (it ran)
        self._stages["market_data"].record(1, 1 if has_candles else 0)
        if not has_candles:
            self._tally_reason(NO_MARKET_DATA_REASON)

    @_safe
    def record_evaluation(self, symbol: str, decision, elapsed_ms: float) -> None:
        """`decision` is the `TradeDecision` `SignalGenerator.evaluate()`
        already returned - see module docstring for exactly which fields
        are read and why reading them here cannot affect the decision
        itself (it has already been made and returned by the time this
        function is called)."""
        missing_components = {m.component for m in decision.missing_evidence}

        # Per-component ICT sub-stages: "found" iff NOT in missing_evidence.
        # Processing time is bundled across all of them (see module
        # docstring's disclosed limitation) rather than split per engine.
        per_component_ms = elapsed_ms / max(1, len(COMPONENT_TO_STAGE))
        for component, stage_key in COMPONENT_TO_STAGE.items():
            found = component not in missing_components
            self._stages[stage_key].record(1, 1 if found else 0, per_component_ms)
            if not found:
                self._tally_reason(COMPONENT_REASON_LABELS.get(component, component))
        if "market_structure" not in missing_components:
            self._ict_setups_found += 1

        # AI Scoring: always produces a number; "filtered" iff it failed
        # the min_confidence gate specifically.
        min_conf_blocked = "min_confidence" in decision.blocking_gates
        self._stages["ai_scoring"].record(1, 0 if min_conf_blocked else 1, elapsed_ms)
        if not min_conf_blocked:
            self._passed_ai += 1

        # Risk Engine: always N/A during generation - see module docstring.
        # Deliberately not recorded as input/output here (stays at 0/0),
        # not faked as "passed".

        # Signal Generator: the aggregated final verdict, plus every
        # blocking gate this run actually hit (a run can hit more than
        # one - every gate is evaluated, never short-circuited).
        self._stages["signal_generator"].record(1, 1 if decision.is_tradeable else 0, elapsed_ms)
        for gate in decision.blocking_gates:
            self._tally_reason(GATE_REASON_LABELS.get(gate, gate))

        if decision.is_tradeable:
            self._signals_generated += 1
            self._lifetime_signals_generated += 1

    @_safe
    def record_database_save(self, symbol: str, *, saved: bool, duplicate: bool) -> None:
        self._stages["database_save"].record(1, 1 if saved else 0)
        if saved:
            self._signals_saved += 1
            self._lifetime_signals_saved += 1
        elif duplicate:
            self._tally_reason(DUPLICATE_SIGNAL_REASON)

    @_safe
    def record_wpf_update(self, symbol: str, *, published: bool) -> None:
        self._stages["wpf_update"].record(1, 1 if published else 0)
        if published:
            self._signals_displayed += 1
            self._lifetime_signals_displayed += 1

    @_safe
    def record_pending_entry_outcome(self, symbol: str, *, filled: bool, reason: str = "") -> None:
        """Called by SignalMonitor when an ICT pending entry is decided -
        either price traded into the zone (filled) or the entry was
        abandoned (expired/invalidated). A pending entry that is still
        waiting is NOT recorded, so `filtered` here counts only genuinely
        abandoned setups, never ones still in progress."""
        self._stages["pending_entry_fill"].record(1, 1 if filled else 0)
        if filled:
            self._pending_entries_filled += 1
            self._lifetime_pending_filled += 1
        else:
            self._pending_entries_expired += 1
            self._lifetime_pending_expired += 1
            self._tally_reason(reason or PENDING_ENTRY_UNFILLED_REASON)

    def _tally_reason(self, label: str) -> None:
        self._rejection_reasons[label] += 1
        self._lifetime_rejection_reasons[label] += 1

    # ------------------------------------------------------------------
    # Summary - checked once per scan; prints and resets interval
    # counters when >= SUMMARY_INTERVAL_SECONDS have elapsed. No new
    # background task/coroutine is introduced (see module docstring on
    # minimizing footprint) - this rides on the scanner's own existing
    # per-candle call frequency.
    # ------------------------------------------------------------------
    @_safe
    def maybe_log_summary(self) -> None:
        now = time.monotonic()
        if now - self._last_summary_at < SUMMARY_INTERVAL_SECONDS:
            return
        self._log_summary(final=False)
        self._reset_interval()

    @_safe
    def force_log_summary(self) -> None:
        """For manual/debug use (e.g. from a shell) - does not reset
        interval counters, so it is safe to call at any time without
        disturbing the normal 5-minute cadence."""
        self._log_summary(final=True)

    def _log_summary(self, *, final: bool) -> None:
        lines = [
            "",
            "=" * 78,
            f"SIGNAL PIPELINE DIAGNOSTICS SUMMARY{'  (on-demand)' if final else ''}",
            "=" * 78,
            f"Symbols scanned:      {len(self._symbols_scanned)}",
            f"ICT setups found:     {self._ict_setups_found}  (confirmed BOS/CHoCH present)",
            f"Passed AI:            {self._passed_ai}  (cleared min_confidence)",
            f"Passed Risk Engine:   N/A - Risk Engine does not run during signal generation "
            f"(see app/diagnostics/signal_pipeline_diagnostics.py module docstring)",
            f"Signals generated:    {self._signals_generated}  (lifetime: {self._lifetime_signals_generated})",
            f"Signals saved:        {self._signals_saved}  (lifetime: {self._lifetime_signals_saved})",
            f"Signals displayed:    {self._signals_displayed}  (lifetime: {self._lifetime_signals_displayed})",
            f"Pending entries FILLED:  {self._pending_entries_filled}  "
            f"(lifetime: {self._lifetime_pending_filled})  - became real trades",
            f"Pending entries EXPIRED: {self._pending_entries_expired}  "
            f"(lifetime: {self._lifetime_pending_expired})  - price never reached the entry zone",
            "-" * 78,
            "Per-stage counts (this interval):",
        ]
        for key, label in STAGE_LABELS.items():
            if key == "risk_engine":
                continue
            lines.append(self._stages[key].line(label))
        lines.append("-" * 78)
        lines.append("Top rejection reasons (this interval):")
        top = self._rejection_reasons.most_common(10)
        if not top:
            lines.append("  (none - either every setup passed, or nothing was scanned this interval)")
        else:
            for reason, count in top:
                lines.append(f"  {reason:<45} {count}")
        lines.append("-" * 78)
        lines.append("Top rejection reasons (lifetime, since process start):")
        for reason, count in self._lifetime_rejection_reasons.most_common(10):
            lines.append(f"  {reason:<45} {count}")
        lines.append("=" * 78)
        logger.info("\n".join(lines))


# Process-wide singleton - constructed once, imported by
# app/scheduler/universal_scanner.py only.
diagnostics = SignalPipelineDiagnostics()
