"""
RiskContext - everything FETCHED, nothing computed.

STATUS (2026-07-30): PRODUCTION. Preserves this project's established
"services fetch, engines compute" separation: the engine and every metric
below it are pure functions over this object and perform no I/O.

EQUITY (2.0.0 change, deliberate and documented)
--------------------------------------------------------------------------
`equity` is `margin_balance` (= wallet_balance + unrealized_pnl), NOT
`wallet_balance`. Every risk ratio is measured against it. The old
wallet-only denominator excluded open PnL, so a portfolio in profit
understated its ratios and one in loss overstated them, and neither
matched Binance's own margin-ratio view. `wallet_balance` is retained
because the Account screen displays it.

AUTHORITATIVE SOURCES - each fact comes from whoever actually owns it:
  - margin / leverage / liquidation -> REAL Binance positions. Binance
    knows these; our database does not.
  - risk_usd -> our own signals. Binance has no idea where our stops are.
  - realized PnL today -> Binance income history (REALIZED_PNL).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class PositionRisk:
    """One open (or candidate) position, normalised."""

    symbol: str
    notional_usd: float
    # Loss if this position's ORIGINAL stop is hit. None when the stop is
    # unknown (e.g. a position opened outside this platform) - never
    # guessed, and excluded from open-risk rather than counted as zero.
    risk_usd: Optional[float] = None
    # Collateral actually locked. Real `positionInitialMargin`/`isolatedWallet`
    # when the position exists; derived as notional/leverage for a candidate.
    initial_margin_usd: Optional[float] = None
    leverage: Optional[int] = None
    is_candidate: bool = False


@dataclass(frozen=True)
class RiskContext:
    # ---- Account (from FuturesAccountInfo) ----
    equity: Optional[float] = None                 # margin_balance
    wallet_balance: Optional[float] = None
    available_balance: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    total_maintenance_margin: Optional[float] = None

    # ---- Positions ----
    open_positions: Tuple[PositionRisk, ...] = ()
    candidate: Optional[PositionRisk] = None
    # Whether a real exchange snapshot backs this context. WITHOUT it,
    # "no open positions" is indistinguishable from "we could not look",
    # and an empty sum would report 0% margin usage - claiming safety we
    # have not verified. Metrics that depend on exchange state check this
    # and report UNKNOWN instead.
    has_exchange_data: bool = False
    # PROVENANCE (Phase 4). Persisted with every decision so a historical
    # verdict can be interpreted: the same "approved" means very different
    # things depending on whether the engine could actually see the account.
    #   "binance_exchange"  - a real futures snapshot backed this decision
    #   "database_fallback" - no snapshot; margin/leverage/liquidation UNKNOWN
    context_source: str = "database_fallback"
    # When the exchange snapshot was FETCHED. None for a fallback decision.
    # The gap between this and the decision time is the data's staleness.
    exchange_snapshot_at: Optional[Any] = None

    # ---- Policy ----
    risk_percent: Optional[float] = None

    # ---- Time-series / history ----
    realized_pnl_today_usd: Optional[float] = None
    peak_equity: Optional[float] = None            # None => Drawdown is UNKNOWN

    # ---- Correlation (symbol -> cluster id), from CorrelationEngine ----
    correlation_clusters: Dict[str, int] = field(default_factory=dict)
    correlation_warning: Optional[str] = None

    @property
    def is_available(self) -> bool:
        """Whether risk can be assessed at all."""
        return self.equity is not None and self.equity > 0

    def all_positions(self) -> List[PositionRisk]:
        """Open positions plus the candidate being assessed."""
        positions = list(self.open_positions)
        if self.candidate is not None:
            positions.append(self.candidate)
        return positions

    def total(self, attr: str) -> Optional[float]:
        """Sum an attribute across all positions, or None if ANY contributor
        is unknown - a partial sum would understate the true total and is
        exactly the kind of silent falsehood this engine must not produce."""
        values = [getattr(p, attr) for p in self.all_positions()]
        if any(v is None for v in values):
            return None
        return float(sum(values))
