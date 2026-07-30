"""
RiskContext assembly from REAL Binance Futures state (Phase 3, 2026-07-30).

This is the only place that turns exchange + database facts into the
`RiskContext` the engine consumes. It FETCHES NOTHING itself - callers
hand it an already-retrieved `FuturesAccountInfo` (from
`signal_service._get_futures_account()`, which reuses the same cached
`get_full_snapshot()` call position sizing already makes, so no additional
Binance request is introduced anywhere).

AUTHORITATIVE SOURCE PER FACT
--------------------------------------------------------------------------
  margin / leverage / maintenance / mark price  -> BINANCE. It owns them;
      our database has never known them.
  risk_usd (loss if the stop is hit)            -> OUR SIGNALS. Binance has
      no idea where our stops are.

A position that exists on the exchange but has no matching signal in our
database (opened manually, or by an earlier version) therefore has a
KNOWN notional and margin but an UNKNOWN risk_usd. That is reported
honestly: `open_risk` and `cluster_concentration` degrade to UNKNOWN
rather than pretending the position carries zero risk, while
`margin_usage`, `effective_leverage` and `margin_ratio` still evaluate
normally because their inputs are complete.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from app.risk.context import PositionRisk, RiskContext


def _position_from_binance(p: Any, risk_usd: Optional[float]) -> PositionRisk:
    """Map one live Binance futures position onto the engine's shape.

    `notional` uses MARK price when Binance reports it (mark price is what
    margin and liquidation are actually computed against), falling back to
    the position's entry price. Quantity is `abs(position_amt)` because a
    short's amount is negative and exposure is direction-agnostic.
    """
    qty = abs(float(getattr(p, "position_amt", 0.0) or 0.0))
    price = getattr(p, "mark_price", None) or getattr(p, "entry_price", None) or 0.0
    notional = qty * float(price)

    # Real `positionInitialMargin` (cross) / `isolatedWallet` (isolated)
    # when Binance reported it. Only when it is absent do we derive it from
    # notional/leverage - and if leverage is also missing it stays None, so
    # `margin_usage` reports UNKNOWN instead of inventing a figure.
    margin = getattr(p, "margin", None)
    leverage = getattr(p, "leverage", None) or None
    if margin is None and leverage:
        margin = notional / float(leverage)

    return PositionRisk(
        symbol=str(getattr(p, "symbol", "UNKNOWN")).upper(),
        notional_usd=notional,
        risk_usd=risk_usd,
        initial_margin_usd=float(margin) if margin is not None else None,
        leverage=int(leverage) if leverage else None,
    )


def build_risk_context(
    futures_account: Any,
    risk_percent: Optional[float],
    signal_risk_by_symbol: Optional[Dict[str, float]] = None,
    candidate: Optional[PositionRisk] = None,
    realized_pnl_today_usd: Optional[float] = None,
    peak_equity: Optional[float] = None,
    correlation_clusters: Optional[Dict[str, int]] = None,
    correlation_warning: Optional[str] = None,
    fallback_equity: Optional[float] = None,
    exchange_snapshot_at: Optional[Any] = None,
) -> RiskContext:
    """
    Assemble a `RiskContext` from a real Binance futures snapshot.

    `signal_risk_by_symbol` maps SYMBOL -> risk_usd, computed by the caller
    from `Signal.initial_stop_loss` (never the live trailed stop - see
    Phase 1). A symbol absent from that map yields `risk_usd=None`, which
    is what makes an externally-opened position honestly UNKNOWN rather
    than silently risk-free.

    `fallback_equity` is used only when no futures account is available at
    all (e.g. no linked account); it keeps pre-2.0.0 callers working with
    wallet balance while every margin/leverage metric correctly reports
    UNKNOWN.
    """
    risk_map = {k.upper(): v for k, v in (signal_risk_by_symbol or {}).items()}

    if futures_account is None:
        return RiskContext(
            equity=fallback_equity,
            wallet_balance=fallback_equity,
            risk_percent=risk_percent,
            candidate=candidate,
            realized_pnl_today_usd=realized_pnl_today_usd,
            peak_equity=peak_equity,
            correlation_clusters=dict(correlation_clusters or {}),
            correlation_warning=correlation_warning,
            has_exchange_data=False,
            context_source="database_fallback",
        )

    raw_positions: Sequence[Any] = getattr(futures_account, "open_positions", ()) or ()
    positions: List[PositionRisk] = [
        _position_from_binance(p, risk_map.get(str(getattr(p, "symbol", "")).upper()))
        for p in raw_positions
        if abs(float(getattr(p, "position_amt", 0.0) or 0.0)) > 0
    ]

    # EQUITY = margin_balance (wallet + unrealized PnL). This is the same
    # base Binance itself uses for margin ratio, so our numbers and the
    # exchange's are directly comparable. `wallet_balance` is retained
    # separately because the Account screen displays it.
    equity = getattr(futures_account, "margin_balance", None)
    if not equity:
        equity = getattr(futures_account, "wallet_balance", None) or fallback_equity

    return RiskContext(
        equity=equity,
        wallet_balance=getattr(futures_account, "wallet_balance", None),
        available_balance=getattr(futures_account, "available_balance", None),
        unrealized_pnl=getattr(futures_account, "unrealized_pnl", None),
        total_maintenance_margin=getattr(futures_account, "total_maintenance_margin", None),
        open_positions=tuple(positions),
        candidate=candidate,
        risk_percent=risk_percent,
        realized_pnl_today_usd=realized_pnl_today_usd,
        peak_equity=peak_equity,
        correlation_clusters=dict(correlation_clusters or {}),
        correlation_warning=correlation_warning,
        has_exchange_data=True,
        context_source="binance_exchange",
        exchange_snapshot_at=exchange_snapshot_at,
    )


def candidate_from_position_size(symbol: str, size: Optional[Dict[str, float]],
                                 leverage: Optional[int] = None) -> Optional[PositionRisk]:
    """Turn a `calculate_position_size()` result into the candidate leg.

    Initial margin for a not-yet-open position can only be derived from
    leverage, since Binance has nothing to report yet. Without a known
    leverage it stays None and `margin_usage` reports UNKNOWN - the
    honest answer, because this platform never sets leverage itself
    (`binance_trading_service.py` documents that explicitly).
    """
    if not size:
        return None
    notional = float(size.get("notional_usd", 0.0) or 0.0)
    return PositionRisk(
        symbol=symbol.upper(),
        notional_usd=notional,
        risk_usd=float(size.get("risk_usd", 0.0) or 0.0),
        initial_margin_usd=(notional / leverage) if leverage else None,
        leverage=leverage,
        is_candidate=True,
    )
