# FINAL ARCHITECTURE SPECIFICATION
## AI Crypto Signal Pro — Institutional Trading Platform

| | |
|---|---|
| **Version** | 1.0 |
| **Date** | 2026-07-31 |
| **Status** | MASTER DESIGN DOCUMENT — supersedes ARCHITECTURE_REDESIGN_BLUEPRINT_2026-07-31.md, which it incorporates |
| **Rule** | No code in this document. Implementation follows the roadmap in §15, one phase at a time, each phase separately approved. |

This specification is written so that independent developers implementing
different modules against it produce one coherent system. Where a choice
was arguable, the decision AND its rationale are recorded, so implementers
do not re-litigate it. Where the requirements as given contained a weak
idea, §16 records the challenge and the adopted resolution — nothing was
silently changed.

---

# 1. System Overview

## 1.1 What the system is

An AI-assisted trading platform for Binance USD-M Futures (multi-exchange
capable by design), with a FastAPI/PostgreSQL/Redis backend and a WPF
desktop client. Strategy analysis (ICT/SMC/SMI) produces **Signals**;
**Trades** execute them; **Orders** implement trades on an exchange;
**Positions** are exchange facts. These are four different things with
four different lifecycles, four owners, and four tables.

## 1.2 The five non-negotiable invariants

Implementers must preserve these in every phase; each is enforced by test:

1. **Single-writer** — exactly one module writes each table (§9).
2. **Risk-in-the-middle** — no order reaches an exchange except through
   the Portfolio Manager's gate. There is no second path.
3. **Environment integrity** — testnet and mainnet state never mix; every
   trade, order and position carries its environment; cross-environment
   operations are refused, never guessed.
4. **Durable intent** — every execution decision is journaled
   (`execution_log`) *before* its side effect, so a crash at any point is
   recoverable by replay. No execution sequence lives only inside an HTTP
   request or a task's stack frame.
5. **No fabricated data** — a missing input (news score, absent zone,
   unreachable exchange field) is represented as absent and explained,
   never defaulted into a number that feeds a decision.

## 1.3 System Architecture Diagram

```
                            ┌──────────────────────────────────────────┐
                            │               WPF DESKTOP                │
                            │ Signals · AutoTrading · Orders ·         │
                            │ Positions · Portfolio · History ·        │
                            │ ExecutionLogs · Risk · Scanner · Settings│
                            └─────────────────────┬────────────────────┘
                                                  │ REST /api/v2 (+v1 facades)
┌─────────────────────────────────────────────────┴─────────────────────────────┐
│  API LAYER (FastAPI) — translation only: DTO shaping, auth, no business logic │
├───────────────────────────────────────────────────────────────────────────────┤
│  DOMAIN LAYER                                                                 │
│                                                                               │
│   ┌─────────┐   candidates   ┌────────────────────┐  scored decision          │
│   │ SCANNER ├───────────────▶│ AI DECISION ENGINE ├──────────────┐            │
│   └────┬────┘                │  (MTF + evidence + │              ▼            │
│        │ market data         │   strategy verdicts│      ┌───────────────┐    │
│        │                     │   + explainability)│      │ SIGNAL ENGINE │    │
│        │                     └──────────▲─────────┘      │ (versioned    │    │
│        │                                │                │  signals)     │    │
│        │                     ┌──────────┴─────────┐      └───────┬───────┘    │
│        │                     │ STRATEGY PLUGINS   │              │SignalCreated
│        │                     │ ICT │ SMC │ SMI │…│              ▼            │
│        │                     └────────────────────┘      ┌───────────────┐    │
│        │                                                 │  AUTO TRADER  │    │
│        │                                                 └───────┬───────┘    │
│        │                              ┌───────────────┐          │TradeRequested
│        │                              │MANUAL TRADING ├──────────┤            │
│        │                              └───────────────┘          ▼            │
│        │                                            ┌─────────────────────┐   │
│        │                                            │  PORTFOLIO MANAGER  │   │
│        │                                            │  (risk gate — ALL   │   │
│        │                                            │   trades pass here) │   │
│        │                                            └──────────┬──────────┘   │
│        │                                                       ▼              │
│        │              ┌────────────────┐  fills   ┌─────────────────────┐     │
│        │              │POSITION MANAGER│◀─────────│    ORDER MANAGER    │     │
│        │              └───────┬────────┘          └──────────┬──────────┘     │
│        │                      │                              │                │
│        └──────────────────────┼──────────────────────────────┼────────────────┤
│                               ▼                              ▼                │
│               ┌──────────────────────────┐   ┌──────────────────────────┐     │
│               │      AI FEEDBACK LOOP    │   │  EVENT BACKBONE          │     │
│               │ (closed trades → weights,│   │  in-process bus +        │     │
│               │  governed, §8)           │   │  execution_log outbox    │     │
│               └──────────────────────────┘   └──────────────────────────┘     │
├───────────────────────────────────────────────────────────────────────────────┤
│  PORTS: ExchangePort · ExchangeStreamPort · MarketDataPort · ClockPort        │
│  ADAPTERS: BinanceFuturesAdapter (today) · <future exchange adapters>         │
├───────────────────────────────────────────────────────────────────────────────┤
│  PostgreSQL (Alembic sole schema owner) · Redis · Docker                      │
└───────────────────────────────────────────────────────────────────────────────┘
```

## 1.4 Module Dependency Diagram

Dependencies point downward only. A module may depend on modules below it,
the event backbone, and ports — never on a module above it, and never on a
sibling's internals.

```
            WPF / API layer
                  │
  ┌───────┬───────┼─────────┬──────────┐
  ▼       ▼       ▼         ▼          ▼
Scanner  Signal  Auto     Manual   Portfolio
  │      Engine  Trader   Trading  Manager
  │        │       │         │         │
  │        ▼       └────┬────┘         │
  │  AI Decision        ▼              │
  │  Engine        Order Manager ◀─────┘ (gate approval precedes)
  │        │            │
  │        ▼            ▼
  │  Strategy      Position Manager
  │  Plugins            │
  └────────┬────────────┘
           ▼
   Event Backbone (bus + outbox)
           │
           ▼
   Ports & Adapters (Exchange, Streams, MarketData, Clock)
           │
           ▼
   PostgreSQL · Redis
```

