#!/usr/bin/env python3
"""
Binance TESTNET order-execution validation harness.

=========================================================================
TESTNET ONLY. This script refuses to run against Mainnet, and the refusal
is checked THREE independent ways before a single request is sent.
=========================================================================

It drives the application's OWN `BinanceTradingService` - not a
reimplementation - so what it proves is what production does. Every HTTP
request and response is captured through httpx event hooks attached to the
service's client, with `signature` and `X-MBX-APIKEY` redacted.

PHASES
  read-only  (default)     connectivity, clock skew, exchange info, symbol
                           filters, account state, leverage/margin as
                           REPORTED by Binance, position sizing, risk
                           assessment, database persistence. Places no
                           orders. Safe to run any time.
  --place-orders           additionally opens and closes a real Testnet
                           position: MARKET entry, reduceOnly STOP_MARKET,
                           reduceOnly LIMIT take-profit, a resting LIMIT
                           entry, order-status reads, cancellation and
                           position close. Requires the explicit flag; it
                           is never the default.

USAGE
  docker compose exec app python -m scripts.validate_testnet_execution
  docker compose exec app python -m scripts.validate_testnet_execution --place-orders

OUTPUT
  Human-readable transcript on stdout, and a full JSON transcript at
  data/testnet_validation_<timestamp>.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Redacted in every captured request. The transcript is meant to be pasted
# into a chat or a ticket.
_SECRET_PARAMS = {"signature"}
_SECRET_HEADERS = {"x-mbx-apikey"}

TRANSCRIPT: list[dict] = []
RESULTS: list[dict] = []


# =========================================================================
# transcript capture
# =========================================================================
def _redact_url(url: str) -> str:
    if "?" not in url:
        return url
    base, _, query = url.partition("?")
    parts = []
    for pair in query.split("&"):
        key, _, value = pair.partition("=")
        parts.append(f"{key}=<REDACTED>" if key.lower() in _SECRET_PARAMS else pair)
    return f"{base}?{'&'.join(parts)}"


def _redact_headers(headers) -> dict:
    return {
        k: ("<REDACTED>" if k.lower() in _SECRET_HEADERS else v)
        for k, v in headers.items()
    }


def attach_capture(client) -> None:
    """Attach httpx event hooks to the service's own client.

    This is why the harness uses the real service: the captured traffic IS
    production traffic, not an approximation of it.
    """
    async def on_request(request):
        TRANSCRIPT.append({
            "seq": len(TRANSCRIPT) + 1,
            "at": datetime.now(timezone.utc).isoformat(),
            "direction": "request",
            "method": request.method,
            "url": _redact_url(str(request.url)),
            "headers": _redact_headers(request.headers),
        })

    async def on_response(response):
        await response.aread()
        try:
            body: Any = json.loads(response.text)
        except Exception:
            body = response.text
        TRANSCRIPT.append({
            "seq": len(TRANSCRIPT) + 1,
            "at": datetime.now(timezone.utc).isoformat(),
            "direction": "response",
            "status": response.status_code,
            "url": _redact_url(str(response.request.url)),
            "elapsed_ms": round(response.elapsed.total_seconds() * 1000, 1)
            if response.is_closed else None,
            "body": body,
        })

    hooks = client.event_hooks
    hooks["request"] = list(hooks.get("request", [])) + [on_request]
    hooks["response"] = list(hooks.get("response", [])) + [on_response]


async def get_order_with_retry(service, symbol: str, order_id: int,
                               client_order_id: Optional[str] = None,
                               attempts: int = 8, first_delay: float = 0.25) -> tuple[dict, str]:
    """Read back a just-placed order, tolerating Binance's write/read lag.

    ROOT CAUSE THIS EXISTS FOR
    ---------------------------------------------------------------------
    `POST /fapi/v1/order` is answered by the MATCHING ENGINE. `GET
    /fapi/v1/order` is answered by a separate query service that is
    eventually consistent with it. For a few hundred milliseconds after a
    successful placement, the query service can legitimately answer
    `-2013 Order does not exist` for an order that exists and has already
    filled. It is a propagation race, not a missing order - which is why
    the Binance UI showed the position while the read failed.

    `get_order()` itself is correct: it sends `symbol` and `orderId`,
    exactly as the API documents. Nothing about the parameters needed
    changing.

    WHAT THIS IS NOT
    ---------------------------------------------------------------------
    It is not error suppression. -2013 is retried, bounded, with every
    attempt logged and the observed propagation delay reported. ANY other
    error is re-raised immediately and unchanged. If the order never
    appears within the budget, this raises - the harness fails.

    Falls back to `origClientOrderId` when one was supplied: it is a
    caller-controlled idempotency key, so it resolves even if the
    exchange-assigned id were somehow mis-read.
    """
    delay = first_delay
    started = time.monotonic()
    last: Optional[Exception] = None

    for attempt in range(1, attempts + 1):
        try:
            order = await service.get_order(symbol, order_id)
            elapsed = time.monotonic() - started
            return order, f"orderId after {attempt} attempt(s), {elapsed * 1000:.0f} ms"
        except Exception as exc:
            if "-2013" not in str(exc):
                raise  # a real error - never swallowed, never retried
            last = exc
            print(f"      attempt {attempt}: -2013 not yet visible, retrying in {delay:.2f}s",
                  flush=True)

        if client_order_id:
            try:
                order = await service._signed_get(
                    "/fapi/v1/order",
                    {"symbol": symbol.upper(), "origClientOrderId": client_order_id},
                )
                elapsed = time.monotonic() - started
                return order, (f"origClientOrderId after {attempt} attempt(s), "
                               f"{elapsed * 1000:.0f} ms (orderId lookup still lagging)")
            except Exception as exc:
                if "-2013" not in str(exc):
                    raise
                last = exc

        await asyncio.sleep(delay)
        delay = min(delay * 2, 2.0)

    raise RuntimeError(
        f"order {order_id} still not readable after {attempts} attempts over "
        f"{time.monotonic() - started:.1f}s. Last error: {last}"
    )


async def close_and_confirm_flat(service, symbol: str,
                                  attempts: int = 8, first_delay: float = 0.25) -> tuple[float, list[str]]:
    """Close any open position on `symbol` and confirm the EXCHANGE reports
    it flat before returning - never on the strength of a single read.

    ROOT CAUSE THIS EXISTS FOR
    ---------------------------------------------------------------------
    One layer further out than get_order_with_retry's -2013 problem, same
    shape: close_position()'s reduceOnly MARKET order can fill in the
    matching engine immediately - and show up in Binance's own Trade
    History, which is fed by the user-data-stream push - while
    `GET /fapi/v2/positionRisk` is served by the account/position
    aggregate, which updates from the fill asynchronously. A single read
    taken immediately after the close call can legitimately still report
    the PRE-close quantity even though the position is already gone. That
    is exactly what was observed: Binance's UI showed flat and the
    reduceOnly SELL was already in Trade History, while the harness's
    one-shot re-read still reported positionAmt=0.0361.

    This does not change WHAT is checked - positionAmt from positionRisk,
    the same endpoint used everywhere else in this harness - only that a
    nonzero read is retried with bounded backoff instead of being declared
    a failure immediately. If the position is genuinely still open after
    the budget, this says so; it does not paper over it.

    Returns (final_position_amt, notes) where notes is an ordered list of
    milestone strings (NOT the individual retry attempts, which only go to
    stdout, matching get_order_with_retry's convention) suitable to join
    straight into a record() detail string.
    """
    notes: list[str] = []

    rows = await service._signed_get("/fapi/v2/positionRisk", {"symbol": symbol})
    amt = float(rows[0].get("positionAmt", 0) or 0) if rows else 0.0
    if amt == 0:
        notes.append(f"{symbol} already flat")
        return 0.0, notes

    closed = await service.close_position(symbol, amt)
    notes.append(f"closed positionAmt={amt} via reduceOnly MARKET, orderId={closed.order_id}")

    # Confirm the close order itself reached a terminal state first. Not
    # fatal if this fails or times out - it is diagnostic context for the
    # positionRisk poll below, which is the actual ground truth.
    try:
        fetched, how = await get_order_with_retry(service, symbol, closed.order_id)
        notes.append(f"close order status={fetched.get('status')} (resolved via {how})")
    except Exception as exc:
        notes.append(f"could not confirm close order status before polling position: {exc}")

    delay = first_delay
    started = time.monotonic()
    final = amt
    for attempt in range(1, attempts + 1):
        rows = await service._signed_get("/fapi/v2/positionRisk", {"symbol": symbol})
        final = float(rows[0].get("positionAmt", 0) or 0) if rows else 0.0
        if final == 0:
            elapsed = time.monotonic() - started
            notes.append(f"positionRisk confirmed flat after {attempt} read(s), {elapsed * 1000:.0f} ms")
            return 0.0, notes
        print(f"      attempt {attempt}: positionRisk still reports {final} "
              f"(position aggregate may not have caught up to the fill yet), "
              f"retrying in {delay:.2f}s", flush=True)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 2.0)

    notes.append(f"positionRisk still reports {final} after {attempts} reads over "
                 f"{time.monotonic() - started:.1f}s - see Final position check")
    return final, notes


def record(item: str, status: str, detail: str, evidence: Any = None) -> None:
    RESULTS.append({"item": item, "status": status, "detail": detail, "evidence": evidence})
    marker = {"PASS": "PASS", "FAIL": "FAIL", "SKIP": "SKIP", "N/A": "N/A ", "WARN": "WARN"}[status]
    print(f"  [{marker}] {item}: {detail}", flush=True)


def section(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}", flush=True)


# =========================================================================
# THE MAINNET GUARD - three independent checks
# =========================================================================
def assert_testnet(service) -> None:
    """Abort unless this is unambiguously Testnet.

    Four checks that would each have to fail independently:
      1. the credential record's own testnet flag
      2. the service's derived `environment` string
      3. base_url EQUALS the class's Testnet URL
      4. base_url is NOT the class's Mainnet URL

    (3) and (4) are read from `BinanceTradingService` itself rather than
    hardcoded here. That matters: this project's Testnet host is
    `demo-fapi.binance.com`, not the `testnet.binancefuture.com` that older
    documentation shows. A hardcoded host string in this guard would either
    abort a valid Testnet run or, far worse, fail to recognise Mainnet if
    the constants were ever changed. Deriving both from the class means the
    guard cannot drift away from the code it is guarding.

    Checks (1) and (2) are kept so a DISAGREEMENT between the stored
    credential flag and the resolved URL is surfaced as an error rather
    than one silently winning.
    """
    from app.services.binance_trading_service import BinanceTradingService as _S

    failures = []
    if getattr(service, "testnet", None) is not True:
        failures.append(f"service.testnet is {getattr(service, 'testnet', None)!r}, expected True")
    if getattr(service, "environment", None) != "testnet":
        failures.append(f"service.environment is {getattr(service, 'environment', None)!r}, expected 'testnet'")

    base = str(getattr(service, "base_url", ""))
    if base != _S.FUTURES_TESTNET_URL:
        failures.append(f"base_url is {base!r}, expected {_S.FUTURES_TESTNET_URL!r}")
    if base == _S.FUTURES_MAINNET_URL:
        failures.append(f"base_url IS THE MAINNET URL ({_S.FUTURES_MAINNET_URL!r})")

    if failures:
        print("\n" + "!" * 72)
        print("ABORTED - THIS IS NOT BINANCE TESTNET. NO REQUEST WAS SENT.")
        for f in failures:
            print(f"  - {f}")
        print("!" * 72)
        raise SystemExit(2)

    print(f"  Testnet confirmed on all four checks. base_url={base}")


# =========================================================================
# phases
# =========================================================================
async def phase_connectivity(service) -> bool:
    section("1. CONNECTIVITY AND CLOCK")
    try:
        await service._public_get("/fapi/v1/ping")
        record("Connectivity", "PASS", f"ping succeeded against {service.base_url}")
    except Exception as exc:
        record("Connectivity", "FAIL", f"{type(exc).__name__}: {exc}")
        return False

    try:
        local_ms = int(time.time() * 1000)
        server = await service._public_get("/fapi/v1/time")
        skew = int(server["serverTime"]) - local_ms
        # Binance rejects signed requests whose recvWindow is exceeded.
        # Default recvWindow is 5000ms; >1s of skew is worth flagging before
        # it shows up as an inexplicable -1021 on an order.
        if abs(skew) > 1000:
            record("Clock skew", "WARN",
                   f"{skew} ms vs Binance. Signed requests fail with -1021 once "
                   f"this exceeds recvWindow.", {"skew_ms": skew})
        else:
            record("Clock skew", "PASS", f"{skew} ms", {"skew_ms": skew})
    except Exception as exc:
        record("Clock skew", "FAIL", f"{type(exc).__name__}: {exc}")
    return True


async def phase_exchange_info(service, symbol: str) -> Optional[dict]:
    section("2. EXCHANGE INFO AND SYMBOL FILTERS")
    try:
        filters = await service._get_symbol_filters(symbol)
        record("Exchange info", "PASS", f"exchangeInfo retrieved for {symbol}")

        # `_get_symbol_filters` returns SNAKE_CASE keys - step_size /
        # tick_size / min_notional - not Binance's camelCase filter names.
        # An earlier revision of this harness read the camelCase names, got
        # None for all of them, and still reported PASS. A validation
        # harness that passes on missing data is worse than no harness, so
        # every value is now asserted present and positive.
        step = filters.get("step_size")
        tick = filters.get("tick_size")
        min_notional = filters.get("min_notional")

        # Every one of the three must be present AND positive. None of them
        # may be inferred, defaulted, or skipped.
        #
        # min_notional deserves a word: app code defaults it to 0.0 when
        # Binance reports no MIN_NOTIONAL filter, so 0.0 here is
        # indistinguishable from "the filter was absent" or "parsing
        # failed". Either way the pre-order notional guard in phase 5
        # cannot function, so this is a hard failure rather than a warning.
        bad = []
        for name, value in (("step_size", step), ("tick_size", tick), ("min_notional", min_notional)):
            if value is None:
                bad.append(f"{name} is missing")
            elif float(value) <= 0:
                bad.append(f"{name} is {value} (must be > 0)")
        if bad:
            record("Symbol filters", "FAIL",
                   "; ".join(bad) + ". Orders would be sent unrounded or unchecked - "
                   "Binance rejects those with -1111 (precision) / -4014 (tick size), "
                   "and the minimum-notional guard cannot run.",
                   filters)
            return None

        record("Symbol filters", "PASS",
               f"step_size={step} tick_size={tick} min_notional={min_notional}", filters)

        # ---- rounding exactness -----------------------------------------
        # Measured in UNITS OF THE STEP, not in absolute price. An absolute
        # epsilon cannot be right for both a 0.001 quantity and a $64,000
        # price: 64738.2 rounded to a 0.1 tick leaves a residual of 1.16e-10
        # step-units purely from binary floating point, which is correct
        # rounding, while a large tick could leave an absolute residual big
        # enough to trip a fixed 1e-9 threshold. Normalising by the step
        # holds both to the same standard.
        def _residual_in_units(value: float, unit: float) -> float:
            q = value / unit
            return abs(q - round(q))

        TOLERANCE = 1e-6  # in step units; far tighter than any exchange cares about

        probes_ok = True
        for label, raw, unit, fn in (
            ("Quantity rounding", 1.23456789, float(step), service._round_to_step),
            ("Quantity rounding", 0.07234, float(step), service._round_to_step),
            ("Price rounding", 1.23456789, float(tick), service._round_price),
        ):
            rounded = fn(raw, unit)
            residual = _residual_in_units(rounded, unit)
            ok = residual < TOLERANCE
            probes_ok = probes_ok and ok
            record(label, "PASS" if ok else "FAIL",
                   f"{raw} -> {rounded} | residual {residual:.2e} step-units "
                   f"(tolerance {TOLERANCE:.0e}) | exact multiple: {ok}")

        # Quantity must FLOOR, never round up: rounding a size upward would
        # place a larger position than the risk engine sized and approved.
        floored = service._round_to_step(0.0009 + float(step), float(step))
        if floored > 0.0009 + float(step) + 1e-12:
            record("Quantity rounds down", "FAIL",
                   f"_round_to_step rounded UP ({0.0009 + float(step)} -> {floored}); "
                   f"that would oversize the position relative to the approved risk")
            probes_ok = False
        else:
            record("Quantity rounds down", "PASS",
                   "_round_to_step floors - a rounded size can never exceed the approved size")

        return filters if probes_ok else None
    except Exception as exc:
        record("Exchange info", "FAIL", f"{type(exc).__name__}: {exc}")
        return None


async def phase_account(service, symbol: str) -> Optional[dict]:
    section("3. ACCOUNT, LEVERAGE, MARGIN MODE")
    try:
        positions = await service._signed_get("/fapi/v2/positionRisk", {"symbol": symbol})
        row = positions[0] if isinstance(positions, list) and positions else {}
        record("Leverage (read)", "PASS",
               f"Binance reports leverage={row.get('leverage')} for {symbol}. "
               f"NOTE: this application never SETS leverage "
               f"(binance_trading_service.py:42) - it is whatever you configured "
               f"in the Binance UI.",
               {"leverage": row.get("leverage")})
        record("Margin mode (read)", "PASS",
               f"marginType={row.get('marginType')}. Also never set by this "
               f"application.",
               {"marginType": row.get("marginType")})
        record("Existing position", "PASS" if float(row.get("positionAmt", 0) or 0) == 0 else "WARN",
               f"positionAmt={row.get('positionAmt')} "
               f"({'flat - clean starting state' if float(row.get('positionAmt', 0) or 0) == 0 else 'NOT FLAT - close before validating'})",
               {"positionAmt": row.get("positionAmt")})
        return row
    except Exception as exc:
        record("Account state", "FAIL", f"{type(exc).__name__}: {exc}")
        return None


async def phase_sizing_and_risk(symbol: str, entry: float, stop: float) -> Optional[dict]:
    section("4. POSITION SIZING AND RISK ASSESSMENT")
    try:
        # The account-service factory lives in binance_credentials.py and is
        # named build_service_from_saved (the TRADING one is
        # build_trading_service_from_saved). Both are in the same module.
        from app.services.binance_credentials import build_service_from_saved
        from app.services.position_sizing import calculate_position_size

        account = build_service_from_saved()
        if account is None:
            record("Position sizing", "SKIP", "no saved credentials for the account service")
            return None
        async with account:
            snapshot = await account.get_full_snapshot()

        # `get_full_snapshot()` returns an AccountSnapshot, NOT a
        # FuturesAccountInfo. The futures figures live one level down at
        # `snapshot.futures`; AccountSnapshot itself has no margin_balance
        # attribute at all, so reading it off the top level silently
        # yielded None. Production reaches it correctly via
        # `signal_service._get_futures_account()`, which returns
        # `_ACCOUNT_BALANCE_CACHE["futures"]` - i.e. the nested object.
        for warning in getattr(snapshot, "warnings", []) or []:
            record("Snapshot warning", "WARN", warning)

        futures = getattr(snapshot, "futures", None)
        if futures is None:
            # A real, reportable condition - the API key has no futures
            # permission, or the futures fetch failed. Not something to
            # paper over with a default: every downstream risk metric would
            # then be computed against a fabricated equity.
            record("Account equity", "FAIL",
                   "snapshot.futures is None - the futures account is unavailable "
                   "(API key lacks futures permission, or the fetch failed). See the "
                   "snapshot warnings above. No default substituted.")
            return None

        # margin_balance is a REQUIRED float on FuturesAccountInfo, mapped
        # from Binance's `totalMarginBalance`. If it is somehow absent the
        # model itself is wrong, and that must fail loudly rather than fall
        # back to wallet_balance - the two diverge as soon as a position
        # carries unrealised PnL, and the risk engine's whole equity
        # denominator depends on the distinction.
        equity = getattr(futures, "margin_balance", None)
        if equity is None:
            record("Account equity", "FAIL",
                   "FuturesAccountInfo.margin_balance is None despite being a required "
                   "field - check the totalMarginBalance mapping in "
                   "binance_account_service.get_futures_account()")
            return None

        record("Account equity", "PASS",
               f"margin_balance={equity} wallet_balance={futures.wallet_balance} "
               f"unrealized_pnl={futures.unrealized_pnl} "
               f"available={futures.available_balance}",
               {"margin_balance": equity,
                "wallet_balance": futures.wallet_balance,
                "unrealized_pnl": futures.unrealized_pnl,
                "available_balance": futures.available_balance,
                "total_maintenance_margin": futures.total_maintenance_margin})

        # The identity the Risk Engine's equity denominator rests on.
        # Flat account => they coincide; the check is here so a future
        # divergence is caught while a position is open.
        drift = abs((futures.wallet_balance + futures.unrealized_pnl) - equity)
        record("Equity identity", "PASS" if drift < 0.01 else "WARN",
               f"wallet_balance + unrealized_pnl = "
               f"{futures.wallet_balance + futures.unrealized_pnl:.8f} vs "
               f"margin_balance = {equity:.8f} (drift {drift:.8f})")

        size = calculate_position_size(equity, entry, stop, 1.0)
        if not size:
            record("Position sizing", "FAIL",
                   f"returned None for entry={entry} stop={stop} equity={equity}")
            return None
        record("Position sizing", "PASS",
               f"qty={size['quantity']} notional={size['notional_usd']} risk_usd={size['risk_usd']}",
               size)

        from app.risk.risk_engine import RiskEngine
        assessment = RiskEngine().assess_new_trade(
            account_balance=equity, entry_price=entry, stop_loss=stop, risk_percent=1.0,
        )
        record("Risk engine", "PASS" if assessment.approved else "WARN",
               f"approved={assessment.approved} reasons={assessment.reasons}",
               {"approved": assessment.approved, "reasons": assessment.reasons,
                "version": getattr(assessment, "risk_engine_version", None)})
        return size
    except Exception as exc:
        record("Position sizing", "FAIL", f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
        return None


async def phase_orders(service, symbol: str, size: dict, filters: dict, mark: float) -> None:
    """Opt-in. Opens and then fully closes a real Testnet position."""
    section("5. ORDER EXECUTION (LIVE ON TESTNET)")

    qty = service._round_to_step(float(size["quantity"]), float(filters["step_size"]))
    tick = float(filters["tick_size"])

    # A rounded-to-zero quantity would be sent to Binance as 0 and rejected;
    # worse, a quantity below min_notional silently wastes a round trip.
    if qty <= 0:
        record("Order execution", "FAIL",
               f"quantity rounded to {qty} at step_size {filters['step_size']} - "
               f"account equity is too small to open a valid position in {symbol}")
        return
    notional = qty * mark
    min_notional = float(filters.get("min_notional") or 0.0)
    if min_notional and notional < min_notional:
        record("Order execution", "FAIL",
               f"notional {notional:.2f} < exchange minimum {min_notional} for {symbol}. "
               f"Use a cheaper symbol for this validation.")
        return
    # Deliberately far from mark so nothing fills unexpectedly during the run.
    limit_px = service._round_price(mark * 0.90, tick)
    stop_px = service._round_price(mark * 0.95, tick)
    tp_px = service._round_price(mark * 1.05, tick)

    opened = False
    try:
        # ---- MARKET entry ------------------------------------------------
        # A caller-supplied client order id gives the read-back a second,
        # independent way to resolve the order. entry_side is a named
        # variable, reused below for the LIMIT order and for deriving
        # place_protective_orders' `direction`, so all three can't drift
        # apart from each other.
        entry_coid = f"val-mkt-{int(time.time())}"
        entry_side = "BUY"  # this harness only ever opens a long
        entry = await service._signed_post("/fapi/v1/order", {
            "symbol": symbol, "side": entry_side, "type": "MARKET", "quantity": qty,
            "newClientOrderId": entry_coid,
        })
        opened = True
        record("Market order", "PASS",
               f"orderId={entry.get('orderId')} status={entry.get('status')} "
               f"executedQty={entry.get('executedQty')} avgPrice={entry.get('avgPrice')}",
               entry)

        # ---- order status synchronisation --------------------------------
        try:
            fetched, how = await get_order_with_retry(
                service, symbol, int(entry["orderId"]), entry_coid
            )
            agrees = str(fetched.get("orderId")) == str(entry.get("orderId"))
            record("Order status sync", "PASS" if agrees else "FAIL",
                   f"resolved via {how}; status={fetched.get('status')} "
                   f"executedQty={fetched.get('executedQty')} "
                   f"avgPrice={fetched.get('avgPrice')}", fetched)
        except Exception as exc:
            # Reported, but NOT fatal to the rest of the phase. The position
            # is already open at this point; abandoning here is what left an
            # unprotected position on the account last time. Protective
            # orders come first, cleanup still runs in `finally`.
            record("Order status sync", "FAIL", f"{type(exc).__name__}: {exc}")

        # ---- protective orders (reduceOnly SL + TP) ----------------------
        # place_protective_orders takes `direction` ("LONG"/"SHORT"), not
        # `side` - that mismatch is what raised "TypeError:
        # place_protective_orders() got an unexpected keyword argument
        # 'side'" before either order was attempted. direction is derived
        # from entry_side instead of hardcoded a second time, so the two
        # cannot drift apart. The service signature itself is unchanged -
        # only this call site now matches it.
        #
        # Its docstring also requires `quantity` to be the ACTUAL FILLED
        # quantity, not the requested size ("a partial fill protected at
        # the full requested size would leave a reduceOnly order larger
        # than the position"). MARKET orders fill immediately in practice,
        # but Testnet liquidity is thin enough that a partial fill isn't
        # impossible, so this reads executedQty off the entry response
        # (available synchronously in the POST reply itself - this is not
        # the query-service field that lags) instead of reusing the
        # pre-trade `qty`, falling back to `qty` only if executedQty is
        # somehow missing or zero.
        #
        # place_protective_orders NEVER raises: a failed leg is reported in
        # the returned warnings list while the other leg still goes out
        # (its documented "say so loudly rather than pretend it isn't"
        # contract). Treating a non-raising call as success would repeat
        # the exact "PASS over missing data" mistake already fixed once in
        # phase_exchange_info, so each leg is checked against what was
        # actually returned, not against the absence of an exception.
        direction = "LONG" if entry_side == "BUY" else "SHORT"
        filled_qty = float(entry.get("executedQty") or 0) or qty
        sl_order, tp_order, protective_warnings = await service.place_protective_orders(
            symbol=symbol, direction=direction, quantity=filled_qty,
            stop_loss=stop_px, take_profit=tp_px, signal_id=None,
        )
        for warning in protective_warnings:
            record("Protective order warning", "WARN", warning)

        if sl_order:
            record("Stop Loss order", "PASS",
                   f"orderId={sl_order.order_id} status={sl_order.status} "
                   f"stopPrice={sl_order.price} quantity={sl_order.quantity}",
                   sl_order.model_dump())
        else:
            record("Stop Loss order", "FAIL",
                   "no stop-loss order returned - position is OPEN WITHOUT A STOP, see warnings above")

        if tp_order:
            record("Take Profit order", "PASS",
                   f"orderId={tp_order.order_id} status={tp_order.status} "
                   f"price={tp_order.price} quantity={tp_order.quantity}",
                   tp_order.model_dump())
        else:
            record("Take Profit order", "FAIL",
                   "no take-profit order returned - set TP manually, see warnings above")

        # ---- reduceOnly actually set on the exchange? --------------------
        open_orders = await service.get_all_open_orders(symbol)
        reduce_only = [o for o in open_orders if o.get("reduceOnly") in (True, "true", "True")]
        record("Reduce Only", "PASS" if reduce_only else "FAIL",
               f"{len(reduce_only)}/{len(open_orders)} resting orders carry reduceOnly=true "
               f"(exchange-confirmed, not just requested)",
               [{"orderId": o.get("orderId"), "type": o.get("type"),
                 "reduceOnly": o.get("reduceOnly")} for o in open_orders])

        # ---- resting LIMIT entry (the ICT pending-entry model) -----------
        limit = await service._signed_post("/fapi/v1/order", {
            "symbol": symbol, "side": entry_side, "type": "LIMIT", "quantity": qty,
            "price": limit_px, "timeInForce": "GTC",
        })
        record("Limit order", "PASS",
               f"orderId={limit.get('orderId')} price={limit.get('price')} "
               f"status={limit.get('status')} (placed 10% below mark so it rests)",
               limit)

        # ---- cancellation ------------------------------------------------
        await service.cancel_order(symbol, int(limit["orderId"]))
        after = await service.get_all_open_orders(symbol)
        gone = all(str(o.get("orderId")) != str(limit["orderId"]) for o in after)
        record("Order cancellation", "PASS" if gone else "FAIL",
               f"orderId={limit.get('orderId')} "
               f"{'no longer resting' if gone else 'STILL RESTING after cancel'}",
               [o.get("orderId") for o in after])

    except Exception as exc:
        record("Order execution", "FAIL", f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    finally:
        # ---- cleanup is not optional -------------------------------------
        section("6. CLEANUP - CANCEL ALL, CLOSE POSITION")
        try:
            for order in await service.get_all_open_orders(symbol):
                await service.cancel_order(symbol, int(order["orderId"]))
            record("Cancel remaining orders", "PASS", "all resting orders cancelled")
        except Exception as exc:
            record("Cancel remaining orders", "FAIL", f"{type(exc).__name__}: {exc}")

        if opened:
            try:
                final, notes = await close_and_confirm_flat(service, symbol)
                record("Position closing", "PASS", " | ".join(notes))
                record("Final position check", "PASS" if final == 0 else "FAIL",
                       f"positionAmt={final} "
                       f"{'flat' if final == 0 else 'STILL OPEN - CLOSE THIS MANUALLY'}")
            except Exception as exc:
                record("Position closing", "FAIL",
                       f"{type(exc).__name__}: {exc} - CHECK YOUR TESTNET ACCOUNT MANUALLY")


async def phase_flatten(service, symbol: str) -> None:
    """Cancel every resting order and close any open position. Nothing else.

    Exists because the previous run left an open, unprotected position: the
    order-status read raised, the exception unwound past the protective-order
    step, and the operator was left holding a bare position. This gives a
    single, targeted way back to flat without re-running the order phase.
    """
    section("FLATTEN - CANCEL ALL ORDERS, CLOSE POSITION")

    try:
        open_orders = await service.get_all_open_orders(symbol)
        if not open_orders:
            record("Cancel all orders", "PASS", f"no resting orders on {symbol}")
        else:
            for order in open_orders:
                await service.cancel_order(symbol, int(order["orderId"]))
                print(f"      cancelled orderId={order.get('orderId')} type={order.get('type')}",
                      flush=True)
            record("Cancel all orders", "PASS", f"cancelled {len(open_orders)} order(s)",
                   [o.get("orderId") for o in open_orders])
    except Exception as exc:
        record("Cancel all orders", "FAIL", f"{type(exc).__name__}: {exc}")

    try:
        final, notes = await close_and_confirm_flat(service, symbol)
        record("Close position", "PASS", " | ".join(notes))
        record("Final position check", "PASS" if final == 0 else "FAIL",
               f"positionAmt={final} "
               f"{'FLAT' if final == 0 else 'STILL OPEN - CLOSE IT IN THE BINANCE UI'}")
    except Exception as exc:
        record("Close position", "FAIL",
               f"{type(exc).__name__}: {exc} - CHECK THE BINANCE UI MANUALLY")


async def phase_database() -> None:
    section("7. DATABASE PERSISTENCE")
    try:
        from sqlalchemy import text
        from app.core.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            for label, sql in (
                ("signals", "SELECT count(*) FROM signals"),
                ("signals PENDING_ENTRY", "SELECT count(*) FROM signals WHERE status = 'PENDING_ENTRY'"),
                ("signals executed", "SELECT count(*) FROM signals WHERE executed = true"),
                ("risk_assessments", "SELECT count(*) FROM risk_assessments"),
                ("equity_snapshots", "SELECT count(*) FROM equity_snapshots"),
            ):
                n = (await session.execute(text(sql))).scalar()
                record(f"DB: {label}", "PASS", f"{n} row(s)", {"count": n})

            row = (await session.execute(text(
                "SELECT risk_engine_version, context_source, approved "
                "FROM risk_assessments ORDER BY created_at DESC LIMIT 1"
            ))).first()
            if row:
                record("Risk audit row", "PASS",
                       f"version={row[0]} context_source={row[1]} approved={row[2]}",
                       {"risk_engine_version": row[0], "context_source": row[1]})
            else:
                record("Risk audit row", "SKIP",
                       "no assessment persisted yet - execute a signal through "
                       "POST /trading/execute/{signal_id} to exercise this path")
    except Exception as exc:
        record("Database persistence", "FAIL", f"{type(exc).__name__}: {exc}")


# =========================================================================
async def main() -> int:
    parser = argparse.ArgumentParser(description="Binance Testnet execution validation")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--place-orders", action="store_true",
                        help="actually open and close a Testnet position")
    parser.add_argument("--flatten", action="store_true",
                        help="ONLY cancel all resting orders and close any open "
                             "position for --symbol, then exit. Places nothing.")
    args = parser.parse_args()

    print(f"Binance TESTNET execution validation - {datetime.now(timezone.utc).isoformat()}")
    print(f"Symbol: {args.symbol}   Mode: "
          f"{'LIVE ORDERS' if args.place_orders else 'READ-ONLY (pass --place-orders to trade)'}")

    from app.services.binance_credentials import build_trading_service_from_saved
    service = build_trading_service_from_saved()
    if service is None:
        print("\nNo saved Binance credentials. Save Testnet credentials in the app first.")
        return 2

    section("0. MAINNET GUARD")
    assert_testnet(service)
    attach_capture(service._client)

    async with service:
        if args.flatten:
            # Deliberately before every other phase and exiting immediately:
            # getting back to a known-flat state must never depend on the
            # rest of the harness succeeding.
            await phase_flatten(service, args.symbol)
            failures = [r for r in RESULTS if r["status"] == "FAIL"]
            print(f"\n  flatten complete - FAIL={len(failures)}")
            return 1 if failures else 0

        if not await phase_connectivity(service):
            return 1
        filters = await phase_exchange_info(service, args.symbol)
        await phase_account(service, args.symbol)

        mark = None
        try:
            ticker = await service._public_get("/fapi/v1/ticker/price", {"symbol": args.symbol})
            mark = float(ticker["price"])
            record("Mark price", "PASS", f"{args.symbol} = {mark}")
        except Exception as exc:
            record("Mark price", "FAIL", f"{type(exc).__name__}: {exc}")

        size = None
        if mark:
            size = await phase_sizing_and_risk(args.symbol, mark, mark * 0.98)

        if args.place_orders:
            if filters and size and mark:
                await phase_orders(service, args.symbol, size, filters, mark)
            else:
                record("Order execution", "SKIP",
                       "prerequisites failed (filters/sizing/mark price) - not trading blind")
        else:
            for item in ("Market order", "Limit order", "Stop Loss order", "Take Profit order",
                         "Reduce Only", "Order cancellation", "Position closing",
                         "Order status sync"):
                record(item, "SKIP", "read-only mode; re-run with --place-orders")

        await phase_database()

    # ---- summary ---------------------------------------------------------
    section("SUMMARY")
    counts: dict[str, int] = {}
    for r in RESULTS:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("  " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    failed = [r for r in RESULTS if r["status"] == "FAIL"]
    if failed:
        print("\n  FAILURES:")
        for r in failed:
            print(f"    - {r['item']}: {r['detail'].splitlines()[0]}")

    out = _PROJECT_ROOT / "data" / f"testnet_validation_{int(time.time())}.json"
    out.write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(),
         "symbol": args.symbol,
         "placed_orders": args.place_orders,
         "base_url": service.base_url,
         "results": RESULTS,
         "http_transcript": TRANSCRIPT},
        indent=2, default=str), encoding="utf-8")
    print(f"\n  Full HTTP transcript ({len(TRANSCRIPT)} entries): {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
