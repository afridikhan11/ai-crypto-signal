# Architecture Redesign Blueprint — Signals / Trading Separation
**Date:** 2026-07-31 · **Status:** PROPOSAL — awaiting approval, no code written
**Scope:** architectural evolution of the working system. Not a rewrite.

Every factual claim about the current system below is cited to a real file
and was verified from source this session. Where I disagree with the brief,
I say so explicitly and explain why (§13) rather than silently complying.

---

## 1. Current Architecture Weaknesses

First, what is *right* — because the migration must protect it: Alembic is
sole schema owner (live-verified on a fresh DB), the RiskEngine gate is
mandatory at execution, testnet/mainnet can never mix silently
(`signal_monitor.py:398-405` refuses cross-environment stop sync), client
order ids make bracket placement idempotent, audit tables exist
(`risk_assessments`, `equity_snapshots`), and 766+ tests are green. These
are institutional-grade properties; several exchanges' retail competitors
lack them. The weaknesses below are structural, not quality, problems.

### W1 — `Signal` is a god object (the root weakness; everything else follows)

`app/models/signal.py` holds four different lifecycles in one row:

| Concern | Columns |
|---|---|
| Analysis (immutable once created) | `entry_price`, `initial_stop_loss`, `take_profit`, `risk_reward`, `confidence`, `reason`, `score_breakdown`, `timeframe`, `entry_type`, `entry_zone_top/bottom`, `entry_expires_at` |
| Execution state (order tracking) | `executed`, `executed_order_id`, `executed_at`, `executed_environment`, `entry_order_id` |
| Live trade state (mutated continuously) | `stop_loss` (moved by Trade Management), `status`, `filled_at`, `actual_fill_price`, `closed_at` |
| Outcome/stats | `status` again (`TP_HIT`/`STOPPED` doubling as trade outcome) |

Consequences, all real today: one signal can never produce more than one
trade (`executed` is a boolean, `executed_order_id` a single column); a
manual trade cannot exist without a signal; the Orders a trade creates have
no home (`executed_order_id` and `entry_order_id` are two hand-labeled
slots, and protective-order ids are deliberately *not persisted at all* —
stop-sync re-discovers them by scanning open orders,
`binance_trading_service.py:633+`); and statistics need careful
status-exclusion gymnastics (`DECIDED_STATUSES` vs `TERMINAL_STATUSES` vs
`OPEN_POSITION_STATUSES`, `signal.py:33-49`) because one enum is serving
three state machines.

### W2 — Entry model is a global mutable mode, not signal data

`entry_mode` (`trading_settings.py:67+`) is one process-wide switch:
"ict_pending" or "market". The platform is wholly one model or the other.
A signal doesn't carry which policy it should be executed under — the
executor consults global state at execution time. Flip the switch with
signals in flight and history's semantics change mid-stream.

### W3 — Fill detection is REST polling; the platform is blind between polls

`SignalMonitor` polls; pending-entry fills are discovered by `get_order()`
REST reads. This is the direct root of two incidents already hit in
validation: the `-2013` write/read propagation race, and the
positionRisk-lag false "STILL OPEN". At the stated target scale — hundreds
of concurrent positions — per-poll REST reads collide with Binance's
request-weight ceiling (~2,400 weight/min), and fill latency equals poll
latency. Every serious platform consumes the **user data stream**
(`listenKey` websocket → `ORDER_TRADE_UPDATE`, `ACCOUNT_UPDATE`) and
treats REST as reconciliation backstop, not as the primary fill signal.
This is the single highest-value architectural change in this document.

### W4 — Execution rides on an HTTP request's lifetime

`POST /trading/execute/{signal_id}` (`trading.py:39+`) performs risk gate →
sizing → entry order → (market path) protective orders, inline, inside one
request. If the process restarts or the connection drops mid-sequence,
there is no durable record of intent — recovery depends on scanning
exchange state. There is no command journal; the sequence is not a
resumable saga. The validation harness's own incident (crash after entry,
before protective orders → naked position) is this weakness reproduced in
miniature.