---

# 2. AI Decision Engine (new requirement 1)

## 2.1 Position in the flow

```
Scanner → AI DECISION ENGINE → Signal Engine → Auto Trading
```

The Scanner detects raw candidates (structure events, zone touches). The
AI Decision Engine is the single place where a candidate becomes a
**decision**: LONG / SHORT / NO_TRADE, with confidence and a complete
explanation. The Signal Engine then *constructs* the signal (entry plan,
stop, targets, expiry) from an approved decision. Auto Trading never sees
anything the Decision Engine rejected.

This is an evolution, not an invention: the codebase already contains
`AIScorer` (13 ICT evidence categories), a Decision Engine
(why LONG/SHORT/NO TRADE + missing evidence, built 2026-07-30), an
Evidence Engine, and per-asset calibration profiles. This specification
formalizes them into one module with one contract, and extends the input
set.

## 2.2 Inputs (the fusion set)

| Input | Source | Present today? |
|---|---|---|
| ICT structure (BOS/CHoCH, MSS) | ICT engines via strategy plugin | yes |
| SMC constructs (Order Blocks, FVGs, mitigation) | engines | yes |
| SMI confirmation | strategy plugin | yes (as scoring input) |
| Liquidity (sweeps, inducement, resting pools) | LiquidityEngine | yes |
| Sessions / kill zones | SessionEngine | yes |
| Volume confirmation | order-flow module | yes |
| Volatility regime (ATR-relative) | existing | yes |
| Higher-timeframe trend | HTF structure snapshot | yes |
| **News score** | NewsPort (new port, §16-a) | **no — port designed now, adapter future** |
| AI confidence | AIScorer weighted fusion | yes |

## 2.3 Output contract

One `Decision` object per evaluated candidate:
direction (`LONG`/`SHORT`/`NO_TRADE`), confidence 0-100, strategy_id,
ai_model_version (weight-set version, §8), the **MTF verdict chain**
(§3), and the **evidence ledger** (§4) — every factor with its score,
weight, and contribution. NO_TRADE decisions carry the same ledger plus
`missing_evidence` (what would have changed the answer) — this exists
today and must be preserved; it is the most operationally useful output
the current system produces.

## 2.4 Rules

Absent inputs contribute nothing and appear in the ledger as
`NOT_AVAILABLE` with a reason (invariant 5). The engine is stateless and
deterministic for a given (inputs, weight-set version) pair — required
for backtest/live parity and for the feedback loop's shadow evaluation.

---

# 3. Multi-Timeframe Confirmation (new requirement 3)

## 3.1 The chain

```
Weekly ─▶ Daily ─▶ 4H ─▶ 1H ─▶ 15M ─▶ 5M ─▶ Entry
  bias     bias    struct struct  setup  timing
```

Each level answers one question and passes its verdict down: W/D establish
institutional bias; 4H/1H establish structure alignment and the zone of
interest; 15M provides the setup (BOS + zone entry); 5M provides entry
timing refinement. Every level's verdict is recorded in the evidence
ledger with its timeframe tag — the MTF chain IS part of explainability,
not separate from it.

## 3.2 Architectural rules

The timeframe stack is **declared by the strategy plugin**, not hardcoded:
ICT crypto may use W→5M; a commodity profile may stop at 1H; a future EMA
plugin may use three timeframes. The AI Decision Engine executes whatever
chain the plugin declares and requires a verdict (or an explicit
NOT_AVAILABLE with reason — e.g. insufficient weekly history for a young
listing) at every declared level. Market data provisioning
(`MarketDataPort`) must therefore serve arbitrary timeframe sets per
symbol, which generalizes today's fixed 15m/1h/4h/1d/weekly frames.
Conflict policy is plugin-declared: `veto` (HTF conflict kills the
candidate — ICT default) or `weight` (conflict reduces confidence).

---

# 4. Explainable AI (new requirement 4)

Every signal must answer "why?" — machine-readably, not as prose only.

## 4.1 The evidence ledger

Stored as rows in `signal_evidence` (§10), one row per contributing
factor:

```
factor            timeframe  detail                              score  weight  contribution
----------------  ---------  ----------------------------------  -----  ------  ------------
order_block       1D         Bullish OB 42,150-42,600, unmitigated  88    0.15        13.2
liquidity_sweep   4H         Sellside sweep of equal lows 41,900    91    0.15        13.7
structure_break   15M        BOS above 42,720 with displacement     85    0.20        17.0
smi_confirmation  15M        SMI cross + divergence agree           72    0.10         7.2
session           —          NY killzone active                     80    0.10         8.0
volume            15M        Volume spike 2.4x 20-bar average       76    0.10         7.6
htf_trend         1W         Weekly structure bullish               83    0.15        12.5
news              —          NOT_AVAILABLE (no news adapter)         —      —            —
                                                        confidence = Σ contributions = 79
```

`confidence` on the signal MUST equal the sum of ledger contributions
under the weight set named by `ai_model_version` — an arithmetic identity
enforced by test. This makes every score auditable and makes the feedback
loop's re-weighting meaningful (§8). The current `score_breakdown` JSON
column is the compatibility ancestor of this table and is dual-written
until contract.

## 4.2 Human rendering

The WPF Signals screen renders the ledger directly (the example in the
requirement — "Daily Bullish OB, 4H Liquidity Sweep, 15M BOS, SMI
Confirmation, NY Session, Volume Spike" — is literally rows of this
table). No separate prose-generation path: one source of truth, one
renderer.

---

# 5. Strategy Plugin Architecture (new requirement 5)

## 5.1 The contract

A strategy is a plugin implementing one protocol (conceptually — no code
here):

