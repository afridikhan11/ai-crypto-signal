"""
Position sizing.

The signal generator only produces price levels (entry/SL/TP) - it has no
concept of dollar amounts or account size, so the same signal looks
identical whether the account has $100 or $100,000 in it. This module
bridges that gap: given a real account balance and a configured risk
percentage, it works out how much of the account to risk in dollars and
how large a position (in base-asset quantity) that translates to for a
given entry/stop-loss pair.

Deliberately kept separate from SignalGenerator - account balance changes
constantly and belongs to a specific trader, not to the (symbol-agnostic)
signal itself - so this is computed on demand at the API layer instead of
being baked into a stored Signal row.
"""
from __future__ import annotations

from typing import Dict, Optional


def calculate_position_size(
    account_balance: Optional[float],
    entry_price: float,
    stop_loss: float,
    risk_percent: float,
    take_profit: Optional[float] = None,
) -> Optional[Dict[str, float]]:
    """
    Returns None if a position size can't be suggested (no known account
    balance, or a degenerate entry/stop pair) - callers should treat that
    as "unknown", not silently show a zero.

    If `take_profit` is given, also returns the dollar profit that quantity
    would realize there - NOT an extra risk, just the same stop-sized
    position priced out at the target instead of at the stop.
    """
    if account_balance is None or account_balance <= 0:
        return None

    price_risk_per_unit = abs(entry_price - stop_loss)
    if price_risk_per_unit <= 0:
        return None

    risk_usd = round(account_balance * (risk_percent / 100.0), 2)
    quantity = risk_usd / price_risk_per_unit
    notional_usd = round(quantity * entry_price, 2)

    result = {
        "risk_usd": risk_usd,
        # Same number as risk_usd - the position is sized so that hitting
        # the stop loses exactly risk_usd, by construction. Exposed as its
        # own field (rather than making callers infer "loss = risk") so the
        # API/UI can show profit and loss side by side symmetrically.
        "loss_sl_usd": risk_usd,
        "quantity": round(quantity, 8),
        "notional_usd": notional_usd,
        "risk_percent": risk_percent,
    }

    if take_profit is not None:
        result["profit_usd"] = round(quantity * abs(take_profit - entry_price), 2)

    return result
