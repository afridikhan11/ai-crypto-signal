"""
What the bot thinks is open, versus what the exchange actually holds.

WHY THIS EXISTS (2026-09-30)
---------------------------------------------------------------------------
A screenshot of the venue showed two positions and three open orders while
the bot's own summary reported one ACTIVE signal and nothing pending. Both
positions were DUST - 0.11 and 0.03 USDT of notional - with a break-even
price of 403.18 on a $1.06 coin and an ROI of -189%. Those absurd numbers
are the venue dividing by a near-zero size, not money at risk; but a dust
position is still a POSITION, and `max_open_positions` counts positions. Left
alone, two specks of dust can quietly consume the bot's allowance and stop it
trading, and nothing in the system would say so.

`signal_stats.py` reports what the DATABASE believes. This reports the gap
between that and the venue, which is the only place a leak of this kind is
visible. Read-only: it places, cancels and closes nothing.

    docker compose exec -T app python scripts/reconcile.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.coin import Coin
from app.models.signal import Signal, SignalStatus
from app.services import binance_credentials
from app.services.binance_account_service import BinanceAccountService

# Below this notional a position is dust: a leftover speck from a partial
# close, too small to mean anything as a trade but still counted as a
# position by the exchange and by our own open-positions cap.
DUST_NOTIONAL_USDT = 1.0


async def main() -> None:
    creds = binance_credentials.load_credentials()
    if not creds:
        print("No saved credentials - nothing to reconcile.")
        return
    env = "testnet/demo" if creds.get("testnet") else "MAINNET"
    print(f"=== RECONCILE ({env}) ===\n")

    # --- what the bot believes -------------------------------------------
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(Signal, Coin.symbol)
            .join(Coin, Signal.coin_id == Coin.id)
            .where(Signal.executed.is_(True),
                   Signal.status.in_([SignalStatus.ACTIVE, SignalStatus.PENDING_ENTRY]))
        )).all()
    believed = {sym.upper(): sig.status.value for sig, sym in rows}

    print("--- the bot believes it holds ---")
    if not believed:
        print("  (nothing)")
    for sym, status in sorted(believed.items()):
        print(f"  {sym:<12} {status}")

    # --- what the venue actually holds -----------------------------------
    service = BinanceAccountService(
        api_key=creds["api_key"], api_secret=creds["api_secret"],
        testnet=bool(creds.get("testnet", False)),
    )
    try:
        futures = await service.get_futures_account()
    finally:
        await service.close()

    if futures is None:
        print("\n  Could not read the exchange - stopping rather than guessing.")
        return

    live = [p for p in futures.open_positions if p.position_amt != 0]
    print("\n--- the exchange actually holds ---")
    if not live:
        print("  (no open positions)")
    dust = []
    unrealised = 0.0
    for p in sorted(live, key=lambda x: x.symbol):
        notional = abs(p.position_amt) * (p.mark_price or p.entry_price or 0.0)
        tag = ""
        if notional < DUST_NOTIONAL_USDT:
            tag = "  <-- DUST"
            dust.append((p.symbol, p.position_amt, notional))
        # Where each open position stands RIGHT NOW. signal_stats only ever
        # reports trades that have already closed, so without this the
        # commonest question of all - "is anything bleeding at this moment?"
        # - has no answer anywhere in the system.
        unrealised += p.unrealized_pnl
        print(f"  {p.symbol:<12} amt {p.position_amt:<14} notional ${notional:>10,.2f}"
              f"   unrealised {p.unrealized_pnl:+8.2f} USDT{tag}")
    if live:
        print(f"  {'':<12} {'':<18} {'TOTAL OPEN':>11}   "
              f"           {unrealised:+8.2f} USDT")

    # --- the gap, which is the whole point -------------------------------
    venue = {p.symbol.upper() for p in live}
    print("\n--- the gap ---")
    orphan = sorted(venue - set(believed))
    missing = sorted(sym for sym, st in believed.items()
                     if st == SignalStatus.ACTIVE.value and sym not in venue)
    if not orphan and not missing and not dust:
        print("  none - the bot's view and the exchange agree.")
    for sym in orphan:
        print(f"  ORPHAN   {sym}: open on the exchange, the bot has no live signal for it.")
    for sym in missing:
        print(f"  PHANTOM  {sym}: the bot thinks it is ACTIVE, the exchange has no position.")
    if dust:
        print(f"\n  {len(dust)} dust position(s) under ${DUST_NOTIONAL_USDT:.2f}. Each still")
        print("  counts against MAX_OPEN_POSITIONS, so they can silently use up the")
        print("  bot's allowance. Close them by hand on the venue.")


asyncio.run(main())
