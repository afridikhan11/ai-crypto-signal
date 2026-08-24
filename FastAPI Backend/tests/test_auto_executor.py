"""
Tests for the testnet auto-executor's SAFETY gate.

The one thing that absolutely must hold: it can NEVER place a mainnet order.
execution_allowed() is the single decision that guarantees it, so it is tested
exhaustively here.
"""
from datetime import datetime, timezone

from app.core.config import get_settings
from app.scheduler.auto_executor import (
    daily_cap_reached,
    executed_last_24h_stmt,
    execution_allowed,
    live_executed_stmt,
    open_positions_cap_reached,
    pending_execution_stmt,
)


class TestExecutionAllowed:
    def test_testnet_creds_and_auto_on_is_allowed(self):
        assert execution_allowed({"testnet": True}, True) is True

    def test_mainnet_creds_never_allowed(self):
        # The critical guarantee: real-money creds are refused, even with the
        # master switch on.
        assert execution_allowed({"testnet": False}, True) is False

    def test_missing_creds_not_allowed(self):
        assert execution_allowed(None, True) is False
        assert execution_allowed({}, True) is False

    def test_testnet_but_auto_trading_off_not_allowed(self):
        assert execution_allowed({"testnet": True}, False) is False

    def test_testnet_flag_must_be_truthy(self):
        assert execution_allowed({"testnet": 0}, True) is False
        assert execution_allowed({"testnet": None}, True) is False


class TestObserveOnlyGuard:
    """The executor must place orders ONLY for legacy-pipeline signals, never
    for Smart AI signals (observe-only until execution is explicitly opted in)."""

    def _sql(self):
        return str(pending_execution_stmt().compile(compile_kwargs={"literal_binds": True}))

    def test_query_only_selects_legacy_or_null_strategy(self):
        sql = self._sql()
        # Legacy rows: strategy_id IS NULL (fresh) OR = 'legacy' (backfilled).
        assert "strategy_id IS NULL" in sql
        assert "strategy_id = 'legacy'" in sql

    def test_query_still_filters_unexecuted_non_terminal(self):
        sql = self._sql()
        assert "executed" in sql and "status" in sql


class TestDailyTradeCap:
    """The owner's rule: at most N trades per rolling 24h, LONGs and SHORTs
    counted together. A placement cap - signals past it stay paper-only."""

    def test_reached_at_cap(self):
        assert daily_cap_reached(3, cap=3) is True
        assert daily_cap_reached(4, cap=3) is True

    def test_below_cap_allows(self):
        assert daily_cap_reached(2, cap=3) is False

    def test_zero_disables(self):
        assert daily_cap_reached(100, cap=0) is False

    def test_count_query_is_direction_blind_and_24h_bounded(self):
        sql = str(
            executed_last_24h_stmt(datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)).compile(
                compile_kwargs={"literal_binds": True}
            )
        )
        # Counts EXECUTED trades in the window - and deliberately does NOT
        # filter by direction (long + short share the one daily allowance).
        assert "executed" in sql
        assert "executed_at" in sql
        assert "direction" not in sql

    def test_config_default_is_3(self):
        assert get_settings().max_trades_per_day == 3


class TestOpenPositionsCap:
    """Closes the 24h-cap leak the owner spotted: 3 trades placed yesterday
    that are still open roll out of the rolling window, and 3 more would have
    stacked on top. Open trades must consume today's allowance."""

    def test_reached_at_cap(self):
        assert open_positions_cap_reached(3, cap=3) is True
        assert open_positions_cap_reached(5, cap=3) is True

    def test_below_cap_allows(self):
        assert open_positions_cap_reached(2, cap=3) is False

    def test_zero_disables(self):
        assert open_positions_cap_reached(100, cap=0) is False

    def test_live_query_counts_executed_non_terminal_any_direction(self):
        sql = str(live_executed_stmt().compile(compile_kwargs={"literal_binds": True}))
        # Live = executor-placed AND not terminal (resting order or open
        # position); direction-blind, like the daily cap.
        assert "executed" in sql
        assert "status" in sql
        assert "direction" not in sql

    def test_config_default_is_3(self):
        assert get_settings().max_open_positions == 3
