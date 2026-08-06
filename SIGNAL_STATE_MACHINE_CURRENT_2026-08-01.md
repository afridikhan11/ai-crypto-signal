# Signal State Machine — As Implemented Today
**Date:** 2026-08-01 · **Method:** exhaustive grep for every `Signal.status`
assignment in the backend, each read in full context. **No code changed.**

---

## 1. Every place Signal.status is written (complete — 8 sites)

The grep `\.status\s*=\s*SignalStatus\.|status=SignalStatus\.` over `app/`
returns exactly these; there are no others:

| # | File:line | Transition | Trigger |
|---|---|---|---|
| 1 | `universal_scanner.py:506` | *(birth)* → `PENDING_ENTRY` or `ACTIVE` | signal creation; which one is decided by the global `entry_mode` switch (`ict_pending` → born PENDING_ENTRY; `market` → born ACTIVE with `filled_at`/`actual_fill_price` stamped immediately) |
| 2 | `signal_monitor.py:718` | `PENDING_ENTRY` → `ACTIVE` | **armed** signals (real resting LIMIT): exchange reports order FILLED / PARTIALLY_FILLED — real `avgPrice` recorded, protection placed |
| 3 | `signal_monitor.py:777` | `PENDING_ENTRY` → `ACTIVE` | **un-armed** (advisory) signals: a 1m candle's high/low trades into the entry zone — *this is the transition your AAVE signal took at 01:10:42* |
| 4 | `signal_monitor.py:740` | `PENDING_ENTRY` → `EXPIRED` | armed: exchange reports the resting order CANCELED/EXPIRED/REJECTED |
| 5 | `signal_monitor.py:762` | `PENDING_ENTRY` → `EXPIRED` | un-armed: price reached the STOP before ever reaching the entry zone (invalidated-before-fill; checked before fill on the same candle, deliberately) |
| 6 | `signal_monitor.py:795` | `PENDING_ENTRY` → `EXPIRED` | entry window elapsed — `entry_expires_at`, set at birth to `PENDING_ENTRY_EXPIRY_CANDLES = 12` × 15m = **180 minutes** (`constants.py:35-36`); any resting order is cancelled first |
| 7 | `signal_monitor.py:867` (via `_resolve_status`, lines 152-167) | `ACTIVE` → `TP_HIT` or `STOPPED` | every 30s poll: live 1m close crossed `take_profit` or the **live** `stop_loss` |
| 8 | `signal_monitor.py:566` | `ACTIVE` → `CANCELLED` | TradeManagementEngine `CLOSE_STRUCTURE_FAILURE` (only when trade management is enabled); for executed signals also closes the real position |
| +  | `trading_control_service.py:232` | `PENDING_ENTRY`(armed) → `EXPIRED` | operator bulk action: Cancel Pending Orders / Kill Switch |

`trading.py` (Execute) **never changes status** — it only checks it
(`trading.py:76`: executable iff `ACTIVE` or `PENDING_ENTRY`) and sets
`executed*` columns.

## 2. The state machine as it actually exists

```
                                    birth (entry_mode="market")
                                    ┌──────────────────────────────┐
                                    │                              ▼
 SCANNER ──birth (ict_pending)──▶ PENDING_ENTRY ────────────▶ ACTIVE
                                    │  │  │        zone touch      │ │ │
                                    │  │  │        (advisory) or   │ │ │
                                    │  │  │        LIMIT filled    │ │ │
                                    │  │  │        (armed)         │ │ │
                    stop-before-fill┘  │  └3h window elapsed       │ │ │
                       ▼               ▼        or order dead      │ │ │
                    EXPIRED         EXPIRED ◀──────────────        │ │ │
                                                    price ≥/≤ TP ──┘ │ │
                                                       ▼             │ │
                                                    TP_HIT           │ │
                                                price ≥/≤ live SL ───┘ │
                                                       ▼               │
                                                    STOPPED            │
                                            structure failure ─────────┘
                                                       ▼
                                                   CANCELLED
```

**Timeout/expiry rules in force:** exactly one — the 180-minute window on
`PENDING_ENTRY`. **There is no timeout, age limit, price-distance rule, or
re-validation of any kind on `ACTIVE`.** Verified by targeted grep for
age/stale/timeout/expire logic near ACTIVE across the whole backend: none
exists. `entry_expires_at` is read only inside `_process_pending_entries`.

**ACTIVE has exactly three exits**, all price/structure-driven, none
time-driven: TP crossed, live stop crossed, structure failure. If none of
the three fires, ACTIVE persists indefinitely — and if
`engine_run_state != "running"`, even those three freeze
(`signal_monitor.py:821-822` returns before any resolution), while
**Execute remains available** (the endpoint checks only
`auto_trading_enabled`, not `engine_run_state`).

## 3. Answer: intentional, or architectural leak?

Both — in two different senses, and the distinction is the finding.

**ACTIVE persisting between SL and TP is intentional.** The design premise
(explicit in the code comments and the calibration/stats layers) is that
ACTIVE means *"the trade thesis is running"* — for an executed signal a
real position, for an advisory signal a simulated one whose outcome feeds
win/loss calibration (`calibration.py:73-74` counts TP_HIT as win, STOPPED
as loss). Under ICT semantics a running thesis is only ever wrong at the
stop, right at the target, or invalidated by structure — "price wandered
away from the entry" is not a thesis verdict, so no such exit exists. For
*outcome tracking*, one touch → ACTIVE forever-until-resolution is
coherent and deliberate.

**Using ACTIVE as an execution gate is the unintentional part.** ACTIVE
answers "is the thesis running?" — it does not answer "is the current
price anywhere near the planned entry?" `trading.py:76+171` treats any
ACTIVE signal as market-executable *now*, at *any* price. Nothing anywhere
compares `entry_price` to the live price at execution time (grep-verified;
the §7 matrix has no counterpart in current code). So the architecture
does not so much *allow* stale-ACTIVE execution as it *fails to define*
execution-time price validity at all — that check simply has no home in
the current design, which is precisely what G1/G2 (two explicit manual
actions + the order-type matrix) introduce. Note the spec's own answer:
signal state and trade state are separated exactly so "thesis running"
and "executable at entry" can never be conflated again.

**One more honest observation from your incident:** with the monitor
*running*, your AAVE SHORT should not have still been ACTIVE at 12:00 —
price ~97-99 was through the ~98.5 take-profit, and site 7 would have
closed it TP_HIT within 30s. Its survival to 12:00/14:01 implies the
resolution loop was not effective during that window — engine paused or
stopped from the control panel (which freezes resolution but not Execute),
the app being down (we rebuilt containers repeatedly on 07-31), or an
empty candle frame after a restart. The DB query you're running will help
settle which: `closed_at`/`status` on the two rows the log shows flipping
ACTIVE (01:10:42 and 01:11:16 — two separate rows exist at entry
100.445). This does not change the architectural verdict above; it adds
that stale ACTIVE is *guaranteed* under a paused engine, because pausing
freezes the only three exits while leaving Execute armed.

## 4. Consequence for the approved work

No scope change. G1 (two explicit actions) and G2 (matrix comparing entry
vs live price at execution time) are exactly the missing "execution-time
validity" layer identified above; G4 (EntryWatcher) later replaces the
advisory zone-touch flip as the executed path's fill authority; the locked
spec's Signal/Trade split removes the ACTIVE double-meaning permanently.
Implementation of G1/G2/G3/G6 can proceed on your word, unchanged.
