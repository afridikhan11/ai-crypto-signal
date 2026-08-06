# Execution-Layer Investigation — 2026-07-31

Read-only. No code changed. Every claim cites the file/line it was verified
against. Scope: `scripts/validate_testnet_execution.py` and the production
paths it's supposed to be validating — credentials, scanner, strategy, risk
calculation, Docker, and Alembic were not touched or re-examined.

---

## 1. `place_protective_orders()` — signature re-verified, still matches

Current service signature, read fresh
(`app/services/binance_trading_service.py:505-513`):

```python
async def place_protective_orders(
    self, symbol: str, direction: str, quantity: float,
    stop_loss: float, take_profit: float, signal_id: Optional[str] = None,
) -> tuple[Optional[OrderResult], Optional[OrderResult], list[str]]
```

Current harness call site (`validate_testnet_execution.py:647-650`):

```python
sl_order, tp_order, protective_warnings = await service.place_protective_orders(
    symbol=symbol, direction=direction, quantity=filled_qty,
    stop_loss=stop_px, take_profit=tp_px, signal_id=None,
)
```

Matches — `direction` not `side`, 3-tuple correctly unpacked, `quantity` is
`filled_qty` (read from the entry order's `executedQty`, not the pre-trade
size). No drift since the last fix. Nothing to change here.

---

## 2. Protective-order lifecycle — real gaps, not "no exception occurred"

The harness currently records PASS for Stop Loss / Take Profit when
`sl_order`/`tp_order` is truthy (lines 654-670). That's real progress over
the old bug (a TypeError before either order was attempted), but it stops
short of what item 2 is asking for. Tracing exactly what `sl_order` /
`tp_order` contain, field by field:

```python
sl_order = OrderResult(
    order_id=sl_raw["orderId"], symbol=symbol, side=exit_side,
    order_type="STOP_MARKET", status=sl_raw.get("status", "NEW"),
    quantity=qty, price=stop_price,
)
```
(`binance_trading_service.py:554-558`, take-profit is the same shape at
577-582)

Only `order_id` and `status` are read back from Binance's actual response
(`sl_raw`). `order_type` is a **literal string** the service writes,
not `sl_raw.get("type")`. `quantity` and `price` are the **locally rounded
values that were sent in the request** (`qty`, `stop_price`), not
`sl_raw.get("origQty")` / `sl_raw.get("stopPrice")`. This is correct and
deliberate on the service's side — `OrderResult` is a normalized return
shape used by several callers — but it means the harness, by reading
`sl_order.quantity` / `sl_order.price` / `sl_order.order_type`, is
**re-displaying what it asked for, not confirming what the exchange did**.
An order that Binance silently modified or rejected-then-retried
differently would still produce a `sl_order` that looks identical.

Concretely, three things item 2 asks for are not actually checked today:

- **Quantities**: `sl_order.quantity` / `tp_order.quantity` are printed,
  never compared against `filled_qty`.
- **Trigger prices**: `sl_order.price` / `tp_order.price` are printed,
  never compared against `stop_px` / `tp_px`.
- **reduceOnly flags, per order**: the existing check (lines 672-679) reads
  `get_all_open_orders(symbol)` — which *is* real exchange data — but only
  asserts `if reduce_only:` (at least one resting order, of any kind, is
  reduceOnly). It isn't keyed to `sl_order.order_id` / `tp_order.order_id`
  specifically. Right now that's harmless only because of ordering: this
  check runs after SL+TP are placed but before the LIMIT entry exists, so
  the two open orders at that moment happen to be exactly SL and TP. It's
  correct by accident of sequence, not by construction — reorder the phase
  (see §4) and it stops being safe to reason about.

**What would close this**, cross-referencing against the *same*
`get_all_open_orders()` call already being made (real `/fapi/v1/openOrders`
fields — `orderId`, `type`, `side`, `price`, `stopPrice`, `origQty`,
`reduceOnly`, confirmed from the method's own docstring and existing
callers in this file): for `sl_order.order_id` specifically, assert exactly
one entry in the open-orders list with that `orderId`, `type ==
"STOP_MARKET"`, `reduceOnly` true, `origQty` matching `filled_qty` (rounded
to `step_size`), and `stopPrice` matching `stop_px` (rounded to
`tick_size`). Same shape for `tp_order.order_id` with `type == "LIMIT"` and
`price` instead of `stopPrice`. That turns "an OrderResult came back" into
"the exchange is holding exactly what we asked for" — not implemented yet,
described here for your review.

