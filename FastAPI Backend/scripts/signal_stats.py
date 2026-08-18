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
import time

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

# Binance income-ledger types we care about for the real-money view. Every
# `income` value is signed the way the account is credited/debited: a
# COMMISSION is negative (a cost), FUNDING_FEE is +/- depending on side.
_REALIZED = "REALIZED_PNL"
_COMMISSION = "COMMISSION"
_FUNDING = "FUNDING_FEE"
# Binance caps one /fapi/v1/income query to a 7-day window, so a longer
# history is paged in windows of this size.
_INCOME_WINDOW_MS = 7 * 24 * 60 * 60 * 1000

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


def _aggregate_income(items) -> dict:
    """Fold a flat list of income-ledger entries into totals and a per-symbol
    breakdown. `income` is already signed by Binance (costs negative), so a
    plain sum gives the real net. Pure function - no I/O - so it is trivially
    testable and reused by the account-truth print below."""
    totals = {_REALIZED: 0.0, _COMMISSION: 0.0, _FUNDING: 0.0, "OTHER": 0.0}
    per_symbol: dict[str, dict] = {}
    for it in items:
        bucket = it.income_type if it.income_type in totals else "OTHER"
        totals[bucket] += it.income
        if it.symbol:
            sym = per_symbol.setdefault(
                it.symbol, {_REALIZED: 0.0, _COMMISSION: 0.0, _FUNDING: 0.0}
            )
            if it.income_type in sym:
                sym[it.income_type] += it.income
    # Trading net EXCLUDES "OTHER" (transfers, deposits, bonuses) - those are
    # capital movements, not performance, and would distort the bottom line.
    net = totals[_REALIZED] + totals[_COMMISSION] + totals[_FUNDING]
    return {"totals": totals, "per_symbol": per_symbol, "net": net}


async def _fetch_income_windowed(svc, start_ms: int, end_ms: int) -> list:
    """Page the futures income ledger in <=7-day windows (Binance's cap) and
    return the concatenated entries."""
    items: list = []
    cursor = start_ms
    while cursor < end_ms:
        chunk_end = min(cursor + _INCOME_WINDOW_MS, end_ms)
        chunk = await svc.get_income_history(
            limit=1000, start_time=cursor, end_time=chunk_end
        )
        items.extend(chunk)
        cursor = chunk_end + 1
    return items


async def _print_exchange_truth(session) -> None:
    """Real money, straight from the exchange: realized PnL, commissions and
    funding actually booked to the account - the figures the price-move %
    above cannot show (it ignores fees, funding and fill slippage).

    Degrades honestly: if no API credentials are saved, the exchange is
    unreachable, or the account has no futures income in the window, it prints
    why and returns rather than failing the whole report."""
    print("\n=== EXCHANGE TRUTH (real realized P/L, fees & funding) ===")

    earliest = (
        await session.execute(
            select(func.min(Signal.executed_at)).where(Signal.executed.is_(True))
        )
    ).scalar()
    if earliest is None:
        print("  (no executed trades yet - nothing to reconcile)")
        return

    try:
        from app.services import binance_credentials

        svc = binance_credentials.build_service_from_saved()
    except FileNotFoundError:
        print("  (skipped - no Binance API credentials saved on this host)")
        return
    except Exception as exc:  # decrypt/config error - never crash the report
        print(f"  (skipped - could not build account client: {exc})")
        return

    # One day of slack before the first fill covers any funding booked just
    # ahead of entry; end at 'now'.
    start_ms = int(earliest.timestamp() * 1000) - 24 * 60 * 60 * 1000
    end_ms = int(time.time() * 1000)

    try:
        async with svc:
            items = await _fetch_income_windowed(svc, start_ms, end_ms)
    except Exception as exc:
        print(f"  (skipped - exchange income fetch failed: {exc})")
        return

    if not items:
        print(
            "  (no futures income returned for the trade window - if you DID "
            "trade, the account client's futures host may differ from the "
            "bot's; check BINANCE_FUTURES_TESTNET_URL)"
        )
        return

    agg = _aggregate_income(items)
    t = agg["totals"]
    print(f"  Realized P/L (gross)    : {t[_REALIZED]:+.4f} USDT")
    print(f"  Commissions (fees)      : {t[_COMMISSION]:+.4f} USDT")
    print(f"  Funding                 : {t[_FUNDING]:+.4f} USDT")
    if t["OTHER"]:
        print(f"  Other (transfers/bonus) : {t['OTHER']:+.4f} USDT  (not counted in NET)")
    print(f"  {'-' * 40}")
    print(f"  NET trading (fees+funding, excl. transfers): {agg['net']:+.4f} USDT")

    per_symbol = agg["per_symbol"]
    if per_symbol:
        print("\n  Per-symbol net (realized + fees + funding):")
        rows = sorted(
            per_symbol.items(),
            key=lambda kv: kv[1][_REALIZED] + kv[1][_COMMISSION] + kv[1][_FUNDING],
        )
        for sym, s in rows:
            sym_net = s[_REALIZED] + s[_COMMISSION] + s[_FUNDING]
            print(
                f"    {sym:<10} net {sym_net:+9.4f}  "
                f"(realized {s[_REALIZED]:+.2f} | fees {s[_COMMISSION]:+.2f} | "
                f"funding {s[_FUNDING]:+.2f})"
            )
    print(
        "\n  NOTE: this is the real account bottom line; the price-move % above "
        "excludes fees, funding and fill slippage, so it reads better than this."
    )


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

        # Real-money reconciliation from the exchange ledger (fees + funding +
        # slippage included). Degrades honestly if the exchange isn't reachable.
        await _print_exchange_truth(session)


if __name__ == "__main__":
    asyncio.run(main())