```
StrategyPlugin
  id            "ict" | "smc" | "smi" | "ema" | "vwap" | <custom>
  display_name
  timeframes    ordered list this strategy requires (drives §3)
  conflict_policy   veto | weight
  evaluate(market_context) → StrategyVerdict | None
  
StrategyVerdict
  direction, evidence[]           (ledger rows, §4)
  entry_plan { anchor zone | explicit price,
               preferred_entry_order_type: LIMIT | STOP | MARKET }   (§7)
  stop_proposal, target_proposals[1..3]
```

## 5.2 Rules

The Signal Engine, Auto Trader, Order Manager and Position Manager consume
`StrategyVerdict` and signal rows **only** — adding a strategy touches
zero engine code (the requirement's acceptance test). Plugins are
discovered from a registry; each is independently enable/disable-able at
runtime (`strategy_registry` config + API §12). Every signal carries its
`strategy_id`; calibration weights (§8) are **per strategy** — an EMA
plugin's outcomes never contaminate ICT's weight learning. The existing
ICT pipeline becomes the first-party `ict` plugin by extraction, not
rewrite; the quarantined `app/legacy` retail indicators may later be
resurrected as `ema`/`vwap` plugins without touching the core — which is
exactly the argument for this architecture (see §16-b for the caution).

---

# 6. Signal Versioning (new requirement 2)

## 6.1 Model

Signals are **immutable**. A revision creates a new row:

```
lineage_id  (stable identity: "the BTC long idea")
version     1, 2, 3 …
is_latest   exactly one true per lineage (partial unique index)
superseded_by → next version's id
revision_reason  e.g. "4H zone refined after new sweep"
```

```
BTC lineage 7f3a…
  V1  WAITING_FOR_ENTRY  entry 42,300   ──superseded──▶
  V2  WAITING_FOR_ENTRY  entry 42,150   ──superseded──▶
  V3  ENTRY_TRIGGERED    entry 42,150   (is_latest ✔)
```

## 6.2 Rules (the part that prevents subtle bugs)

**What creates a version:** only the Signal Engine, on re-evaluation
(structure changed, zone refined, targets adjusted, confidence
re-scored). **What does NOT:** trade management. A moving stop is *trade*
state (`trades.live_stop`), not signal state — the signal's
`initial_stop_loss` never changes and the old bug class ("live stop
overwrote the sizing reference", solved 2026-07-30 by `initial_stop_loss`)
is structurally impossible here.
**Auto Trader** watches `is_latest` only; a supersede event cancels the
watch/resting order of the old version and re-evaluates the new one.
**Trades** reference the exact `signals.id` (version) they executed — an
open trade is never silently re-pointed at a newer version.
**Status** lives per-version; superseded versions get status `SUPERSEDED`
(terminal). History remains queryable by lineage (`GET
/v2/signals/lineage/{id}`).

---

# 7. Entry Types (new requirement 7)

The architecture supports LIMIT, STOP and MARKET entries. **The strategy
decides; the execution engine follows.** Precedence, resolving the
apparent conflict with the blueprint's auto-selection matrix:

1. If the plugin's `entry_plan.preferred_entry_order_type` is explicit —
   use it (validated for feasibility, e.g. a LIMIT above market for a
   LONG is rejected back to the plugin as infeasible, never "fixed"
   silently into a market order).
2. If the plugin expresses only a price/zone (the common ICT case), the
   Auto Trader resolves the mechanics with the matrix:

```
LONG  entry below current price → LIMIT BUY
LONG  entry above current price → STOP-MARKET BUY
SHORT entry above current price → LIMIT SELL
SHORT entry below current price → STOP-MARKET SELL
entry within band (default 5 ticks, configurable) → policy: REJECT | IMMEDIATE_MARKET
```

3. Manual Trading's two actions are explicit overrides: Execute Market
   forces MARKET; Place Signal Order runs rule 2 on the signal's entry.

Stop entries carry `workingType` (default CONTRACT_PRICE) and a max
slippage guard. Stop-entry capability is NEW — today `STOP_MARKET` exists
only reduceOnly for protective stops (verified from source 2026-07-31);
it arrives in Phase A4 (§15).

---

# 8. AI Feedback Loop (new requirement 6)

## 8.1 Flow

```
Trade closed ─▶ Outcome recorded ─▶ Evaluation ─▶ Weight proposal ─▶ SHADOW ─▶ ACTIVE
                (trades.outcome +     (per-strategy,  (ai_weight_sets,   (scored in     (used by
                 evidence ledger       per-factor      status=PROPOSED)   parallel,      Decision
                 of its signal)        hit-rates)                         not acted on)  Engine)
```

Closed trades join to their signal's evidence ledger: for every factor,
"when this factor scored high, did the trade win?" Per-strategy weight
sets are re-derived (this generalizes the existing
`app/ai/calibration.py`, which already re-derives the 13 ICT category
weights from closed outcomes).

## 8.2 Governance — the CTO rule

**No self-modifying weights on a live system.** A proposed weight set
must: (1) be produced from a minimum sample of closed trades
(configurable, default 50 per strategy); (2) run in SHADOW — the Decision
Engine scores every candidate under both ACTIVE and SHADOW sets, journals
both, acts only on ACTIVE; (3) demonstrate improvement over the shadow
window plus a backtest report; (4) be **manually activated** via API
(§12). Every signal already stamps `ai_model_version`; that becomes the
weight-set version, so any historical signal's score is reproducible.
Feedback data is environment-segregated: Testnet outcomes never train
Mainnet weights (Testnet fills against thin books are not evidence about
real execution).

---

# 9. Module Boundaries (consolidated, with ownership)

| Module | Writes (only writer) | Emits | Never does |
|---|---|---|---|
| Scanner | — (transient candidates) | CandidateDetected | write DB, place orders |
| AI Decision Engine | — (stateless; decisions journaled by caller) | DecisionMade / DecisionRejected | touch exchange, persist signals |
| Signal Engine | signals, signal_targets, signal_evidence | SignalCreated, SignalRevised, SignalExpired, SignalInvalidated | know orders/exchange exist |
| Auto Trader | trades (create, source=AUTO), watch state | EntryTriggered, TradeRequested | place orders directly, create signals |
| Manual Trading | trades (create, source=MANUAL_*) | TradeRequested | mutate signals |
| Portfolio Manager | risk_assessments, equity_snapshots, limits | TradeApproved/Rejected, RiskLimitBreached, KillSwitchActivated | be bypassable |
| Order Manager | orders | OrderSubmitted/Filled/PartiallyFilled/Canceled/Rejected/Expired | decide whether to trade |
| Position Manager | positions; trades (state after open) | PositionOpened/Changed/Closed, StopMoved, TargetHit, TradeClosed | place raw orders (asks Order Manager) |
| Feedback Loop | ai_weight_sets | WeightSetProposed | activate itself |
| Exchange Gateway | — | normalized exchange events | contain business rules |

---

# 10. Database Design (complete)

All schema is Alembic-owned; every change below arrives as a migration in
its roadmap phase; expand → dual-write → read-switch → contract.
Types are indicative (PostgreSQL).

## 10.1 ER Diagram

```
 coins 1───∞ signals ∞───1 signals (superseded_by, self-ref)
                │ 1
                ├───∞ signal_targets
                ├───∞ signal_evidence
                │ 1
                └───∞ trades ∞───1 risk_assessments (assessment per request)
                        │ 1
                        ├───∞ orders
                        │
 positions (exchange-reconciled; linked to trades by symbol+environment+time,
            deliberately NOT FK-coupled — a position is exchange fact and can
            exist with no trade of ours, e.g. manual UI trading)
                        
 execution_log (append-only; references any aggregate by (aggregate_type, aggregate_id))
 equity_snapshots (time series, standalone)
 ai_weight_sets (per strategy_id, versioned)
```

## 10.2 Tables

**signals** — writer: Signal Engine
| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK |
| lineage_id | UUID | NOT NULL |
| version | INT | NOT NULL, default 1 |
| is_latest | BOOL | NOT NULL |
| superseded_by | UUID | NULL, FK→signals.id |
| revision_reason | VARCHAR(200) | NULL |
| coin_id | UUID | NOT NULL, FK→coins.id |
| strategy_id | VARCHAR(20) | NOT NULL |
| direction | direction enum | NOT NULL |
| entry_price | FLOAT | NOT NULL |
| entry_anchor_type | VARCHAR(20) | NULL (ote/order_block/fvg/supply_demand/…) |
| entry_zone_top / entry_zone_bottom | FLOAT | NULL |
| preferred_entry_order_type | entry_order_type enum | NULL (NULL = resolve by matrix) |
| initial_stop_loss | FLOAT | NOT NULL, immutable |
| risk_reward | FLOAT | NOT NULL |
| confidence | INT | NOT NULL (= Σ evidence contributions, tested) |
| ai_model_version | VARCHAR(20) | NOT NULL (weight-set version) |
| timeframe | VARCHAR(10) | NOT NULL |
| status | signal_status enum | NOT NULL |
| outcome | trade_outcome enum | NULL (mirror of primary trade) |
| generated_at, expires_at | TIMESTAMPTZ | NOT NULL / NULL |
| archived | BOOL | NOT NULL default false |
| created_at, updated_at | TIMESTAMPTZ | NOT NULL, server_default now() |

Indexes/constraints: UNIQUE(lineage_id, version) · partial UNIQUE
(lineage_id) WHERE is_latest · INDEX(status) WHERE is_latest ·
INDEX(coin_id, generated_at DESC) · INDEX(strategy_id).
Legacy columns (`executed*`, `entry_order_id`, `filled_at`,
`actual_fill_price`, live `stop_loss`, `take_profit`, `score_breakdown`)
remain and are dual-written until the contract phase, then dropped.

**signal_targets** — writer: Signal Engine
id UUID PK · signal_id FK→signals.id NOT NULL · rank INT NOT NULL (1..3) ·
price FLOAT NOT NULL · size_fraction FLOAT NOT NULL (Σ per signal = 1.0,
tested) · UNIQUE(signal_id, rank).

**signal_evidence** — writer: Signal Engine
id UUID PK · signal_id FK NOT NULL · factor VARCHAR(30) NOT NULL ·
timeframe VARCHAR(10) NULL · detail VARCHAR(300) · score FLOAT NULL ·
weight FLOAT NULL · contribution FLOAT NULL · available BOOL NOT NULL
(false ⇒ NOT_AVAILABLE row, score/weight/contribution NULL) ·
INDEX(signal_id) · INDEX(factor).

**trades** — writer: Auto Trader / Manual Trading (create), Position Manager (state)
| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK |
| signal_id | UUID | NULL, FK→signals.id (exact version) |
| source | trade_source enum | NOT NULL |
| environment | environment enum | NOT NULL |
| symbol | VARCHAR(20) | NOT NULL |
| direction | direction enum | NOT NULL |
| planned_entry, planned_stop | FLOAT | NOT NULL (frozen snapshot) |
| planned_targets | JSONB | NOT NULL (frozen copy of targets) |
| quantity | FLOAT | NOT NULL |
| filled_entry_price | FLOAT | NULL |
| live_stop | FLOAT | NULL (the moving stop lives HERE, not on signal) |
| remaining_quantity | FLOAT | NOT NULL (partial exits decrement) |
| status | trade_status enum | NOT NULL |
| outcome | trade_outcome enum | NULL |
| close_reason | VARCHAR(200) | NULL |
| risk_assessment_id | UUID | NULL, FK→risk_assessments.id |
| correlation_id | UUID | NOT NULL |
| requested_at, opened_at, closed_at | TIMESTAMPTZ | NOT NULL/NULL/NULL |
| realized_pnl | FLOAT | NULL |
| created_at, updated_at | TIMESTAMPTZ | NOT NULL |

Indexes: INDEX(signal_id) · INDEX(status) · INDEX(environment, status) ·
INDEX(correlation_id) · INDEX(symbol, closed_at DESC).

**orders** — writer: Order Manager
| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK |
| trade_id | UUID | NOT NULL, FK→trades.id |
| purpose | order_purpose enum | NOT NULL |
| exchange | VARCHAR(20) | NOT NULL default 'binance_futures' |
| client_order_id | VARCHAR(40) | NOT NULL, UNIQUE (idempotency key) |
| exchange_order_id | BIGINT | NULL |
| type | order_type enum | NOT NULL |
| side | VARCHAR(4) | NOT NULL |
| reduce_only | BOOL | NOT NULL |
| quantity, price, stop_price | FLOAT | NOT NULL / NULL / NULL |
| working_type | VARCHAR(20) | NULL |
| status | order_status enum | NOT NULL |
| executed_qty | FLOAT | NOT NULL default 0 |
| avg_fill_price | FLOAT | NULL |
| raw_request, raw_last_response | JSONB | NOT NULL / NULL |
| submitted_at, last_update_at | TIMESTAMPTZ | NULL / NOT NULL |
| created_at, updated_at | TIMESTAMPTZ | NOT NULL |

Indexes: partial UNIQUE(exchange, exchange_order_id) WHERE
exchange_order_id IS NOT NULL · INDEX(trade_id) · INDEX(status) WHERE
status NOT IN (terminal).

**positions** — writer: Position Manager (exchange-reconciled)
id UUID PK · exchange VARCHAR NOT NULL · environment enum NOT NULL ·
symbol VARCHAR NOT NULL · side VARCHAR NOT NULL · quantity FLOAT NOT NULL ·
avg_entry_price FLOAT NOT NULL · leverage INT NULL · unrealized_pnl FLOAT
NULL · realized_pnl FLOAT NOT NULL default 0 · status position_status enum
NOT NULL · opened_at TIMESTAMPTZ NOT NULL · closed_at NULL ·
last_reconciled_at TIMESTAMPTZ NOT NULL ·
partial UNIQUE(exchange, environment, symbol) WHERE status='OPEN'.

**execution_log** — writer: event backbone (every module appends via it)
id BIGSERIAL PK · event_id UUID NOT NULL UNIQUE · event_type VARCHAR(40)
NOT NULL · aggregate_type VARCHAR(20) NOT NULL · aggregate_id UUID NOT
NULL · correlation_id UUID NOT NULL · payload JSONB NOT NULL · created_at
TIMESTAMPTZ NOT NULL server_default now() · dispatched_at TIMESTAMPTZ NULL ·
INDEX(aggregate_type, aggregate_id) · INDEX(correlation_id) · partial
INDEX(created_at) WHERE dispatched_at IS NULL (outbox scan).

**ai_weight_sets** — writer: Feedback Loop (create), API (activate)
id UUID PK · strategy_id VARCHAR NOT NULL · version VARCHAR(20) NOT NULL ·
weights JSONB NOT NULL · status weight_set_status enum NOT NULL ·
derived_from_trades INT NOT NULL · shadow_report JSONB NULL ·
backtest_report_ref VARCHAR NULL · created_at, activated_at ·
UNIQUE(strategy_id, version) · partial UNIQUE(strategy_id) WHERE
status='ACTIVE'.

**risk_assessments / equity_snapshots / coins** — as today;
risk_assessments gains nullable trade_id FK.

## 10.3 Status Enums

```
signal_status:    GENERATED · WAITING_FOR_ENTRY · ENTRY_TRIGGERED · ACTIVE
                  · TP_HIT · SL_HIT · EXPIRED · CANCELLED · SUPERSEDED
                  (mapping from current enum: PENDING_ENTRY→WAITING_FOR_ENTRY,
                   STOPPED→SL_HIT; stored new, served old to v1/WPF until migrated)
trade_status:     REQUESTED · APPROVED · REJECTED · ENTRY_PENDING · OPEN
                  · REDUCING · CLOSED · FAILED
trade_outcome:    TP_HIT · SL_HIT · MANUAL_CLOSE · STRUCTURE_CLOSE
                  · KILL_SWITCH · EXPIRED_UNFILLED
trade_source:     AUTO · MANUAL_MARKET · MANUAL_PENDING
order_purpose:    ENTRY · STOP_LOSS · TAKE_PROFIT_1 · TAKE_PROFIT_2
                  · TAKE_PROFIT_3 · CLOSE · MANUAL
order_type:       MARKET · LIMIT · STOP_MARKET · STOP · TAKE_PROFIT_MARKET
order_status:     PENDING_SUBMIT · SUBMITTED · NEW · PARTIALLY_FILLED
                  · FILLED · CANCELED · REJECTED · EXPIRED   (Binance names, 1:1)
position_status:  OPEN · CLOSED
environment:      TESTNET · MAINNET
weight_set_status: PROPOSED · SHADOW · ACTIVE · RETIRED
```

---

# 11. Lifecycle State Machines

## 11.1 Signal Lifecycle
```
                         ┌────────────────────────────────────────────┐
 GENERATED ──▶ WAITING_FOR_ENTRY ──▶ ENTRY_TRIGGERED ──▶ ACTIVE ──▶ TP_HIT
      │              │        │             │(no fill)      │    └─▶ SL_HIT
      │              │        └─▶ SUPERSEDED│               └──────▶ CANCELLED
      │              ├─▶ EXPIRED            └─▶ EXPIRED_… (via trade FAILED)
      │              └─▶ CANCELLED
      └─▶ SUPERSEDED (re-evaluated before ever waiting)
 All terminal states ──(retention)──▶ archived=true
```
TP_HIT/SL_HIT at signal level are the primary trade's outcome mirrored for
display; source of truth is `trades.outcome` (approved blueprint §13-c).

## 11.2 Trade Lifecycle
```
 REQUESTED ──▶ APPROVED ──▶ ENTRY_PENDING ──▶ OPEN ──▶ REDUCING ──▶ CLOSED(outcome)
     │             │              │ (entry canceled/expired)   ▲ (partial TP legs)
     └─▶ REJECTED  │              └─▶ FAILED                   │
        (risk gate)└── (submit error) ─▶ FAILED     OPEN ──────┘
```

## 11.3 Order Lifecycle
```
 PENDING_SUBMIT ──▶ SUBMITTED ──▶ NEW ──▶ PARTIALLY_FILLED ──▶ FILLED
        │               │          │              │
        └─(pre-flight   │          ├─▶ CANCELED   └─▶ CANCELED (remainder)
           reject)      └─▶ REJECTED└─▶ EXPIRED
 REJECTED
```

## 11.4 Position Lifecycle
```
 OPEN ──(fills adjust quantity/avg price; reconciler heals)──▶ CLOSED
 (positions are exchange FACT: created/changed/closed only from
  ORDER_TRADE_UPDATE / ACCOUNT_UPDATE / positionRisk reconciliation)
```

---

# 12. API Specification

All endpoints authenticated (`Depends(get_current_user)`, unchanged).
v1 endpoints remain as byte-compatible facades until the contract phase.
Responses are DTOs shaped by the API layer; enum values served to v1/WPF
use legacy names until each screen migrates.

## 12.1 Signals — owner: Signal Engine (read-only externally)
| Method & Path | Request | Response | Purpose |
|---|---|---|---|
| GET /v2/signals | filters: status, strategy, symbol, is_latest, page | Signal[] (incl. targets) | list signals |
| GET /v2/signals/{id} | — | Signal + targets + evidence | detail |
| GET /v2/signals/{id}/evidence | — | EvidenceRow[] | explainability ledger |
| GET /v2/signals/lineage/{lineage_id} | — | Signal[] (all versions) | version history |

## 12.2 Auto Trading — owner: Auto Trader
| Method & Path | Request | Response | Purpose |
|---|---|---|---|
| GET /v2/auto-trading/status | — | engine state, watch counts | running status |
| PUT /v2/auto-trading/config | enabled, band_policy, per-strategy toggles | config | control |
| GET /v2/auto-trading/watchlist | — | WatchItem[] (signal, distance-to-entry, resolved order type) | monitored signals |

## 12.3 Manual Trading — owner: Manual Trading
| Method & Path | Request | Response | Purpose |
|---|---|---|---|
| POST /v2/manual/execute-market | symbol/direction/qty-or-risk OR signal_id | Trade | immediate MARKET entry |
| POST /v2/manual/place-signal-order | signal_id | Trade | pending order at signal entry (matrix-resolved) |
| POST /v2/manual/close-position/{symbol} | — | Trade(CLOSE) | reduceOnly close, live qty fetched fresh |
| DELETE /v2/manual/orders/{order_id} | — | Order | cancel a pending order |

## 12.4 Orders — owner: Order Manager (read-only externally)
| GET /v2/orders | filters: trade_id, status, purpose, symbol | Order[] | first-class order queries |
| GET /v2/orders/{id} | — | Order + raw payloads | audit detail |

## 12.5 Positions — owner: Position Manager (read-only externally)
| GET /v2/positions | env filter | Position[] + last_reconciled_at | open positions |
| GET /v2/positions/{symbol} | — | Position + linked trades | detail |

## 12.6 Portfolio — owner: Portfolio Manager
| GET /v2/portfolio/risk | — | margin usage, open risk, daily loss, drawdown | risk truth |
| GET /v2/portfolio/exposure | — | per-symbol/direction exposure | exposure |
| GET/PUT /v2/portfolio/limits | limit config | limits | limit management |
| POST /v2/portfolio/kill-switch | — | KillSwitchResult | existing semantics preserved |

## 12.7 Strategies & AI — owners: plugin registry / Feedback Loop
| GET /v2/strategies | — | plugin list + enabled + timeframes | registry |
| PUT /v2/strategies/{id}/enabled | bool | plugin | enable/disable |
| GET /v2/ai/weight-sets?strategy= | — | WeightSet[] (status, reports) | inspect learning |
| POST /v2/ai/weight-sets/{id}/activate | — | WeightSet | governed activation (§8.2) |

## 12.8 Journal & History
| GET /v2/execution-log?correlation_id= | — | Event[] | full story of one signal→trade→orders chain |
| GET /v2/history/trades | filters + pagination | closed Trade[] + outcome | replaces signal-based history |

## 12.9 Legacy facades (until contract)
`POST /trading/execute/{signal_id}` → creates trade via Manual/Auto path ·
`POST /trading/close-position/{symbol}` → /v2/manual/close-position ·
trading-control bulk endpoints → portfolio equivalents · existing GET
endpoints unchanged.

---

# 13. Background Workers

Common rules: supervised tasks (held refs + done-callbacks + cancellation
— the pattern already established by audit fix H-1); every handler
idempotent (event_id dedupe); at-least-once delivery; on restart each
worker replays undispatched outbox rows relevant to it, which is the
recovery mechanism (invariant 4). "Backoff" = exponential 0.25s→2s cap,
the constants already proven in `get_order_with_retry`.

| Worker | Input | Output | Retry logic | Failure recovery |
|---|---|---|---|---|
| MarketDataFeed | Binance market ws | candles/liquidations to bus + cache | reconnect w/ backoff; staleness watchdog | on gap: REST backfill of missed candles; staleness > threshold ⇒ Scanner pauses (never scan frozen prices) |
| ScannerWorker | new-candle events | CandidateDetected | none (next candle is the retry) | stateless; resumes on next candle |
| SignalPipelineWorker (AI Decision → Signal Engine) | CandidateDetected | signals rows + SignalCreated/Revised | DB write retry ×3 backoff, then drop candidate with journal entry | stateless per candidate; journal records dropped candidates |
| EntryWatcher | candle stream + WAITING_FOR_ENTRY signals (is_latest) | EntryTriggered | none needed (pure detection) | rebuilds watchlist from signals table on start |
| UserDataStreamWorker | Binance user-data ws (listenKey) | normalized OrderFilled/etc. | listenKey keepalive every 30 min; reconnect w/ backoff | on reconnect: OrderReconciler full sweep covers the gap window |
| OrderReconciler | interval (60s) + reconnect triggers | order status corrections | REST retry w/ backoff; -2013 tolerated w/ bounded retry (existing logic institutionalized) | it IS the recovery mechanism for missed stream events; alerts on divergence it cannot explain |
| OrderManagerExecutor | TradeApproved | order submissions | submit retry ONLY on idempotent-safe errors (timeouts w/ client_order_id dedupe); never blind-retry rejections | on restart: journal replay resumes mid-sequence (e.g. entry filled, protection missing ⇒ place protection FIRST) |
| PositionWorker | fills, ACCOUNT_UPDATE, interval | positions rows, StopMoved, TargetHit, TradeClosed | stop-replace uses existing place-new-then-cancel-old ordering (never naked) | reconciles positions vs positionRisk on start; unknown exchange position ⇒ alert, never auto-close |
| PortfolioWorker | events + interval (30s) | equity_snapshots, RiskLimitBreached, kill-switch | DB retry ×3 | limits re-evaluated from scratch each tick (stateless) |
| FeedbackWorker | TradeClosed (mainnet-only for learning) | ai_weight_sets PROPOSED/SHADOW scores | batch job; failed run re-runs next cycle | pure derivation from durable tables; no state to lose |
| OutboxDispatcher | execution_log WHERE dispatched_at IS NULL | bus deliveries | per-event retry w/ backoff; poison events parked + alerted after N failures | THE replay mechanism: restart = scan undispatched, redeliver |
| Archiver | daily schedule | archived flags | re-run safe (idempotent) | none needed |

---

# 14. Event Flow & Trading Flows

## 14.1 Event Flow Diagram
```
CandidateDetected ─▶ SignalPipeline ─▶ SignalCreated ─┬─▶ AutoTrader (watch)
                                      SignalRevised  ─┤     │
                                                      └─▶ UI feed
            EntryTriggered ◀─ EntryWatcher ◀─ candle stream │
                  │                                         │
                  └────────────▶ TradeRequested ◀── ManualTrading
                                      │
                            PortfolioManager gate
                             │approved        │rejected
                             ▼                ▼
                        OrderManager      journal (with reasons)
                             │ submit
                             ▼
                     ═ exchange ═ ─▶ user-data stream ─▶ OrderFilled/…
                                                            │
                        ┌───────────────────────────────────┤
                        ▼                                   ▼
                 PositionManager ─▶ StopMoved/TargetHit ─▶ OrderManager (legs)
                        │
                        └─▶ TradeClosed ─▶ SignalEngine (outcome mirror)
                                        └─▶ FeedbackWorker ─▶ WeightSetProposed
   RiskLimitBreached / KillSwitchActivated ─▶ AutoTrader halt + close/cancel all
```

## 14.2 Auto Trading Flow
```
SignalCreated(WAITING_FOR_ENTRY, is_latest)
  ─▶ AutoTrader registers watch
  ─▶ EntryWatcher: price enters zone ─▶ EntryTriggered
  ─▶ TradeRequested ─▶ Portfolio gate ─▶ APPROVED (risk_assessment persisted)
  ─▶ OrderManager: resolve type (§7 matrix) ─▶ ENTRY order (journal first)
  ─▶ user-data stream: FILLED (executedQty)
  ─▶ PositionManager: SL leg + TP1..3 legs (reduceOnly, sized to fills)
  ─▶ manage: breakeven → trail (existing TradeManagementEngine decisions);
             TP1 fill ⇒ REDUCING, resize remaining protection
  ─▶ final leg closes ─▶ TradeClosed(outcome) ─▶ signal mirror ─▶ archive
Supersede while waiting: cancel watch + resting order, evaluate new version.
Expiry while waiting: cancel order, trade FAILED(EXPIRED_UNFILLED), signal EXPIRED.
```

## 14.3 Manual Trading Flow
```
Execute Market:      POST /v2/manual/execute-market
                       ─▶ trade(source=MANUAL_MARKET) ─▶ gate ─▶ MARKET entry
                       ─▶ same fill/protect/manage pipeline as auto
Place Signal Order:  POST /v2/manual/place-signal-order
                       ─▶ trade(source=MANUAL_PENDING) ─▶ gate ─▶ matrix ─▶ LIMIT/STOP
                       ─▶ same pipeline
Close / Cancel:      close-position ─▶ reduceOnly MARKET, live qty fetched fresh
                     cancel order ─▶ Order Manager cancel + journal
The signal is READ, never written, by every manual action.
```

---

# 15. Implementation Roadmap

Durations assume the current single-developer + AI-assisted workflow, in
working days, ±50% honesty band. Every phase: flag default OFF, old path
intact, rollback = flag off. Full test suite + fresh-DB build +
`alembic check` green is an implicit gate on every phase.

| Phase | Objectives | Modules | DB changes | UI changes | API changes | Validation tests | Rollback | Duration |
|---|---|---|---|---|---|---|---|---|
| **A0** | Characterization tests freezing v1 endpoint behavior; approve this spec | — | — | — | — | golden-response suite green | n/a | 2-3 d |
| **A1** | Schema expand + backfill (trades/orders/positions/signal_targets/signal_evidence/execution_log/ai_weight_sets + versioning columns) | DB only | all new tables; signals +lineage/version cols (backfilled lineage_id=id, version=1) | none | none | fresh-DB, alembic check, backfill reconciliation counts | migrations are additive; down-revisions tested | 4-6 d |
| **A2** | ExchangePort protocol around existing services; UserDataStreamWorker + OrderReconciler in shadow (record-only) | Gateway, workers | — | none | none | Testnet soak: shadow records ≡ REST truth | disable workers | 6-8 d |
| **A3** | Order Manager + Trade aggregate; v1 execute becomes facade (dual-write legacy columns); OutboxDispatcher live | OrderMgr, backbone | dual-write on | none | facades (byte-compatible) | harness extended: trades/orders rows asserted (closes W12); divergence=0 | flag off → direct legacy path | 8-12 d |
| **A4** | Auto Trader: EntryWatcher, order-type matrix, **stop entries**, per-signal policy stamp | AutoTrader, Gateway | — | AutoTrading screen: watchlist | /v2/auto-trading/* | Testnet E2E: LIMIT-below, STOP-above, band-edge, expiry, supersede-cancel | flag off | 8-12 d |
| **A5** | Manual Trading module (two distinct actions) | Manual | — | Manual screen | /v2/manual/* | Testnet: both actions + close + cancel | flag off | 5-7 d |
| **A6** | Position Manager: fills→positions, PnL, TP1-3 partial exits, trade mgmt moved off SignalMonitor | PositionMgr | — | Positions screen | /v2/positions | Testnet partial-exit E2E; stop-sync regression | flag off (SignalMonitor path retained) | 8-12 d |
| **A7** | Portfolio Manager: continuous enforcement, kill-switch on breach | PortfolioMgr | limits config | Portfolio/Risk screen | /v2/portfolio/* | limit-breach simulations | flag off | 5-7 d |
| **B1** | AI Decision Engine formalization: evidence ledger persistence, MTF chain execution, confidence=Σ identity | AIDecision, SignalEngine | signal_evidence live | Signals screen shows ledger | /v2/signals evidence endpoints | ledger-sum identity tests; golden-dataset regression | dual-write score_breakdown continues | 6-9 d |
| **B2** | Signal versioning behavior (supersede flow, watch re-evaluation) | SignalEngine, AutoTrader | versioning cols already present | version history view | lineage endpoint | supersede E2E incl. resting-order cancel | revisions off ⇒ single-version behavior | 4-6 d |
| **B3** | Strategy plugin architecture: registry, extract ICT as first plugin; SMC/SMI verdict separation | Plugins | strategy_id live | strategy toggle UI | /v2/strategies | plugin-isolation tests: engine untouched by plugin add | registry pinned to ict only | 8-12 d |
| **B4** | Feedback loop with governance (proposal→shadow→manual activate) | Feedback | ai_weight_sets live | weight-set review UI | /v2/ai/* | shadow-scoring parity tests; env-segregation tests | keep ACTIVE set frozen | 5-8 d |
| **C1** | UI completion: Orders, ExecutionLogs, History screens; screen-by-screen v2 migration | WPF | — | remaining target screens | — | per-screen acceptance; WPF build | old screens retained until accepted | 10-15 d |
| **C2** | Full validation program: restart-recovery (kill between fill and protection ⇒ replay heals), reconciler soak, load test (target: 100+ concurrent watches, 25+ concurrent positions on Testnet) | all | — | — | — | certification report | n/a | 5-8 d |
| **C3** | **Strategy-policy phase (separate approval + backtest evidence):** remove MARKET fallback; anchor SL/TP to entry; 3-target generation | SignalEngine/plugins | — | — | — | before/after backtest comparison | config-revert | 4-6 d + backtest |
| **C4** | **Contract:** drop legacy signal columns, delete v1 facades, retire global entry_mode | DB, API | destructive migration (last) | — | v1 removed | full suite + fresh-DB + WPF build | restore from the retained down-revision | 2-3 d |

Milestone A = execution foundation · B = intelligence layer · C = completion.
Sequencing rule: within a milestone phases are ordered; B may begin once A3
is stable (B does not depend on A4-A7).

---

# 16. Design Challenges & Resolutions (CTO record)

**(a) News Score.** Required as an input "for the future". Weak form
would be a placeholder score — rejected (invariant 5: no fabricated
inputs). Adopted: a `NewsPort` with a null adapter that reports
NOT_AVAILABLE; the ledger shows it as absent; weights renormalize over
available factors. When a real news adapter exists, it slots in with zero
engine change.

**(b) EMA/VWAP as example plugins.** Supported by the plugin architecture
— that is its point — but recorded caution: this platform deliberately
removed retail indicators from the production path in 2026 (ICT-only
migration). Re-admitting them as plugins is safe *only* because weights,
calibration and outcomes are per-strategy; an EMA plugin can never dilute
ICT scoring. Any new plugin still requires backtest evidence before
enablement on mainnet.

**(c) "One final decision" from the AI Decision Engine** with multiple
strategy plugins needs a defined combination rule. Adopted: strategies do
NOT vote into one blended score (blending destroys explainability and
accountability). Each plugin produces its own candidate decision; the
engine scores each independently; multiple concurrent signals on one
symbol from different strategies are allowed, and the Portfolio Manager's
exposure limits arbitrate capital — that is where cross-strategy conflict
belongs, priced in risk, not hidden in score averaging.

**(d) Signal versioning triggers.** The requirement said "every
modification creates a new version". Unbounded, that turns trailing-stop
ticks into version spam. Adopted boundary: versions record *analysis*
revisions only (Signal Engine); execution-time state lives on trades.
This preserves "signals are never overwritten" exactly, without 400
versions per trade.

**(e) "Execution engine simply follows the strategy" (entry types)** is
adopted with one carve-out: feasibility validation. An infeasible
instruction (LIMIT above market for a LONG) is rejected back with a
journaled reason — following it would execute as an accidental market
order, which is worse than disobedience.

**(f) Durations.** Provided as required (§15), with a ±50% band stated
honestly. Phases gate on validation evidence, not on calendar — where the
two conflict, evidence wins.

---

# 17. Validation Program (consolidated)

Per-module suites (scanner golden-dataset; signal invariants incl.
confidence=Σ identity and targets-fraction sum; auto-trading E2E per entry
type; order reconciliation soak; position partial-exit scenarios with PnL
cross-checked against income history; DB integrity + single-writer tests;
restart-recovery via journal replay) plus the standing regression floor:
the existing MARKET-execution Testnet harness runs unchanged in every
phase. Mainnet enablement requires: C2 certification complete, C-1 secret
rotation done (per PRODUCTION_READINESS_AUDIT_2026-07-31), Testnet
confidence override removed, and explicit operator sign-off.

---

*End of specification. Implementation begins at Phase A0 upon approval,
one phase at a time, each phase separately verified before the next.*