---

## 3. Cleanup lifecycle — mostly compliant, one real gap

Requested: wait for the close order's terminal state, refresh position,
confirm `positionAmt == 0`, fail only on timeout.

What `close_and_confirm_flat()` (`validate_testnet_execution.py:189-253`)
actually does:

- Position-flat check: bounded retry, 8 attempts, 0.25s→2s backoff,
  returns the real final amount either way, **never raises** — the caller
  decides PASS/FAIL from the returned value. This part already satisfies
  "fail only if timeout expires": there is no other way for this half to
  produce a FAIL.
- Close-order status check: calls `get_order_with_retry`, which resolves
  as soon as the order is *readable* (no longer `-2013`) — it does not
  assert `status` is one of `FILLED` / `CANCELED` / `EXPIRED` / `REJECTED`.
  For a reduceOnly MARKET order specifically this is low-risk in practice —
  Binance Futures MARKET orders don't rest, so "readable" and "terminal"
  should coincide — but the code doesn't state that assumption or verify
  it.
- More importantly: if that status check fails or times out, the exception
  is caught and reduced to a single note string
  (`could not confirm close order status before polling position: ...`),
  folded into the same "Position closing" detail as everything else. It
  never produces a WARN or FAIL of its own. Item 3 says "fail only if
  timeout expires" — read literally, a timeout on *this* step should be
  visible as a failure of *this* step, not silently absorbed into a detail
  string three levels down.

