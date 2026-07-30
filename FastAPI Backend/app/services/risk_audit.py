"""
Risk audit persistence (Phase 4, 2026-07-30).

Two responsibilities, both write-mostly:

  1. `record_assessment()` - Mandatory Addition #2. Persists the complete
     metric snapshot for EVERY assessment, approved or rejected, exactly
     as it was computed. Never recomputed later: a recomputation would use
     a different account state and possibly different rules, which is what
     an audit trail exists to prevent.

  2. `record_equity_snapshot()` / `get_peak_equity()` - the equity time
     series Drawdown needs. Without it there is no honest high-water mark
     and the metric correctly reports UNKNOWN.

FAILURE DISCIPLINE: writing an audit row must NEVER change a trading
decision. The decision has already been made by the time these run, so a
database problem here is logged loudly and swallowed - refusing a trade
because its audit row failed to write would be a worse outcome than the
missing row. This mirrors `trading_control_service.expire_armed_pending_entries`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.equity_snapshot import EquitySnapshot
from app.models.risk_assessment import RiskAssessmentRecord


async def record_assessment(
    assessment: Any,
    symbol: str,
    limits: Any = None,
    signal_id: Optional[uuid.UUID] = None,
) -> Optional[uuid.UUID]:
    """Persist one risk decision. Returns the audit row id, or None if the
    write failed (never raises - see the module docstring)."""
    try:
        snapshot = assessment.metric_snapshot(limits)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"{symbol}: could not build risk metric snapshot: {e}")
        return None

    try:
        async with AsyncSessionLocal() as session:
            row = RiskAssessmentRecord(
                signal_id=signal_id,
                symbol=symbol.upper(),
                approved=bool(assessment.approved),
                risk_engine_version=assessment.risk_engine_version,
                context_source=assessment.context_source,
                exchange_snapshot_timestamp=assessment.exchange_snapshot_at,
                snapshot=snapshot,
            )
            session.add(row)
            await session.commit()
            return row.id
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"{symbol}: risk assessment audit row could not be written ({e}). The "
            f"decision itself is unaffected and was already logged."
        )
        return None


async def record_equity_snapshot(
    equity: float,
    wallet_balance: Optional[float] = None,
    unrealized_pnl: Optional[float] = None,
    total_maintenance_margin: Optional[float] = None,
    environment: Optional[str] = None,
    captured_at: Optional[datetime] = None,
) -> bool:
    """Append one point to the equity curve. Called from the monitor poll."""
    if equity is None or equity <= 0:
        # A zero/absent equity is not a data point - recording it would put
        # a false trough in the curve and corrupt the peak.
        return False
    try:
        async with AsyncSessionLocal() as session:
            session.add(EquitySnapshot(
                captured_at=captured_at or datetime.now(timezone.utc),
                equity=float(equity),
                wallet_balance=wallet_balance,
                unrealized_pnl=unrealized_pnl,
                total_maintenance_margin=total_maintenance_margin,
                environment=environment,
            ))
            await session.commit()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Equity snapshot could not be recorded: {e}")
        return False


async def get_peak_equity(environment: Optional[str] = None) -> Optional[float]:
    """
    The highest equity ever OBSERVED, from the real series.

    Returns None when no history exists yet - which is what keeps Drawdown
    honestly UNKNOWN instead of reporting 0% and implying "no drawdown"
    when the truth is "we have not measured one".

    Scoped by environment so a testnet equity curve can never contribute a
    peak to a mainnet drawdown, or vice versa.
    """
    try:
        async with AsyncSessionLocal() as session:
            stmt = select(EquitySnapshot.equity)
            if environment:
                stmt = stmt.where(EquitySnapshot.environment == environment)
            stmt = stmt.order_by(EquitySnapshot.equity.desc()).limit(1)
            return (await session.execute(stmt)).scalar_one_or_none()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Could not read peak equity: {e}")
        return None


def log_assessment(assessment: Any, symbol: str, audit_id: Optional[uuid.UUID] = None) -> None:
    """
    Structured audit log line for every decision, approved or rejected.

    Carries the engine version, the provenance of the inputs and the audit
    row id, so a log line alone is enough to find and interpret the stored
    snapshot later.
    """
    verdict = "APPROVED" if assessment.approved else "REJECTED"
    metrics = " ".join(
        f"{m.name}={m.status.value}"
        + (f"({m.value})" if m.value is not None else "")
        for m in assessment.metrics
    )
    line = (
        f"RISK AUDIT | {symbol} | {verdict} | engine={assessment.risk_engine_version} "
        f"| source={assessment.context_source} "
        f"| snapshot_at={assessment.exchange_snapshot_at} "
        f"| audit_id={audit_id} | {metrics}"
    )
    if assessment.approved:
        logger.info(line)
    else:
        logger.warning(line)
    if assessment.unknown_notices:
        logger.warning(
            f"RISK AUDIT | {symbol} | decided with {len(assessment.unknown_notices)} "
            f"UNEVALUATED metric(s): {assessment.unknown_notices}"
        )
