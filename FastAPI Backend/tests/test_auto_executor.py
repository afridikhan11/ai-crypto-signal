"""
Tests for the testnet auto-executor's SAFETY gate.

The one thing that absolutely must hold: it can NEVER place a mainnet order.
execution_allowed() is the single decision that guarantees it, so it is tested
exhaustively here.
"""
from app.scheduler.auto_executor import execution_allowed, pending_execution_stmt


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
