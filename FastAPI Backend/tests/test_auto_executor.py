"""
Tests for the testnet auto-executor's SAFETY gate.

The one thing that absolutely must hold: it can NEVER place a mainnet order.
execution_allowed() is the single decision that guarantees it, so it is tested
exhaustively here.
"""
from app.scheduler.auto_executor import execution_allowed


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