### W5 — No Order aggregate

Orders exist only as scattered id columns and live exchange reads. Nothing
locally records an order's parameters, purpose (entry vs SL vs TP), status
history, or linkage to what it protects. `replace_stop_loss()` must
*re-discover* which resting order is "the stop" by filtering
`type == STOP_MARKET AND reduceOnly` — correct with one position per
symbol, ambiguous the day there are more.

### W6 — No Position aggregate

Position truth is re-fetched ad hoc from `/fapi/v2/positionRisk` in at
least four places (endpoint close, control-plane close-all, monitor sync,
harness). No local record of average entry, realized PnL, partial-close
history, or remaining quantity. PnL reporting is therefore only possible
via income-history archaeology.

### W7 — Entry-price policy leak (your stated concern; confirmed real)

Two code paths contradict "signals are never priced from the market":

1. `EntryEngine.build()` ends its priority chain with
   `EntryType.MARKET` at `current_price` (`entry_engine.py:134-138`), and
   `signal_generator.py:582-592` falls back to `market_entry_price`
   whenever no usable anchor exists — so market-priced signals are
   generated today whenever zones are absent.
2. Even for pending entries, SL/TP construction "still measures from
   `current_price`" (`signal_generator.py:525-528` states it verbatim) —
   ATR-multiple stops/fallback targets are anchored to where price *was at
   detection*, not to the entry the trade will actually fill at. RR is
   computed from `entry_price_used` (594-596), so the RR number is honest,
   but the *levels* are detection-anchored. This connects directly to the
   RR root-cause finding of 2026-07-30 (SL farthest / TP nearest → median
   RR 0.33).

Both are **strategy-layer changes** and are gated accordingly in the
roadmap — flagged, not silently "fixed" (strategy logic is frozen without
explicit approval).

### W8 — Single TP; partial exits impossible

TP1/2/3 was deliberately collapsed to one `take_profit` because the
monitor only ever acted on the first level reached. Restoring TP1/2/3 as
*real* behavior requires per-leg reduceOnly orders and remaining-quantity
tracking — i.e. it requires the Position Manager and Order aggregate to
exist. Columns alone would recreate the old decoration.

### W9 — One process, one worker, everything in it

Scanner, monitor, market-data websockets, and the API share one uvicorn
worker; `docker-compose.prod.yml:60-68` documents the `--workers 1`
constraint (no cross-worker coordination exists). A scanner stall degrades
the API; nothing can be scaled or restarted independently.

### W10 — Control plane is a JSON file

`data/trading_settings.json` (risk %, auto-trading gate, engine run state,
entry mode). Acceptable single-user; no audit trail of who flipped what
when, and unsafe the moment more than one process writes it.

### W11 — Exchange coupling is concrete, not port-shaped

`BinanceTradingService` / `BinanceAccountService` are used directly
everywhere. They are well-built, but there is no interface between domain
logic and Binance specifics (filters, rounding, error codes, endpoints).
A second exchange today means `if exchange == ...` forks.

### W12 — Validation covers the exchange layer, not the platform

Established in the 2026-07-31 execution-layer investigation: the harness
drives `BinanceTradingService` directly; `risk_assessments`/`executed`
correctly read 0 because the Signal→execute→persist path is never
exercised. "Recovery after restart" from the original mandate remains
unexercised entirely.

---

## 2. Recommended Architecture

**Style:** modular monolith with an internal event backbone and
hexagonal (ports & adapters) edges. Domain modules communicate through
events and owned tables — never by reaching into each other's state.

