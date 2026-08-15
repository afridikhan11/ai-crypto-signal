"""
Leverage-fit position sizing.

A tight stop under a fixed risk % sizes a position so large it breaches the
effective-leverage ceiling and the trade is rejected outright. These tests
cover the cap that shrinks such a trade to the exposure headroom instead, so
it is taken at a smaller size (risking LESS than the requested %), and the
headroom helper that feeds the cap.
"""
from types import SimpleNamespace

from app.services.execution_risk import _leverage_headroom_notional
from app.services.position_sizing import calculate_position_size


class TestCalculatePositionSizeCap:
    def test_uncapped_when_notional_below_max(self):
        # Normal stop: notional well under the cap -> unchanged, not capped.
        r = calculate_position_size(1000, 100, 95, 1.0, max_notional=10_000)
        assert r["risk_usd"] == 10.0
        assert abs(r["quantity"] - 2.0) < 1e-9
        assert r["notional_usd"] == 200.0
        assert r["capped_to_max_notional"] is False

    def test_tight_stop_is_capped_to_max_notional(self):
        # entry 100, stop 99.9 -> 0.1 stop distance. 1% of 1000 = $10 risk ->
        # qty 100 -> $10,000 notional (10x). Cap at $3,000 (3x).
        r = calculate_position_size(1000, 100, 99.9, 1.0, max_notional=3000)
        assert r["capped_to_max_notional"] is True
        assert r["notional_usd"] == 3000.0
        assert abs(r["quantity"] - 30.0) < 1e-9
        # Same 0.1 stop distance on 30 units = $3 real risk (< the $10 asked).
        assert abs(r["risk_usd"] - 3.0) < 1e-6
        assert r["risk_usd"] < 10.0
        assert abs(r["risk_percent"] - 0.3) < 1e-6

    def test_zero_headroom_returns_none(self):
        assert calculate_position_size(1000, 100, 99.9, 1.0, max_notional=0) is None
        assert calculate_position_size(1000, 100, 99.9, 1.0, max_notional=-50) is None

    def test_profit_uses_the_capped_quantity(self):
        r = calculate_position_size(1000, 100, 99.9, 1.0, take_profit=103, max_notional=3000)
        # 30 units * $3 move to TP = $90.
        assert abs(r["profit_usd"] - 90.0) < 1e-6


class TestLeverageHeadroom:
    def _futures(self, margin_balance, positions):
        return SimpleNamespace(
            margin_balance=margin_balance,
            wallet_balance=margin_balance,
            open_positions=[
                SimpleNamespace(position_amt=amt, mark_price=price, entry_price=price)
                for amt, price in positions
            ],
        )

    def test_none_when_no_exchange_snapshot(self):
        # No futures -> leverage metric is UNKNOWN, so no cap (legacy behavior).
        rc = SimpleNamespace(account_balance=1000)
        assert _leverage_headroom_notional(None, rc) is None

    def test_headroom_is_ceiling_minus_existing_notional(self):
        # equity 1000, one open position 5 @ 100 = $500 notional.
        # headroom = 0.98 * 3.0 * 1000 - 500 = 2940 - 500 = 2440.
        futures = self._futures(1000, [(5, 100)])
        rc = SimpleNamespace(account_balance=1000)
        headroom = _leverage_headroom_notional(futures, rc)
        assert abs(headroom - 2440.0) < 1e-6

    def test_headroom_never_negative_when_over_leveraged(self):
        # Existing notional already past the ceiling -> no room, never negative.
        futures = self._futures(1000, [(100, 100)])  # $10,000 notional on $1,000
        rc = SimpleNamespace(account_balance=1000)
        assert _leverage_headroom_notional(futures, rc) == 0.0

    def test_short_position_notional_is_absolute(self):
        # A short reports a negative position_amt; exposure is direction-agnostic.
        futures = self._futures(1000, [(-5, 100)])
        rc = SimpleNamespace(account_balance=1000)
        headroom = _leverage_headroom_notional(futures, rc)
        assert abs(headroom - 2440.0) < 1e-6
