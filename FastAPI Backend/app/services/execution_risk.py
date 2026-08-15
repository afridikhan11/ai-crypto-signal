"""
Execution Risk Gate - Live Execution Safety (2026-07-30), Objective 1.

Framework-independent: this module has NO dependency on FastAPI. It is
kept separate from `app/api/v1/endpoints/trading.py` (which imports
FastAPI) specifically so the actual risk DECISION - the part that
matters - can be exercised directly by tests without needing the FastAPI
layer importable at all. This project's own existing test suite already
follows that pattern deliberately (see `tests/test_signal_service_risk.py`'s
docstring: "the async DB-backed `_build_risk_context()` ... is covered by
the integration tests that run against a real database" - the endpoint/
HTTP layer has never been unit-tested here, only the service layer).

`app/api/v1/endpoints/trading.py::execute_signal()` is a thin translator
around `assess_execution_risk()`: it calls this function and turns a
`TradeRiskRejected` into an `HTTPException(400)`. This is the ONE gate a
real order execution passes through - see LIVE_EXECUTION_SAFETY_REPORT.md.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from app.models.signal import Signal
from app.risk.risk_engine import RiskAssessment, RiskEngine
from app.risk.context_builder import build_risk_context, candidate_from_position_size
from app.services.position_sizing import calculate_position_size
from app.services.risk_audit import log_assessment, record_assessment
from app.services.signal_service import (
    SignalService,
    _futures_snapshot_time,
    _get_futures_account,
)

# Stateless - see app/risk/risk_engine.py. One instance reused for every
# execution request.
_risk_engine = RiskEngine()


def _leverage_for(futures_account, symbol: str):
    """This symbol's CONFIGURED exchange leverage, when Binance reported an
    existing position on it. Used only to derive the candidate's initial
    margin. Returns None otherwise - this platform never sets leverage
    itself, so guessing one would fabricate a margin figure."""
    if futures_account is None:
        return None
    for p in getattr(futures_account, "open_positions", ()) or ():
        if str(getattr(p, "symbol", "")).upper() == symbol.upper():
            lev = getattr(p, "leverage", None)
            return int(lev) if lev else None
    return None


# Stay just under the effective-leverage ceiling so float rounding at the
# exact boundary can't flip an in-limit trade back to a rejection.
_LEVERAGE_HEADROOM_SAFETY = 0.98


def _leverage_headroom_notional(futures_account, risk_context) -> Optional[float]:
    """Dollar notional a NEW position may add while keeping TOTAL effective
    leverage under the ceiling that the `effective_leverage` metric enforces:

        headroom = SAFETY * max_leverage * equity - existing_open_notional

    Uses the SAME equity (margin_balance) and open-position notionals
    (qty * mark price) the metric itself uses, so the cap and the metric can
    never disagree. Returns None when there is no exchange snapshot - the
    leverage metric is UNKNOWN then and does not block, so no cap is needed
    (legacy behavior is preserved exactly).
    """
    if futures_account is None:
        return None
    equity = getattr(futures_account, "margin_balance", None) or getattr(
        futures_account, "wallet_balance", None
    )
    if not equity:
        equity = getattr(risk_context, "account_balance", None)
    if not equity or equity <= 0:
        return None
    existing_notional = 0.0
    for p in getattr(futures_account, "open_positions", ()) or ():
        qty = abs(float(getattr(p, "position_amt", 0.0) or 0.0))
        price = getattr(p, "mark_price", None) or getattr(p, "entry_price", None) or 0.0
        existing_notional += qty * float(price)
    max_lev = _risk_engine.limits.max_leverage_percent / 100.0
    headroom = _LEVERAGE_HEADROOM_SAFETY * max_lev * float(equity) - existing_notional
    return max(0.0, headroom)


class TradeRiskRejected(Exception):
    """
    Raised when a trade must NOT proceed to execution - either because
    `RiskEngine` explicitly rejected it (a limit would be breached) or
    because risk could not be assessed at all (no known account balance).
    Plain Python exception with no FastAPI dependency; `extra` carries the
    same portfolio fields (open_risk_percent, exposure_percent, etc.) the
    signal list/detail endpoints already expose, so a caller can surface
    exactly what was breached.
    """

    def __init__(self, message: str, reasons: Optional[List[str]] = None, **extra: Any):
        super().__init__(message)
        self.message = message
        self.reasons: List[str] = reasons or []
        self.extra: Dict[str, Any] = extra


async def assess_execution_risk(signal_service: SignalService, signal: Signal) -> RiskAssessment:
    """
    The single, mandatory Risk Engine gate every real-order execution must
    pass through.

    Reuses the EXACT SAME `PortfolioRiskContext` construction the signal
    list/detail endpoints already use (`SignalService.build_portfolio_risk_context`)
    so the risk_approved/risk_reasons a user sees while browsing a signal
    can never disagree with what actually gates execution - one risk read,
    two callers.

    Returns the `RiskAssessment` on success (`assessment.approved` is
    always True on a normal return - a rejection always raises instead of
    being returned, so a caller can never forget to check `.approved`).
    Raises `TradeRiskRejected` when the account balance itself is unknown
    (risk cannot be assessed at all) or when `RiskEngine` does not approve
    the trade - in both cases, with every reason attached.
    """
    # `committed_only=True`: this gate must measure REAL exposure - what is
    # actually on the exchange right now - not hypothetical exposure. The
    # scanner produces ACTIVE signals continuously, and counting the
    # un-executed ones let purely notional positions consume the exposure
    # budget and block genuine trades. (PENDING_ENTRY already required
    # `executed=True`; ACTIVE did not, which is the asymmetry this closes.)
    # The advisory portfolio-risk display keeps its "if every signal were
    # taken" meaning by leaving this False.
    risk_context = await signal_service.build_portfolio_risk_context(
        exclude_signal_id=signal.id, committed_only=True
    )
    symbol = signal.coin.symbol if getattr(signal, "coin", None) else str(signal.id)

    if not risk_context.is_available:
        logger.warning(
            f"{symbol}: execution BLOCKED - no account balance/risk % available, "
            f"Risk Engine cannot assess this trade."
        )
        raise TradeRiskRejected(
            "Could not assess risk for this trade - no linked account balance, or no risk % configured.",
        )

    # RISK REFERENCE: the ORIGINAL stop, never the live one.
    #
    # `signal.stop_loss` is moved by Trade Management over the life of a
    # trade - to entry_price exactly at breakeven, then trailed. Sizing off
    # it made this gate's answer depend on how far the stop had already
    # travelled: at breakeven `abs(entry - stop)` is 0, position sizing
    # returns None, and this gate rejected the trade with the misleading
    # "unknown account balance, or a degenerate entry/stop pair" - even
    # though the balance was perfectly well known. `initial_stop_loss` is
    # frozen at creation, so the risk this gate measures is the risk the
    # trade was actually taken with.
    # PHASE 3: hand the engine the REAL Binance state (margin balance,
    # per-position initial margin and leverage, total maintenance margin) so
    # Margin Usage / Effective Leverage / Liquidation Ratio evaluate on
    # facts instead of reporting UNKNOWN. Sourced from the SAME cached
    # `get_full_snapshot()` call position sizing already makes - no extra
    # Binance request. If no account is linked, `futures` is None and those
    # three metrics correctly stay UNKNOWN rather than being fabricated.
    futures = await _get_futures_account()
    # LEVERAGE-FIT SIZING: a tight stop under a fixed risk % sizes a position
    # so large it breaches the effective-leverage ceiling and the trade is
    # rejected. Instead, cap the candidate to the exposure headroom that
    # remains under the ceiling, so it is taken at a smaller size (risking less
    # than risk %) rather than not at all. The SAME cap is handed to the sizing
    # below AND to assess_new_trade, so the leverage the engine assesses and
    # the position_size it returns describe one identical position.
    max_notional = _leverage_headroom_notional(futures, risk_context)
    # Only SUPERSEDE the DB-derived portfolio when a real exchange snapshot
    # actually exists. Passing an exchange-backed context built from `None`
    # would silently DISCARD the open positions the risk context already
    # gathered from our own signals, quietly emptying the portfolio and
    # letting a trade through that should have been blocked. With no
    # snapshot we fall through to `assess_new_trade`'s own legacy context
    # construction, which is exactly the pre-Phase-3 behaviour.
    engine_context = None if futures is None else build_risk_context(
        futures_account=futures,
        risk_percent=risk_context.risk_percent,
        signal_risk_by_symbol=getattr(risk_context, "risk_usd_by_symbol", None),
        candidate=candidate_from_position_size(
            symbol,
            calculate_position_size(
                risk_context.account_balance, signal.entry_price,
                signal.initial_stop_loss, risk_context.risk_percent,
                max_notional=max_notional,
            ),
            leverage=_leverage_for(futures, symbol),
        ),
        realized_pnl_today_usd=sum(
            t.get("realized_pnl_usd", 0) or 0 for t in risk_context.closed_trades_today
        ) if risk_context.closed_trades_today is not None else None,
        peak_equity=risk_context.equity_peak,
        fallback_equity=risk_context.account_balance,
        exchange_snapshot_at=_futures_snapshot_time(),
    )

    assessment = _risk_engine.assess_new_trade(
        context=engine_context,
        account_balance=risk_context.account_balance,
        entry_price=signal.entry_price,
        stop_loss=signal.initial_stop_loss,
        take_profit=signal.take_profit,
        risk_percent=risk_context.risk_percent,
        open_positions=list(risk_context.open_positions),
        closed_trades_today=list(risk_context.closed_trades_today),
        equity_peak=risk_context.equity_peak,
        max_notional=max_notional,
    )

    # Permanent, always-on instrumentation of the exposure calculation.
    # When this gate rejects a trade the operator needs the INPUTS, not just
    # the verdict - "exposure 221.5%" alone is unactionable. Logged at debug
    # for an approval and surfaced in the warning below for a rejection.
    logger.debug(
        f"{symbol}: risk inputs | balance={risk_context.account_balance} "
        f"risk%={risk_context.risk_percent} entry={signal.entry_price} "
        f"initial_stop={signal.initial_stop_loss} live_stop={signal.stop_loss} "
        f"committed_positions={len(risk_context.open_positions)} "
        f"open_notional={sum(p.get('notional_usd', 0) for p in risk_context.open_positions):.2f} "
        f"-> exposure={assessment.exposure_percent}% open_risk={assessment.open_risk_percent}%"
    )

    # MANDATORY ADDITION #2 - persist the complete metric snapshot for
    # EVERY assessment, approved or rejected, with the engine version and
    # the provenance of its inputs. Written before the verdict is acted on
    # so a rejection is recorded even though it raises below. Never raises
    # itself - see app/services/risk_audit.py's failure discipline.
    audit_id = await record_assessment(
        assessment, symbol, limits=_risk_engine.limits,
        signal_id=getattr(signal, "id", None),
    )
    log_assessment(assessment, symbol, audit_id)

    if assessment.approved:
        logger.success(
            f"{symbol}: Risk Approved for execution | open_risk={assessment.open_risk_percent}% "
            f"exposure={assessment.exposure_percent}% daily_pnl={assessment.daily_pnl_percent}% "
            f"drawdown={assessment.current_drawdown_percent}%"
        )
        return assessment

    logger.warning(
        f"{symbol}: Risk REJECTED for execution | reasons={assessment.reasons} | "
        f"inputs: balance={risk_context.account_balance} risk%={risk_context.risk_percent} "
        f"entry={signal.entry_price} initial_stop={signal.initial_stop_loss} "
        f"(live_stop={signal.stop_loss}) committed_positions={len(risk_context.open_positions)} "
        f"open_notional={sum(p.get('notional_usd', 0) for p in risk_context.open_positions):.2f} "
        f"exposure={assessment.exposure_percent}% open_risk={assessment.open_risk_percent}%"
    )
    raise TradeRiskRejected(
        "Risk Engine rejected this trade - no order was placed.",
        reasons=assessment.reasons,
        open_risk_percent=assessment.open_risk_percent,
        exposure_percent=assessment.exposure_percent,
        daily_pnl_percent=assessment.daily_pnl_percent,
        current_drawdown_percent=assessment.current_drawdown_percent,
    )
