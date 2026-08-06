"""
Entry order-type resolver - the Final Architecture Specification v1.0 §7
execution matrix, implemented exactly as locked. A5-lite (G2), 2026-08-01.

WHAT THIS IS
--------------------------------------------------------------------------
The single place that answers: "given a signal's planned entry and the
CURRENT market price, what order type implements that entry right now?"

    LONG   entry below current price  ->  LIMIT BUY
    LONG   entry above current price  ->  STOP-MARKET BUY   (stop entry)
    SHORT  entry above current price  ->  LIMIT SELL
    SHORT  entry below current price  ->  STOP-MARKET SELL  (stop entry)
    entry within the band             ->  configured policy:
                                          "reject" | "immediate_market"

This decision was previously made NOWHERE: POST /trading/execute chose
MARKET-vs-LIMIT from Signal.status alone, which is how a SHORT with
entry=100.445 was market-sold at ~97 (see
SIGNAL_STATE_MACHINE_CURRENT_2026-08-01.md). Per the spec's execution
rule, Signal.status describes the ANALYSIS lifecycle only - execution
decisions come from (current price, entry, direction, this matrix).

WHY THE BAND EXISTS (spec §7, edge rule 1)
--------------------------------------------------------------------------
A LIMIT order whose price is at/through the current market fills
immediately - it would be a market order wearing a limit order's clothes,
which is exactly the surprise this module exists to remove. Inside the
band the caller must apply an EXPLICIT policy: refuse, or knowingly
execute at market. The default is "reject": the safe behaviour is the one
that requires no configuration.

DESIGN CONSTRAINTS
--------------------------------------------------------------------------
- Pure and stdlib-only: no I/O, no imports from the rest of the app, so
  the full matrix is provable by tests with zero infrastructure. The
  Manual Trading endpoints use it today; the Phase-A4 Auto Trader consumes
  the SAME function later (§7 rule 2) - one matrix, never two.
- This module never talks to an exchange and never reads a Signal row.
  Callers pass plain numbers.
"""
from __future__ import annotations

from dataclasses import dataclass

# Order types (Binance Futures names, matching binance_trading_service usage)
ORDER_TYPE_LIMIT = "LIMIT"
ORDER_TYPE_STOP_MARKET = "STOP_MARKET"
ORDER_TYPE_MARKET = "MARKET"

# Band policies (spec §7: REJECT | IMMEDIATE_MARKET)
BAND_POLICY_REJECT = "reject"
BAND_POLICY_IMMEDIATE_MARKET = "immediate_market"

# Spec §8: "entry within a small band of current price (default ~5 ticks,
# configurable)".
DEFAULT_BAND_TICKS = 5

# Display recommendations for the Signals screen (analysis only - showing
# a recommendation executes nothing).
RECOMMEND_PLACE_LIMIT = "PLACE_LIMIT"
RECOMMEND_PLACE_STOP = "PLACE_STOP"
RECOMMEND_EXECUTE_MARKET = "EXECUTE_MARKET"
RECOMMEND_SIGNAL_EXPIRED = "SIGNAL_EXPIRED"
# "No action available right now": already executed, or the current price
# is unknown (never guessed - spec invariant 5).
RECOMMEND_WAIT = "WAIT"

_VALID_DIRECTIONS = ("LONG", "SHORT")
_VALID_POLICIES = (BAND_POLICY_REJECT, BAND_POLICY_IMMEDIATE_MARKET)


class EntryInsideBandError(ValueError):
    """Entry is within the execution band and the policy is 'reject'.

    Deliberately an error, not a silent MARKET fallback: converting a
    planned-entry order into an immediate market fill is exactly the
    surprise A5-lite exists to remove, so it may only happen when the
    operator explicitly configured 'immediate_market'."""

    def __init__(self, direction: str, entry_price: float, current_price: float, band: float):
        self.direction = direction
        self.entry_price = entry_price
        self.current_price = current_price
        self.band = band
        super().__init__(
            f"Entry {entry_price} is within {band} of the current price {current_price} "
            f"for this {direction} - a resting order here would fill immediately as a "
            f"de-facto market order. Use Execute Market if an immediate fill is what "
            f"you want, or set the band policy to 'immediate_market'."
        )


@dataclass(frozen=True)
class ResolvedEntry:
    """The matrix's answer. `order_type` is one of ORDER_TYPE_*; `reason`
    states the comparison that produced it, for journals/UI - every
    execution decision must be able to explain itself."""

    order_type: str
    direction: str
    entry_price: float
    current_price: float
    band: float
    reason: str