```
                    ┌────────────────────────────────────────────────┐
                    │                  WPF CLIENT                    │
                    └───────────────┬────────────────────────────────┘
                                    │ REST (v1 facades + v2 resources)
┌───────────────────────────────────┴───────────────────────────────────┐
│ API LAYER (FastAPI)  — thin translators only, no business logic       │
├───────────────────────────────────────────────────────────────────────┤
│ DOMAIN MODULES                                                        │
│                                                                       │
│  Scanner ──emits──▶ SignalEngine ──SignalCreated──▶ AutoTrader        │
│     │                    │                             │              │
│     │                    ▼                             ▼              │
│     │              signals tables                 OrderManager ◀──────┼── ManualTrading
│     │                                                  │              │
│     │                                                  ▼              │
│     │                                          PositionManager        │
│     │                                                  │              │
│     └──────────▶ PortfolioManager ◀────────────────────┘              │
│                        (risk truth: consumes everything)              │
├───────────────────────────────────────────────────────────────────────┤
│ EVENT BACKBONE — in-process bus + durable outbox (execution_log)      │
├───────────────────────────────────────────────────────────────────────┤
│ PORTS  ExchangePort (trade) · ExchangeStreamPort (user-data ws)       │
│        MarketDataPort · ClockPort                                     │
│ ADAPTERS  BinanceFuturesAdapter (wraps the two existing services)     │
├───────────────────────────────────────────────────────────────────────┤
│ PostgreSQL (Alembic-owned) · Redis (locks/cache) · Docker             │
└───────────────────────────────────────────────────────────────────────┘
```

