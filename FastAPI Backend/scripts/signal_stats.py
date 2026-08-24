"""
Signal performance summary — win/loss/win-rate at a glance.

Run inside the app container:

    docker compose exec app python scripts/signal_stats.py
    docker compose exec app python scripts/signal_stats.py --since 2026-08-25

`--since YYYY-MM-DD` restricts EVERY section (counts, trade list, exchange
truth) to signals CREATED on/after that UTC date - so a rule-change era can be
measured cleanly without deleting any history. Exchange-truth in since-mode
attributes only trades OPENED in the era; the tail income of trades opened
before it falls into the "manual/non-bot" remainder and is labelled as such.

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
# Slack around a bot trade's attribution window: the entry commission is booked
# ~at entry, and the closing realized-PnL + commission (bot OR manual close) can
# settle a little after. Generous on the exit side to catch that lag.
_ENTRY_BUFFER_MS = 2 * 60 * 1000
_EXIT_BUFFER_MS = 5 * 60 * 1000

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


async def _counts(session, executed_only: bool, since=None) -> dict:
    stmt = select(Signal.status, func.count(Signal.id)).group_by(Signal.status)
    if executed_only:
        stmt = stmt.where(Signal.executed.is_(True))
    if since is not None:
        stmt = stmt.where(Signal.created_at >= since)
    rows = (await session.execute(stmt)).all()
    return {status: count for status, count in rows}


def _move_pct(entry: float, exit_price: float, direction) -> float:
    if direction is Direction.SHORT:
        return (entry - exit_price) / entry * 100.0
    return (exit_price - entry) / entry * 100.0


def _pnl_pct(signal) -> float | None:
    """Realised price move as a % of entry, from the level the trade ACTUALLY
    exited at - take_profit for TP_HIT, the (possibly TRAILED) stop_loss for
    STOPPED. This is the honest win/loss: a STOPPED trade whose stop was
    trailed into profit is a WIN, even though its status is 'STOPPED'. Returns
    None for statuses whose exit price the database does not store (CANCELLED).

    Partial-TP aware: when TP1 fired (tp1_done), the realized move is the
    blend of the banked fraction at tp1_price and the remainder at the final
    exit - so a runner stopped at breakeven after a TP1 partial shows its real
    ~+1%, not a misleading +0.00%."""
    entry = signal.actual_fill_price or signal.entry_price
    if not entry:
        return None
    if signal.status is SignalStatus.TP_HIT:
        exit_price = signal.take_profit
    elif signal.status is SignalStatus.STOPPED:
        exit_price = signal.stop_loss  # the trailed stop at the moment it hit
    else:
        return None
    runner_move = _move_pct(entry, exit_price, signal.direction)
    if getattr(signal, "tp1_done", False) and getattr(signal, "tp1_price", None):
        from app.core.config import get_settings

        fraction = get_settings().signal_tp1_fraction
        tp1_move = _move_pct(entry, signal.tp1_price, signal.direction)
        return fraction * tp1_move + (1 - fraction) * runner_move
    return runner_move


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


def _in_bot_window(symbol: str, t: int, windows: dict) -> bool:
    """True if ledger time `t` falls inside any attribution window the bot held
    `symbol` (with entry/exit slack). Pure - trivially testable."""
    for start, end in windows.get(symbol, ()):
        if start - _ENTRY_BUFFER_MS <= t <= end + _EXIT_BUFFER_MS:
            return True
    return False


def _split_bot_income(items, windows: dict):
    """Partition ledger entries into (bot, other). A row is the bot's only if
    its symbol matches a symbol the bot OPENED a trade on AND its time lands in
    one of that symbol's attribution windows - so fully-manual trades (symbols
    the bot never opened, or a bot symbol before its first bot entry) fall into
    `other`."""
    bot, other = [], []
    for it in items:
        if it.symbol and _in_bot_window(it.symbol.upper(), it.time, windows):
            bot.append(it)
        else:
            other.append(it)
    return bot, other


def _windows_from_entries(entry_rows, now_ms: int) -> dict:
    """Build per-symbol attribution windows from the bot's entry times. Each
    window runs from one bot entry to the NEXT bot entry on the same symbol (or
    'now' for the last).

    Anchored on ENTRIES, deliberately NOT [entry, closed_at]: a position the bot
    opened may have been closed MANUALLY on the exchange, and that realized PnL
    is booked AFTER the bot's own closed_at (often a premature CANCELLED
    reconcile). Attributing by "the bot opened this symbol at time T, until it
    opened the next one" keeps a manually-closed bot trade counted as the bot's,
    while a symbol the bot never opened gets no window at all. Pure/testable."""
    entries: dict[str, list] = {}
    for symbol, entry_ms in entry_rows:
        entries.setdefault(symbol.upper(), []).append(entry_ms)
    windows: dict[str, list] = {}
    for symbol, ts in entries.items():
        ts.sort()
        windows[symbol] = [
            (ts[i], ts[i + 1] if i + 1 < len(ts) else now_ms) for i in range(len(ts))
        ]
    return windows


async def _load_bot_windows(session, since=None) -> dict:
    """Entry-anchored attribution windows for every trade the bot placed.
    With `since`, only trades OPENED on/after it build windows - the era's own
    trades; older trades' tail income then lands in the non-bot remainder."""
    stmt = (
        select(Coin.symbol, Signal.executed_at)
        .join(Coin, Signal.coin_id == Coin.id)
        .where(Signal.executed.is_(True), Signal.executed_at.isnot(None))
    )
    if since is not None:
        stmt = stmt.where(Signal.executed_at >= since)
    rows = (await session.execute(stmt)).all()
    entry_rows = [(symbol, int(executed_at.timestamp() * 1000)) for symbol, executed_at in rows]
    return _windows_from_entries(entry_rows, int(time.time() * 1000))


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


