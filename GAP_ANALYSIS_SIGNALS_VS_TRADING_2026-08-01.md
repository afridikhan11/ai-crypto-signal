# Gap Analysis — Signals vs Trading Separation
**Date:** 2026-08-01 · **Status:** AWAITING APPROVAL — no code changed
**Authority:** ARCHITECTURE_SPECIFICATION_FINAL v1.0 (locked). Every
"Required" cell below cites the spec section it comes from. Nothing here
redesigns anything.

---

## 1. What was studied (all verified from source this session)

| Area | Location | Key finding |
|---|---|---|
| Signal module (data) | `app/models/signal.py` | single `take_profit`; no version/lineage; `stop_loss` mutated live by trade management |
| Signal module (generation) | `app/strategy/signal_generator.py:582-592`, `entry_engine.py:134-138` | market-price fallback exists when no ICT anchor |
| Auto Trading module | `AutoTradingViewModel.cs` + `trading_control_service.py` + `signal_monitor.py` | control panel + monitor; **no component watches price and auto-executes** — execution is always a manual click |
| Manual Trading module | — | **does not exist as a distinct module**; there is one Execute action |
| Execute button | `AutoTradingViewModel.cs:291-352` (`[RelayCommand] Execute`) — the only execute wiring in WPF (grep-verified; LiveSignals has none) | one button, one endpoint; label/confirmation vary by signal status |
| Signal Order button | — | **does not exist** |
| Execution API | `POST /trading/execute/{signal_id}`, `trading.py:123-212` | one endpoint, branches on `signal.status` |
| Binance execution path | `binance_trading_service.py` | `place_signal_bracket` (MARKET), `place_limit_entry` (LIMIT); `STOP_MARKET` exists **only reduceOnly** — no stop-entry capability |
| UI bindings | `SignalModel.cs:71-98` (`IsPendingEntry`, `ExecuteButtonLabel`) | button text switches "Execute"/"Arm Entry" from status string |

## 2. Root cause of the reported behavior (Entry 100.445, filled ~97)

Traced exactly:

1. `trading.py:123` — the endpoint branches on **signal status**, not on
   entry-vs-market comparison. `PENDING_ENTRY` → resting LIMIT at
   `signal.entry_price`. **Anything else (`ACTIVE`) → MARKET bracket**
   (`trading.py:171-180`), which fills at whatever the market is (97).
2. Your signal was `ACTIVE`, so it took the MARKET branch. Signals are
   born `ACTIVE` when the global `entry_mode` switch
   (`trading_settings.py:67+`) is `"market"`, or as legacy rows. So the
   behavior you saw is the hidden global switch + status branching — the
   user was never asked "market now, or at the signal's entry?" That
   decision must belong to two explicit buttons (spec §12.3), not to
   invisible state.
3. **Data-integrity defect found while tracing** — `trading.py:192`:
   after a MARKET fill, `signal.actual_fill_price = signal.entry_price`.
   In your example that records 100.445 as the fill price of an order
   that filled at ~97. The real `avgPrice` is in the entry order
   response and is not used. This violates spec invariant 5 (no
   fabricated data) and silently corrupts future PnL/feedback data.
   Listed as gap G6.

## 3. Gap analysis

| # | Current behavior | Required behavior (spec ref) | Files affected |
|---|---|---|---|
| **G1** | One Execute button; meaning depends on signal status + hidden global `entry_mode` | Two never-merged actions: **Execute Market** (ignore signal entry, MARKET now) and **Place Signal Order** (pending order at signal entry via matrix) — §12.3, §14.3 | `trading.py` (new endpoints; old one becomes facade), `schemas/trading.py`, `AutoTradingViewModel.cs`, `AutoTradingView.xaml`, `SignalModel.cs`, `TradingDtos.cs`, `ApiService` usage |
| **G2** | Order type chosen by status branch; no entry-vs-market comparison anywhere | §7 matrix: LONG below→LIMIT, LONG above→STOP, SHORT above→LIMIT, SHORT below→STOP, band→policy | new pure resolver (part of Auto Trader/Manual per §7 — not a new module), used by both manual endpoints; tests |
| **G3** | No stop-entry capability (STOP_MARKET is reduceOnly-only, verified) | STOP entries supported — §7, phase A4 capability | `binance_trading_service.py` (one new entry method, mirrors `place_limit_entry`), harness extension |
| **G4** | Nothing watches price to execute at entry; "wait until price returns" doesn't exist (a SHORT @100 with market 97 can only be manually market-executed at 97 or manually armed as LIMIT) | Auto Trader watches signals, waits, EntryTriggered → matrix → order — §14.2, phase A4 | `EntryWatcher` + Auto Trader per spec (phase A4 scope; **not in the immediate slice below**) |
| **G5** | Signals mutated after generation: monitor moves `signal.stop_loss`; generation can price a signal from market when no anchor | Signals immutable; live stop belongs to Trade (§6.2, A6); market-fallback removal is phase C3 (strategy change, separate approval) | scheduled in locked roadmap phases A6/C3 — **no action now**, listed for completeness |
| **G6** | `actual_fill_price` fabricated as `entry_price` on MARKET fills (`trading.py:192`) | record the real `avgPrice` from the entry order; absent → NULL + warning, never a substitute (invariant 5) | `trading.py`, `binance_trading_service.py` (surface avgPrice on OrderResult), test |
| **G7** | Signals screen: single TP, no version, no strategy column, evidence as prose only | TP1-3, version, strategy, evidence ledger — phases A1/B1/B2 | scheduled in locked roadmap — **no action now** |

## 4. Proposed immediate scope — needs your explicit approval

The locked roadmap orders phases A0→A1→A2→A3 before A5 (Manual Trading).
Your current pain is a G1/G2/G6 problem. Two options:

**Option 1 — follow the roadmap order strictly.** Start A0 (characterization
tests), then A1 (DB expand)… The two buttons arrive with A5, several
phases from now. Architecturally cleanest; slowest to relieve the UX
problem.

**Option 2 (recommended) — approved sequencing deviation: "A5-lite" now.**
Implement G1 + G2 + G6 (and the minimal part of G3 the matrix needs)
against the **existing** schema, shaped exactly as §12.3 so phase A3
later re-routes their internals without changing their contracts:

- `POST /v2/manual/execute-market` → existing MARKET bracket path,
  recording the real fill price (G6 fixed).
- `POST /v2/manual/place-signal-order` → §7 matrix → existing
  `place_limit_entry`, or the new stop-entry method (G3) when the matrix
  says STOP. Infeasible cases rejected with the journaled reason, per §7.
- `POST /trading/execute/{signal_id}` kept, byte-compatible, delegating
  by its current rules — nothing existing breaks (spec backward-compat
  rule).
- WPF Auto Trading screen: two buttons, two distinct confirmations, no
  shared implementation (§12.3 "never merged").
- No DB changes, no new tables, no new modules, no renames. G4 (real
  auto-execution watch), G5, G7 stay exactly where the locked roadmap
  put them.

Implementation would be file-by-file per your rule (analyze → explain →
modify → build → fix → run → test; backend pytest per file; WPF
`dotnet build` per file; Testnet validation of both buttons via the
harness before done).

## 5. Waiting

No code has been written. Which option do you approve — and if Option 2,
confirm the sequencing deviation (A5-lite before A1-A3) so it is on
record against the locked roadmap?