**What I am deliberately NOT proposing** (this is the "challenge every
decision" part, applied to the brief itself): no microservices, no
Kafka/RabbitMQ, no CQRS event store. This is a single-operator desktop
product on one box. Distributed messaging buys ordering/backpressure
problems and an ops burden with zero users to justify them. The event
backbone is an in-process async dispatcher whose events are *also written
durably* to `execution_log` (transactional outbox pattern). Every handler
is idempotent and keyed by event id, so if the platform later genuinely
needs a broker, the bus implementation swaps without touching domain code.
Thousands of signals/day ≈ one every ~30-60 s at peak: a single Postgres
and one well-structured process handles that with two orders of magnitude
of headroom. Hundreds of concurrent positions is a *stream + rate-limit*
problem (solved by W3's user-data stream), not a horizontal-scaling
problem.

---

## 3. Module Boundaries

Each module: what it **owns** (only writer), what it **consumes**, what it
**emits**, and what it must **never do**. Mapping to existing code shown so
this stays an evolution.

| Module | Owns (writes) | Consumes | Emits | Never does | Evolves from |
|---|---|---|---|---|---|
| **Scanner** | nothing durable (candidate setups are transient) | market data (MarketDataPort) | `CandidateDetected` | place orders, write signals | `UniversalScanner` + data manager |
| **Signal Engine** | `signals`, `signal_targets` | `CandidateDetected`, calibration profiles | `SignalCreated`, `SignalExpired`, `SignalInvalidated` | talk to the exchange, know about orders | `signal_generator.py` + ICT engines + `EntryEngine` |
| **Auto Trader** | `trades` (creates), watch-state | `SignalCreated`, candle stream (entry-trigger detection), `OrderFilled` | `TradeRequested`, `EntryTriggered` | compute signals, place orders directly (asks OrderManager) | `SignalMonitor` pending-entry half + `execute_signal` auto path |
| **Manual Trading** | `trades` (creates, `source=MANUAL_*`) | operator commands | `TradeRequested` | mutate signals, ever | new (thin), reuses executor |
| **Order Manager** | `orders` | `TradeRequested`, user-data stream, REST reconcile sweeps | `OrderSubmitted/Filled/PartiallyFilled/Canceled/Rejected/Expired` | decide *whether* to trade (only *how*) | `BinanceTradingService` call sites + `get_order_with_retry` learnings |
| **Position Manager** | `positions`, live `trades` state | `OrderFilled`, `ACCOUNT_UPDATE`, TradeManagementEngine decisions | `PositionOpened/Changed/Closed`, `StopMoved`, `TargetHit` | place raw orders itself (asks OrderManager) | `SignalMonitor` trade-management half + `replace_stop_loss` flow |
| **Portfolio Manager** | `risk_assessments`, `equity_snapshots`, limits config | every position/order event, account snapshots | `RiskLimitBreached`, `KillSwitchActivated` | be bypassable: it is the only gate to `TradeRequested → OrderManager` | RiskEngine 2.0.0 + `execution_risk.py` + `trading_control_service` |
| **Exchange Gateway** | nothing | port calls | raw exchange events (normalized) | contain business rules | `BinanceTradingService`, `BinanceAccountService` behind `ExchangePort` |

Two hard rules that make the boundaries real rather than aspirational:
**single-writer** — exactly one module writes each table (enforceable by
test, same style as the existing schema-ownership tests); and
**risk-in-the-middle** — `TradeRequested` events pass through Portfolio
Manager's gate before Order Manager sees them, preserving today's
"mandatory RiskEngine gate" invariant in the new shape.

---

## 4. Database Redesign

Principle: **expand → migrate → contract**, Alembic-only, additive first.
`signals` is slimmed *logically* now and *physically* only in the final
contract phase. All new tables below; no DDL here, names and purpose only.

**`signals`** (analysis only — Signal Engine writes, everyone reads)
Keeps: symbol link, direction, strategy (`ICT`/`SMC`/`SMI` — new column),
entry_price, entry_type, entry_zone_top/bottom, initial_stop_loss,
targets (via `signal_targets`), risk_reward, confidence, timeframe,
reason/evidence, score_breakdown, created/expiry, status (analysis
lifecycle only, §9), outcome (derived summary, see below).
Loses (in contract phase, after backfill): `executed*`, `entry_order_id`,
`filled_at`, `actual_fill_price`, live `stop_loss` mutation (the live stop
belongs to the Trade).

**`signal_targets`** — (signal_id, rank 1..3, price, size_fraction).
TP1/2/3 as rows, not columns: TP3-optional for free, per-leg fill state
lands on orders not on the signal, and future scale-out (multiple entries)
doesn't force another migration. WPF still receives flat `tp1/tp2/tp3`
fields in DTOs — shaping is the API's job.

**`trades`** (Auto Trader / Manual Trading create; Position Manager
updates) — id, signal_id **nullable** (manual trades exist without
signals), source (`AUTO` / `MANUAL_MARKET` / `MANUAL_PENDING`),
environment (testnet/mainnet — kept per-trade, same rationale as today's
`executed_environment`), planned entry/stop/targets snapshot (frozen copy —
the signal may expire later; the trade must stand alone), quantity, status
(§9), opened_at/closed_at, realized_pnl, close_reason.
One signal → zero, one, or many trades falls out of the FK direction.

**`orders`** (Order Manager only) — id, trade_id, purpose (`ENTRY` /
`STOP_LOSS` / `TAKE_PROFIT_1..3` / `CLOSE` / `MANUAL`), exchange,
exchange_order_id, client_order_id (idempotency key, unique), type
(MARKET/LIMIT/STOP_MARKET/…), side, reduce_only, quantity, price,
stop_price, status (§9), submitted/last_update timestamps, raw request +
last raw exchange payload (JSON — the audit answer to "what did we
actually send / what did Binance actually say", which the validation
harness currently reconstructs from HTTP capture).

**`positions`** (Position Manager only; exchange-reconciled) — id, symbol,
environment, side, quantity, avg_entry_price, leverage, unrealized_pnl
(refresh), realized_pnl (accumulated), opened_at/closed_at, status. Truth
is *reconciled from the exchange* (ACCOUNT_UPDATE + positionRisk sweeps),
never derived purely from our own orders — a position changed via the
Binance UI must still be seen.

**`execution_log`** (append-only; the event journal AND the outbox) —
id, event_type, aggregate_type/id, payload JSON, correlation_id
(signal→trade→order chains share one), created_at, dispatched_at.
Doubles as the durable record W4 lacks: a restart replays undispatched
rows.

**Unchanged:** `risk_assessments`, `equity_snapshots` (gain a nullable
`trade_id`), `coins`. **Status enums:** new enums for trades/orders;
signal status value mapping in §9 (stored new, legacy names served to WPF
until its screens migrate).

---

## 5. API Redesign

Resource-oriented v2, thin; v1 kept as facades until WPF migrates
(backward-compat rule: WPF must never break mid-migration).

| Area | Endpoints (v2) | Notes |
|---|---|---|
| Signals | `GET /signals`, `GET /signals/{id}` | read-only; no execute verb lives here anymore |
| Auto Trading | `GET/PUT /auto-trading/config`, `GET /auto-trading/status`, `GET /auto-trading/watchlist` | engine on/off, per-signal watch state |
| Manual | `POST /manual/execute-market`, `POST /manual/place-signal-order`, `POST /manual/close-position/{symbol}`, `DELETE /manual/orders/{id}` | your two distinct actions, never merged; both create a `trade` with the right `source` |
| Orders | `GET /orders?trade_id=&status=` | first time orders are queryable at all |
| Positions | `GET /positions`, `GET /positions/{symbol}` | local aggregate + live reconcile timestamp |
| Portfolio | `GET /portfolio/risk`, `GET /portfolio/exposure`, `POST /portfolio/kill-switch` | absorbs today's control-plane bulk actions |
| Legacy | `POST /trading/execute/{signal_id}` → facade over Manual/Auto path; `POST /trading/close-position/{symbol}` → facade | deleted only in the contract phase |

Auth model unchanged (explicitly out of scope per your constraints).

---

## 6. Background Workers

Phase-1 shape: supervised in-process tasks (the pattern the H-1 audit fix
already established — held references, done-callbacks, cancellation).
Phase-2 shape (when justified): same code, separate processes per worker
group, enabled by the single-writer rule — no distributed locks needed
because no two processes ever write the same table.

| Worker | Trigger | Responsibility |
|---|---|---|
| MarketDataFeed | websocket | candles/liquidations (exists today; unchanged) |
| ScannerWorker | new candle | run detection, emit candidates (exists as UniversalScanner) |
| EntryWatcher | new candle | price-vs-entry-zone crossing for `WAITING_FOR_ENTRY` signals — from the **stream**, not REST |
| UserDataStreamWorker | Binance user-data ws | listenKey keepalive; normalize `ORDER_TRADE_UPDATE`/`ACCOUNT_UPDATE` into events. **The W3 fix.** |
| OrderReconciler | interval | REST sweep comparing `orders` vs exchange; heals missed stream events (`get_order_with_retry` logic institutionalized) |
| PositionWorker | events + interval | trade management (breakeven/trailing via existing TradeManagementEngine), partial-TP tracking |
| PortfolioWorker | interval + events | equity snapshots, daily-loss/drawdown watch, kill-switch enforcement |
| Archiver | daily | terminal signals/trades past retention → archived flag |

---

## 7. Event Flow

Catalog (producer → consumers). At-least-once delivery; every handler
idempotent on event id; correlation_id threads a signal's whole story.

```
CandidateDetected        Scanner        → SignalEngine
SignalCreated            SignalEngine   → AutoTrader, UI feed
SignalExpired/Invalidated SignalEngine  → AutoTrader (cancel watch), UI
EntryTriggered           EntryWatcher   → AutoTrader
TradeRequested           AutoTrader|Manual → PortfolioManager (gate!)
TradeApproved/Rejected   PortfolioManager → OrderManager | journal
OrderSubmitted           OrderManager   → journal
OrderFilled/PartFilled   UserDataStream → OrderManager → PositionManager, AutoTrader
OrderCanceled/Rejected/Expired UserDataStream|Reconciler → OrderManager → owners
PositionOpened/Changed/Closed  PositionManager → PortfolioManager, UI
StopMoved / TargetHit(n) PositionManager → OrderManager (replace/cancel legs)
TradeClosed              PositionManager → SignalEngine (outcome mirror), stats
RiskLimitBreached        PortfolioManager → AutoTrader (halt), UI
KillSwitchActivated      PortfolioManager → everything (existing semantics kept)
```

---

## 8. Execution Flow

**Order-type selection** (Auto Trader decides; Order Manager executes) —
exactly your matrix, plus the two edge rules any real desk needs:

| Direction | Entry vs current price | Order |
|---|---|---|
| LONG | below | LIMIT BUY |
| LONG | above | STOP-MARKET BUY (stop entry) |
| SHORT | above | LIMIT SELL |
| SHORT | below | STOP-MARKET SELL (stop entry) |

Edge rules: (1) entry within a small band of current price (default ~5
ticks, configurable) → policy decision `IMMEDIATE_MARKET` or `REJECT`,
never a LIMIT that would fill instantly as a disguised market order;
(2) stop entries carry `workingType=CONTRACT_PRICE` by default and a max
slippage guard on fill. **Note:** stop-entry orders are a *new exchange
capability* — today `STOP_MARKET` is used exclusively reduceOnly for
protective stops (`binance_trading_service.py`, all call sites verified);
`place_limit_entry` + market bracket are the only entry paths. Today a
LONG whose ICT entry is *above* price cannot be represented at all.

**Auto flow (pending):**
```
SignalCreated(WAITING_FOR_ENTRY)
 → AutoTrader registers watch → [optional: rest the order immediately]
 → EntryTriggered (price enters zone)      ← candle stream, not REST
 → TradeRequested → PortfolioManager gate → OrderManager places ENTRY
 → OrderFilled (user-data stream)
 → PositionManager: place SL + TP legs (reduceOnly, sized to executedQty)
 → manage (breakeven → trail; partial exits at TP1/TP2)
 → TradeClosed(outcome) → signal outcome mirrored → archived
```
**Manual Market:** `POST /manual/execute-market` → trade(source=MANUAL_MARKET)
→ gate → MARKET entry → same fill/protect/manage pipeline. Signal untouched.
**Manual Pending:** `POST /manual/place-signal-order` → trade(source=MANUAL_PENDING)
→ gate → order-type matrix picks LIMIT vs STOP → same pipeline. Signal untouched.
**Cleanup invariant** (learned the hard way in validation): protective
placement precedes any status verification; close confirmation = terminal
order state + position poll with bounded backoff; flatten path independent
of everything else.

---

## 9. State Machines

**Signal** (analysis lifecycle; your names; mapping from current enum):

```
GENERATED ─▶ WAITING_FOR_ENTRY ─▶ ENTRY_TRIGGERED ─▶ ACTIVE ─▶ TP_HIT
                    │                    │              │        or SL_HIT
                    │                    └─(no fill)────┤            │
                    ├─▶ EXPIRED (never entered)         └─▶ CANCELLED│
                    └─▶ CANCELLED (operator/invalidated)             ▼
                                                                 ARCHIVED
```
Mapping: `PENDING_ENTRY→WAITING_FOR_ENTRY`, `ACTIVE→ACTIVE`,
`TP_HIT→TP_HIT`, `STOPPED→SL_HIT`, `EXPIRED/CANCELLED` unchanged;
`ENTRY_TRIGGERED` is new (order placed/fill in progress). With multiple
trades per signal, TP_HIT/SL_HIT at signal level are the **primary trade's
outcome mirrored for display** — source of truth lives on the trade
(§13-c).

**Trade:** `REQUESTED → APPROVED → ENTRY_PENDING → OPEN → REDUCING → CLOSED(outcome: TP_HIT|SL_HIT|MANUAL|STRUCTURE|KILL_SWITCH)`
plus `REJECTED` (risk gate) and `FAILED` (entry never filled / canceled).

**Order:** `PENDING_SUBMIT → SUBMITTED → NEW → PARTIALLY_FILLED → FILLED | CANCELED | REJECTED | EXPIRED`
(exact Binance statuses; no invented states — reconciler maps 1:1).

**Position:** `OPEN → (quantity changes via fills) → CLOSED` — thin by
design; a position is exchange fact, not our intent.

Illegal transitions are rejected in code and covered by tests, same style
as the existing one-of-each invariant tests.

---

## 10. Migration Strategy

Expand-migrate-contract, with the same discipline the Alembic cleanup
proved out:

1. **Expand** — new tables via Alembic, nothing removed, dual-write off.
   Existing system runs untouched. Verified on fresh DB + `alembic check`.
2. **Backfill** — data migration derives `trades`/`orders` rows from
   executed signals' columns (`executed_order_id`, `entry_order_id`,
   `filled_at`, `actual_fill_price`) so history/stats survive the split.
3. **Dual-write** — execution paths write old columns AND new tables;
   reads still come from old columns. Divergence check runs in tests and a
   reconciler assertion.
4. **Read-switch** — endpoints (then WPF screens) move reads to the new
   aggregates, one screen at a time, feature-flagged.
5. **Contract** — only after every reader has moved and a full validation
   pass: drop legacy columns, delete facades. This is the only destructive
   step, and it is last.

Strategy-layer items (W7: MARKET fallback removal; SL/TP anchoring to
entry rather than detection price; three-target generation) are **fenced
into their own phase with their own explicit approval**, because they
change what signals get produced — that is a trading-behavior decision,
not an architecture decision, and per standing policy strategy logic does
not move without sign-off and backtest evidence.

## 11. Backward Compatibility Strategy

The invariant: **at every phase boundary, the current WPF app, the current
endpoints, and the current validation harness all still pass.**

Legacy `/trading/*` endpoints become facades over the new services with
byte-compatible response DTOs; `Signal` legacy columns stay populated by
dual-write until contract; enum renames are stored-new/served-old at the
schema layer until each WPF screen migrates; `entry_mode` maps to the
AutoTrader's default policy and is stamped per-signal at creation so
in-flight signals keep their birth semantics; every phase's feature flag
defaults OFF and the old code path physically remains until contract —
rollback is "turn the flag off", not "revert the deploy". History
continuity is guaranteed by the §10 backfill, so Statistics/History
screens never lose closed trades.

## 12. Implementation Roadmap

Each phase independently testable; none breaks production; each ends with
the full suite green + a targeted validation run. Sizes are relative
(S/M/L), not dates.

| Phase | Contents | Size | Test gate |
|---|---|---|---|
| **0** | Approve this blueprint. Characterization tests freezing current endpoint behavior (golden responses). | S | suite green |
| **1** | DB expand: `trades`, `orders`, `positions`, `signal_targets`, `execution_log` + backfill migration. No writer yet. | M | fresh-DB build, `alembic check` clean, backfill row-count reconciliation |
| **2** | Exchange Gateway: `ExchangePort` protocol wrapping the two services (no behavior change); UserDataStreamWorker + OrderReconciler in **shadow mode** — they only record into `orders`/`execution_log`, decide nothing. | M | shadow records match REST truth over a soak run on Testnet |
| **3** | Order Manager + Trade aggregate: `/trading/execute` internally creates trade+orders (dual-write with legacy columns). Harness extended to assert `trades`/`orders` rows — closing the W12 gap; §5-6 counts stop being informational. | L | dual-write divergence = 0; harness FAIL=0 incl. new DB assertions |
| **4** | Auto Trader: EntryWatcher (stream-based), order-type matrix incl. **stop entries** (new gateway capability), per-signal policy stamp. Flagged OFF by default. | L | Testnet E2E: pending→trigger→fill→SL/TP→close, both LIMIT and STOP entries |
| **5** | Manual Trading module + `/manual/*` endpoints (two distinct actions) + WPF Manual screen. | M | endpoint tests + Testnet manual runs of both actions |
| **6** | Position Manager: fills→position aggregation, PnL, breakeven/trailing moved off SignalMonitor; **TP1/2/3 partial exits** (needs §13-b decision). | L | Testnet partial-exit E2E; stop-sync regression suite |
| **7** | Portfolio Manager: continuous limit enforcement, kill-switch on breach, `/portfolio/*`. | M | limit-breach simulation tests |
| **8** | UI split to target screens: Signals / Auto Trading / Orders / Positions / Portfolio / History / Execution Logs / Risk / Scanner — mapped from the existing 13 views (LiveSignals+GoldSignals→Signals; AutoTrading stays; Orders & Positions new; Portfolio+PortfolioIntelligence merge; History+Statistics→History; TokenScanner→Scanner). One screen per PR, old screen kept until its replacement is accepted. | L | WPF build + per-screen acceptance |
| **9** | Validation suites per module (§ validation strategy): scanner golden-dataset, signal invariants, auto-trading E2E, order reconciliation soak, **recovery-after-restart** (kill mid-sequence, assert journal replay heals), DB integrity sweep. | M | all suites green, Testnet certification report |
| **10** | Strategy-policy phase (separate approval): remove MARKET fallback, anchor SL/TP to entry, generate 3 targets. Backtest before/after comparison required. | M | backtest evidence + your sign-off |
| **11** | **Contract**: drop legacy columns, delete facades, retire `entry_mode` global. | S | full suite + fresh-DB + WPF build |

## 13. Where I Push Back on the Brief

(a) **No message broker / microservices** — argued in §2. The brief says
"event driven where appropriate"; appropriate here is in-process +
durable journal.
(b) **TP1/2/3 as columns** — I recommend `signal_targets` rows instead
(§4). Same UI result, but partial fills, optional TP3 and future scaling
don't need another migration. Flat columns are a one-way door.
(c) **TP_HIT/SL_HIT as *signal* statuses** — with one-signal→many-trades
these are ambiguous at signal level (trade A stops out, trade B hits TP —
which is the signal?). Store outcome on the trade; mirror the primary
trade's outcome onto the signal for display. Your status list is fully
served; the storage location differs.
(d) **"Signals must NEVER use market price"** — architecturally correct
and adopted, but it is a strategy behavior change that will measurably
reduce signal count (every current `entry_type="market"` signal
disappears). It gets its own phase (10) with backtest evidence, not a
silent flip inside a refactor.
(e) **Hundreds of concurrent positions** — the honest bottleneck is not
architecture, it's exchange rate limits and one account's margin. The
design supports it (stream-driven, O(1) REST per event instead of O(n)
polling), but Phase 4-6 acceptance tests will certify tens of concurrent
positions on Testnet; hundreds is a load-test claim to demonstrate, not
assert.

## 14. Validation Strategy (per module)

Scanner: golden-dataset regression (fixed candles in → expected candidates
out) so ICT logic drift is caught structurally. Signals: invariant tests
(entry inside zone, stop on correct side, RR arithmetic, expiry set) + DB
single-writer tests. Auto Trading: Testnet E2E per entry type
(LIMIT below, STOP above, band-edge) through to close. Orders: soak test —
shadow reconciler vs REST truth, zero unexplained divergence over N hours.
Positions: partial-fill and partial-exit scenarios; PnL cross-checked
against income history. Database: fresh-DB build + `alembic check` +
backfill reconciliation counts (already institutional practice here).
Exchange sync: kill the process between entry fill and protective
placement — journal replay must attach protection on restart; this is the
"recovery after restart" item, finally made testable by the outbox.
Existing MARKET-execution harness keeps running unchanged throughout as
the regression floor.

---

**Next step:** your approval (or edits) on this document — in particular
decisions (b), (c), (d) in §13 and the Phase 4 stop-entry capability.
Implementation then proceeds one phase at a time, starting at Phase 0.
No code has been written.
