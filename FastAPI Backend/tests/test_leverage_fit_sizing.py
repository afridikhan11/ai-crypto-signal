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


class TestPerPositionNotionalCap:
    """The owner's rule: one position's notional never exceeds a set % of
    equity, so a tight structural stop can no longer balloon size (FIL ran
    ~2x equity on a ~0.5% stop) and stop-slippage overshoot stays bounded
    (COTI booked -1.4% of equity on a -0.77% move)."""

    def test_cap_is_percent_of_equity(self):
        from app.services.execution_risk import per_position_notional_cap
        assert per_position_notional_cap(4600.0, 50.0) == 2300.0

    def test_disabled_or_unknown_equity_gives_none(self):
        from app.services.execution_risk import per_position_notional_cap
        assert per_position_notional_cap(4600.0, 0.0) is None
        assert per_position_notional_cap(None, 50.0) is None
        assert per_position_notional_cap(0.0, 50.0) is None

    def test_combined_takes_the_tightest_cap(self):
        from app.services.execution_risk import combined_notional_cap
        assert combined_notional_cap(13_000.0, 2_300.0) == 2_300.0
        assert combined_notional_cap(None, 2_300.0) == 2_300.0
        assert combined_notional_cap(13_000.0, None) == 13_000.0
        assert combined_notional_cap(None, None) is None

    def test_fil_scenario_is_bounded(self):
        # FIL replay: equity 4600, entry 0.7658, stop ~0.5% away. Uncapped,
        # 1% risk sizes ~12,400 FIL (~9,500 notional, ~2x equity). With the
        # 50% cap the position stops at 2,300 notional (~0.5x) and the risk
        # actually taken drops below 1% - the slippage overshoot shrinks with it.
        r = calculate_position_size(4600, 0.7658, 0.762, 1.0, max_notional=2300.0)
        assert r["capped_to_max_notional"] is True
        assert abs(r["notional_usd"] - 2300.0) < 1e-6
        assert r["risk_usd"] < 46.0   # risking less than the full 1%

    def test_equity_resolution_falls_back_to_db_balance(self):
        from app.services.execution_risk import _equity_for_caps
        ctx = SimpleNamespace(account_balance=4600.0)
        assert _equity_for_caps(None, ctx) == 4600.0
        fut = SimpleNamespace(margin_balance=5000.0, wallet_balance=None)
        assert _equity_for_caps(fut, ctx) == 5000.0

    def test_config_default_is_50_pct(self):
        from app.core.config import get_settings
        assert get_settings().max_position_notional_pct == 50.0