async def _print_exchange_truth(session, since=None) -> None:
    """Real money, straight from the exchange: realized PnL, commissions and
    funding actually booked to the account - the figures the price-move %
    above cannot show (it ignores fees, funding and fill slippage).

    Degrades honestly: if no API credentials are saved, the exchange is
    unreachable, or the account has no futures income in the window, it prints
    why and returns rather than failing the whole report."""
    era = f" - SINCE {since.date()}" if since is not None else ""
    print(f"\n=== EXCHANGE TRUTH (real realized P/L, fees & funding){era} ===")

    earliest_stmt = select(func.min(Signal.executed_at)).where(Signal.executed.is_(True))
    if since is not None:
        earliest_stmt = earliest_stmt.where(Signal.executed_at >= since)
    earliest = (await session.execute(earliest_stmt)).scalar()
    if earliest is None:
        print("  (no executed trades in this window - nothing to reconcile)")
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

    # Attribute the ledger to the BOT's own trades vs everything else (your
    # manual activity), so the bot's real scorecard is isolated.
    windows = await _load_bot_windows(session, since=since)
    bot_items, other_items = _split_bot_income(items, windows)
    bot = _aggregate_income(bot_items)
    account = _aggregate_income(items)
    bt = bot["totals"]

    print("  --- BOT-ONLY (trades the bot opened; a bot trade you closed manually still counts) ---")
    print(f"  Realized P/L (gross)    : {bt[_REALIZED]:+.4f} USDT")
    print(f"  Commissions (fees)      : {bt[_COMMISSION]:+.4f} USDT")
    print(f"  Funding                 : {bt[_FUNDING]:+.4f} USDT")
    print(f"  {'-' * 40}")
    print(f"  NET (bot, after fees+funding): {bot['net']:+.4f} USDT")

    if bot["per_symbol"]:
        print("\n  Per-symbol BOT net (realized + fees + funding):")
        rows = sorted(
            bot["per_symbol"].items(),
            key=lambda kv: kv[1][_REALIZED] + kv[1][_COMMISSION] + kv[1][_FUNDING],
        )
        for sym, s in rows:
            sym_net = s[_REALIZED] + s[_COMMISSION] + s[_FUNDING]
            print(
                f"    {sym:<10} net {sym_net:+9.4f}  "
                f"(realized {s[_REALIZED]:+.2f} | fees {s[_COMMISSION]:+.2f} | "
                f"funding {s[_FUNDING]:+.2f})"
            )

    # Context: the whole account, and what the non-bot (manual) part contributed.
    manual_net = account["net"] - bot["net"]
    print(f"\n  Account-wide NET (bot + your manual trades): {account['net']:+.4f} USDT")
    print(f"  => Manual / non-bot activity              : {manual_net:+.4f} USDT")
    print(
        "\n  NOTE: 'bot-only' attributes income by the symbol+time the BOT opened a\n"
        "  trade (until its next entry on that symbol), so a bot trade you closed\n"
        "  manually is still the bot's; fully-manual symbols are excluded. The\n"
        "  price-move % section above ignores fees/funding/slippage, so it reads\n"
        "  better than these real numbers."
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


def _since_filter(stmt, since):
    return stmt.where(Signal.created_at >= since) if since is not None else stmt


async def main(since=None) -> None:
    async with AsyncSessionLocal() as session:
        total = (
            await session.execute(_since_filter(select(func.count(Signal.id)), since))
        ).scalar_one()
        avg_conf = (
            await session.execute(_since_filter(select(func.avg(Signal.confidence)), since))
        ).scalar() or 0.0
        avg_rr = (
            await session.execute(_since_filter(select(func.avg(Signal.risk_reward)), since))
        ).scalar() or 0.0

        print("========================================")
        print(" SIGNAL PERFORMANCE SUMMARY")
        if since is not None:
            print(f" (SINCE {since.date()} UTC - earlier history hidden, not deleted)")
        print("========================================")
        print(f" Total signals ever      : {total}")
        print(f" Avg confidence          : {avg_conf:.1f}")
        print(f" Avg risk:reward         : {avg_rr:.2f}")

        _print_block("ALL SIGNALS (paper + executed)", await _counts(session, executed_only=False, since=since))
        _print_block("EXECUTED ONLY (real testnet trades)", await _counts(session, executed_only=True, since=since))

        # Per-trade list of executed, decided trades (the real outcomes).
        decided_stmt = (
            select(Signal, Coin.symbol)
            .join(Coin, Signal.coin_id == Coin.id)
            .where(
                Signal.executed.is_(True),
                Signal.status.in_(DECIDED_STATUSES),
            )
            .order_by(Signal.closed_at.asc().nullslast())
        )
        if since is not None:
            decided_stmt = decided_stmt.where(Signal.created_at >= since)
        rows = (await session.execute(decided_stmt)).all()

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
        await _print_exchange_truth(session, since=since)


def _parse_since(argv) -> "datetime | None":
    """--since YYYY-MM-DD -> timezone-aware UTC midnight, or None."""
    import argparse

    parser = argparse.ArgumentParser(description="Signal performance summary")
    parser.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        default=None,
        help="restrict every section to signals created on/after this UTC date "
        "(earlier history is hidden, never deleted)",
    )
    args = parser.parse_args(argv)
    if args.since is None:
        return None
    from datetime import datetime, timezone

    try:
        return datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        parser.error(f"--since must be YYYY-MM-DD, got {args.since!r}")


if __name__ == "__main__":
    asyncio.run(main(since=_parse_since(sys.argv[1:])))
