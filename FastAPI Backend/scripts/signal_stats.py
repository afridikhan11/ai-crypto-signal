"""
Signal performance summary — win/loss/win-rate at a glance.

Run inside the app container:

    docker compose exec app python scripts/signal_stats.py

Prints, over ALL signals and (separately) over only EXECUTED signals:
  - a count of every status,
  - wins / losses / cancelled and the win-rate,
  - average confidence and risk:reward,
and then lists every executed trade that reached a decided outcome.

Win-rate uses the same honest denominator the app's /stats endpoint does:
only DECIDED trades count (TP_HIT, STOPPED, CANCELLED). PENDING_ENTRY (not
started) and EXPIRED (never entered) are excluded so trades that were never
actually taken cannot inflate or deflate the number.
"""
from __future__ import annotations

import asyncio
import os
import sys

# Allow running as `python scripts/signal_stats.py`: Python puts scripts/ on
# sys.path, not the project root, so add the parent (the app package root).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.models.coin import Coin
from app.models.signal import (
    DECIDED_STATUSES,
    Direction,
    Signal,
    SignalStatus,
)

# The statuses we tally, in a sensible reading order.
_ORDER = [
    SignalStatus.TP_HIT,
    SignalStatus.STOPPED,
    SignalStatus.CANCELLED,
    SignalStatus.EXPIRED,
    SignalStatus.ACTIVE,
    SignalStatus.PENDING_ENTRY,
]
_LABEL = {
    SignalStatus.TP_HIT: "WIN  (TP hit)",
    SignalStatus.STOPPED: "LOSS (stopped)",
    SignalStatus.CANCELLED: "CANCELLED (early exit)",
    SignalStatus.EXPIRED: "EXPIRED (never filled)",
    SignalStatus.ACTIVE: "OPEN (active)",
    SignalStatus.PENDING_ENTRY: "PENDING (waiting fill)",
}


async def _counts(session, executed_only: bool) -> dict:
    stmt = select(Signal.status, func.count(Signal.id)).group_by(Signal.status)
    if executed_only:
        stmt = stmt.where(Signal.executed.is_(True))
    rows = (await session.execute(stmt)).all()
    return {status: count for status, count in rows}


def _pnl_pct(signal) -> float | None:
    """Realised price move as a % of entry, from the level the trade ACTUALLY
    exited at - take_profit for TP_HIT, the (possibly TRAILED) stop_loss for
    STOPPED. This is the honest win/loss: a STOPPED trade whose stop was
    trailed into profit is a WIN, even though its status is 'STOPPED'. Returns
    None for statuses whose exit price the database does not store (CANCELLED)."""
    entry = signal.actual_fill_price or signal.entry_price
    if not entry:
        return None
    if signal.status is SignalStatus.TP_HIT:
        exit_price = signal.take_profit
    elif signal.status is SignalStatus.STOPPED:
        exit_price = signal.stop_loss  # the trailed stop at the moment it hit
    else:
        return None
    if signal.direction is Direction.SHORT:
        return (entry - exit_price) / entry * 100.0
    return (exit_price - entry) / entry * 100.0


def _print_block(title: str, counts: dict) -> None:
    wins = counts.get(SignalStatus.TP_HIT, 0)
    losses = counts.get(SignalStatus.STOPPED, 0)
    cancelled = counts.get(SignalStatus.CANCELLED, 0)
    decided = wins + losses + cancelled
    win_rate = (wins / decided * 100.0) if decided else 0.0

    print(f"\n=== {title} ===")
    for status in _ORDER:
        print(f"  {_LABEL[status]:<24}: {counts.get(status, 0)}")
    print(f"  {'-' * 40}")
    print(f"  Decided trades          : {decided}")
    print(f"  Win-rate                : {win_rate:.1f}%  ({wins}W / {losses}L / {cancelled}C)")


async def main() -> None:
    async with AsyncSessionLocal() as session:
        total = (await session.execute(select(func.count(Signal.id)))).scalar_one()
        avg_conf = (await session.execute(select(func.avg(Signal.confidence)))).scalar() or 0.0
        avg_rr = (await session.execute(select(func.avg(Signal.risk_reward)))).scalar() or 0.0

        print("========================================")
        print(" SIGNAL PERFORMANCE SUMMARY")
        print("========================================")
        print(f" Total signals ever      : {total}")
        print(f" Avg confidence          : {avg_conf:.1f}")
        print(f" Avg risk:reward         : {avg_rr:.2f}")

        _print_block("ALL SIGNALS (paper + executed)", await _counts(session, executed_only=False))
        _print_block("EXECUTED ONLY (real testnet trades)", await _counts(session, executed_only=True))

        # Per-trade list of executed, decided trades (the real outcomes).
        rows = (
            await session.execute(
                select(Signal, Coin.symbol)
                .join(Coin, Signal.coin_id == Coin.id)
                .where(
                    Signal.executed.is_(True),
                    Signal.status.in_(DECIDED_STATUSES),
                )
                .order_by(Signal.closed_at.asc().nullslast())
            )
        ).all()

        print("\n=== EXECUTED DECIDED TRADES (by ACTUAL profit/loss) ===")
        if not rows:
            print("  (none yet — no executed trade has reached TP/SL/cancel)")
        real_win = real_loss = 0
        total_pnl = 0.0
        for signal, symbol in rows:
            pnl = _pnl_pct(signal)
            if pnl is None:
                tag, pnl_str = "EARLY", "   n/a"
            else:
                total_pnl += pnl
                if pnl > 0:
                    real_win += 1
                    tag = "WIN "
                else:
                    real_loss += 1
                    tag = "LOSS"
                pnl_str = f"{pnl:+6.2f}%"
            print(
                f"  {symbol:<10} {signal.direction.value:<5} "
                f"{signal.status.value:<10} {tag} PnL {pnl_str} | conf {signal.confidence}"
            )

        decided_by_pnl = real_win + real_loss
        true_wr = (real_win / decided_by_pnl * 100.0) if decided_by_pnl else 0.0
        print(f"  {'-' * 50}")
        print(
            f"  TRUE win-rate (by P/L, TP_HIT+STOPPED): {true_wr:.1f}%  "
            f"({real_win}W / {real_loss}L)"
        )
        print(
            f"  Sum of price-move P/L on those trades : {total_pnl:+.2f}%  "
            f"(price move, not account % - size varies per trade)"
        )
        print("  EARLY = CANCELLED structure exit; its exit price is not stored.")


if __name__ == "__main__":
    asyncio.run(main())