def _validate(direction: str, entry_price: float, current_price: float,
              tick_size: float, band_ticks: int, band_policy: str) -> None:
    if direction not in _VALID_DIRECTIONS:
        raise ValueError(f"direction must be LONG or SHORT, got {direction!r}")
    if not (entry_price > 0):
        raise ValueError(f"entry_price must be > 0, got {entry_price!r}")
    if not (current_price > 0):
        raise ValueError(f"current_price must be > 0, got {current_price!r}")
    if not (tick_size > 0):
        # A zero/missing tick size would silently produce a zero band and
        # defeat the immediate-fill guard - fail loudly instead (the same
        # "never PASS on missing filter data" lesson the validation
        # harness already learned).
        raise ValueError(f"tick_size must be > 0, got {tick_size!r}")
    if band_ticks < 0:
        raise ValueError(f"band_ticks must be >= 0, got {band_ticks!r}")
    if band_policy not in _VALID_POLICIES:
        raise ValueError(f"band_policy must be one of {_VALID_POLICIES}, got {band_policy!r}")


def resolve_entry_order_type(
    direction: str,
    entry_price: float,
    current_price: float,
    tick_size: float,
    band_ticks: int = DEFAULT_BAND_TICKS,
    band_policy: str = BAND_POLICY_REJECT,
) -> ResolvedEntry:
    """Apply the §7 matrix. Raises EntryInsideBandError under the 'reject'
    policy when the entry sits inside the band; raises ValueError on
    invalid inputs. Never returns a guess."""
    _validate(direction, entry_price, current_price, tick_size, band_ticks, band_policy)

    band = band_ticks * tick_size
    distance = entry_price - current_price

    if abs(distance) <= band:
        if band_policy == BAND_POLICY_IMMEDIATE_MARKET:
            return ResolvedEntry(
                order_type=ORDER_TYPE_MARKET,
                direction=direction, entry_price=entry_price,
                current_price=current_price, band=band,
                reason=(
                    f"entry {entry_price} within {band} of current {current_price}; "
                    f"policy=immediate_market -> MARKET"
                ),
            )
        raise EntryInsideBandError(direction, entry_price, current_price, band)

    if direction == "LONG":
        # Below market: buy the retracement -> resting LIMIT BUY.
        # Above market: buy the breakout -> STOP-MARKET BUY.
        order_type = ORDER_TYPE_LIMIT if distance < 0 else ORDER_TYPE_STOP_MARKET
    else:
        # SHORT mirror: above market -> LIMIT SELL; below -> STOP-MARKET SELL.
        order_type = ORDER_TYPE_LIMIT if distance > 0 else ORDER_TYPE_STOP_MARKET

    side_word = "below" if distance < 0 else "above"
    return ResolvedEntry(
        order_type=order_type,
        direction=direction, entry_price=entry_price,
        current_price=current_price, band=band,
        reason=(
            f"{direction} entry {entry_price} is {side_word} current {current_price} "
            f"(distance {distance:+.10g}, band {band:.10g}) -> {order_type}"
        ),
    )


def recommend_execution(
    direction: str,
    entry_price: float,
    current_price: float | None,
    tick_size: float | None,
    *,
    expired: bool = False,
    already_executed: bool = False,
    band_ticks: int = DEFAULT_BAND_TICKS,
) -> str:
    """Display-only recommendation for the Signals screen (spec: WAIT /
    PLACE LIMIT / PLACE STOP / EXECUTE MARKET / SIGNAL EXPIRED).

    Shows what *Place Signal Order* WOULD do right now. Executes nothing.
    WAIT means "no action is available right now": the signal already has
    an order/position, or the current price/tick size is unavailable - in
    which case nothing is invented (invariant 5)."""
    if expired:
        return RECOMMEND_SIGNAL_EXPIRED
    if already_executed:
        return RECOMMEND_WAIT
    if current_price is None or not current_price > 0 or tick_size is None or not tick_size > 0:
        return RECOMMEND_WAIT

    try:
        resolved = resolve_entry_order_type(
            direction, entry_price, current_price, tick_size,
            band_ticks=band_ticks, band_policy=BAND_POLICY_IMMEDIATE_MARKET,
        )
    except ValueError:
        return RECOMMEND_WAIT

    if resolved.order_type == ORDER_TYPE_MARKET:
        return RECOMMEND_EXECUTE_MARKET
    if resolved.order_type == ORDER_TYPE_LIMIT:
        return RECOMMEND_PLACE_LIMIT
    return RECOMMEND_PLACE_STOP