**Proposed tightening** (not implemented): after `get_order_with_retry`
resolves, explicitly check `fetched.get("status") in {"FILLED", "CANCELED",
"EXPIRED", "REJECTED"}` and note if it resolved to something else (e.g.
still `NEW` — which would itself be a meaningful anomaly for a MARKET
order). If the retry budget is exhausted, keep it non-fatal to reaching the
position-flat poll (severing that was what left an unprotected position
open two runs ago — see the module's own history) but record it as an
explicit WARN result, not just a note string, so it's visible in the
summary counts instead of only in the joined detail text.

---

## 4. Full path ordering — Entry → Cleanup → Database confirmed; one internal reorder needed

At the `main()` level (`validate_testnet_execution.py:826-855`): Connectivity
→ Exchange Info → Account → Mark Price → Sizing/Risk → `phase_orders()`
(which ends with Cleanup in its own `finally`) → `phase_database()`. That
part matches Entry → ... → Cleanup → Database Persistence.

**Inside** `phase_orders()`, the current order (lines 591-670) is:

```
MARKET entry → Order Status sync → Protective Orders
```

Your requested sequence is Entry → Protective Orders → Order Status. This
isn't just a preference — reordering is the safer choice, and I'd recommend
making it:

`filled_qty` for the protective-order call already comes from
`entry.get("executedQty")`, read directly off the MARKET order's own POST
response (line 646) — it does **not** depend on the order-status-sync GET
succeeding. So Order Status sync currently buys nothing by running first;
it only adds however long its retry budget takes (up to ~12s worst case,
per `get_order_with_retry`'s 8-attempt schedule) to the window during which
a filled position has no stop-loss or take-profit attached. Moving
Protective Orders immediately after the MARKET entry and Order Status sync
after that shortens the unprotected window to whatever the two protective
`POST`s themselves take — a genuine safety improvement, not just sequence
cosmetics. Not implemented yet, flagging for your confirmation since it
changes the phase's control flow.

---

## 5. `risk_assessments = 0` — traced to source, not a bug

Grepped every call site of `record_assessment()` (the only function that
writes to `risk_assessments` — `app/services/risk_audit.py:36-69`, wraps
the insert in try/except and never raises, per its own documented failure
discipline):

```
$ grep -rn "record_assessment(" app/ --include=*.py
app/services/execution_risk.py:189:    audit_id = await record_assessment(
```

One call site, total. It's inside `assess_execution_risk()`
(`execution_risk.py`), which requires an actual `app.models.signal.Signal`
row — it pulls `signal_id`, entry/stop, and account context from it. The
**only** caller of `assess_execution_risk()` is
`POST /trading/execute/{signal_id}` (`trading.py:95`), which loads that
`Signal` from the database by `signal_id` before calling it.

The harness's `phase_sizing_and_risk()` never creates a `Signal` row and
never calls that endpoint. It calls `RiskEngine().assess_new_trade(...)`
directly — the stateless decision function, one layer below
`assess_execution_risk()`, which by design has no persistence of its own
(persistence is `execution_risk.py`'s responsibility, deliberately kept
separate — see that module's own docstring on why it has zero FastAPI
dependency). The harness drives `BinanceTradingService` straight against a
bare symbol string; there is no `Signal` object anywhere in its execution
path for `record_assessment()` to attach an audit row to.

**Conclusion: the harness bypasses the production execution endpoint, by
original design** — it validates the exchange-communication layer
(connectivity, sizing math, order placement, position sync), not the
Signal/RiskEngine/persistence layer, which is a separate, already-tested
path (`LIVE_EXECUTION_SAFETY_REPORT.md`). `risk_assessments = 0` after a
harness run is the *correct*, expected count for what this script actually
exercises. It was never asserted against a nonzero expectation either —
`phase_database()` (`validate_testnet_execution.py`, DB persistence phase)
records every one of its five counts as PASS unconditionally, count
included only as information:

```python
n = (await session.execute(text(sql))).scalar()
record(f"DB: {label}", "PASS", f"{n} row(s)", {"count": n})
```

So this was never a false PASS masking a failure — it's an informational
count that happens to legitimately be zero for this validation shape. I'm
not fabricating a row to make it nonzero. If you want a run that actually
exercises `record_assessment()`, that requires driving the harness through
`POST /trading/execute/{signal_id}` against a real, pre-seeded `Signal`
row — a materially different (and larger) validation than "drive the
trading service directly," not a fix to the current script. Your call on
whether that's worth building; not started.

---

## 6. `signals.executed = 0` — same root cause as §5

```
$ grep -rn "\.executed = True" app/ --include=*.py
app/api/v1/endpoints/trading.py:144
app/api/v1/endpoints/trading.py:186
```

Both inside `execute_signal()` — the same `POST /trading/execute/{signal_id}`
endpoint from §5, both after the risk gate passes and an order is actually
placed (one for the ICT pending-LIMIT path, one for the market-entry path).
There is no other place in the codebase that sets `Signal.executed`.

Same structural answer as risk_assessments: the harness never creates or
loads a `Signal` row, so nothing in its run could ever set `executed =
True` — this table's `executed` count reads 0 because the harness
operates entirely below the Signal layer, not because persistence is
broken. This is expected given the harness's current scope, not a defect
to patch. The same either/or applies: either treat 0 as the correct result
for this validation shape and document it as such in `phase_database()`'s
output (so a future reader doesn't mistake "informational count" for
"assertion"), or build a separate, endpoint-level validation if you want
`executed` to actually flip during a Testnet run. Explaining, not deciding
this for you.

---

## Summary — what's confirmed correct vs. what needs a decision

| # | Item | Finding |
|---|---|---|
| 1 | `place_protective_orders` call | Matches current signature exactly. No action. |
| 2 | Protective-order lifecycle | Truthiness-only checks today; quantity/price/type/reduceOnly are not cross-verified against exchange data per-order. Fix proposed, not implemented. |
| 3 | Cleanup lifecycle | Position-flat check already fails-only-on-timeout correctly. Close-order terminal-state check doesn't assert a terminal status and swallows its own timeout into a note instead of a visible result. Fix proposed, not implemented. |
| 4 | Full path ordering | Entry→Cleanup→Database confirmed correct. Order Status currently runs before Protective Orders inside `phase_orders`, which is safe today only because `filled_qty` doesn't depend on it — reordering to match your sequence shortens the unprotected-position window. Fix proposed, not implemented. |
| 5 | `risk_assessments = 0` | Traced to source: only writer is `record_assessment()`, only caller is `assess_execution_risk()`, only caller of that is the `/trading/execute` endpoint, which requires a `Signal` row the harness never creates. Expected, not missing. No fake record written. |
| 6 | `signals.executed = 0` | Same root cause as #5 — `executed = True` is set only inside the same endpoint the harness never calls. Expected, not missing. |

Nothing has been changed. Items 2-4 have concrete, scoped fixes described
above, ready to implement on your go-ahead. Items 5-6 aren't code problems
in the harness or in production — they're a scope boundary worth a decision
from you: accept 0 as correct and label it that way, or build the
separate endpoint-level validation that would actually exercise persistence.
