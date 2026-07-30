"""
Live Execution Safety, Objective 1/6 - tests for `app.services.execution_risk`,
the mandatory Risk Engine gate every real-order execution passes through.

Deliberately does NOT import the FastAPI endpoint layer
(app/api/v1/endpoints/trading.py) - this project's existing test suite has
never imported FastAPI itself (see tests/test_signal_service_risk.py's own
docstring), and `execution_risk.py` was specifically factored out to be
framework-independent so the actual risk DECISION could be unit-tested
directly. `SignalService.build_portfolio_risk_context` (the one async,
DB-backed call) is monkeypatched to return a hand-built
`PortfolioRiskContext` - exactly the same context shape `RiskEngine` is
already regression-tested against in tests/test_risk_engine.py and
tests/test_signal_service_risk.py, so this file focuses on proving the
EXECUTION gate wires that context through correctly, not re-deriving
RiskEngine's own math.
"""
import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.models.signal import Direction, SignalStatus
from app.services.execution_risk import TradeRiskRejected, assess_execution_risk
from app.services.signal_service import PortfolioRiskContext, SignalService

NOW = datetime(2026, 1, 5, 12, 0, 0, tzinfo=timezone.utc)


class _FakeCoin:
    def __init__(self, symbol="BTCUSDT"):
        self.symbol = symbol


class _FakeSignal:
    """Complete enough to satisfy both `assess_execution_risk` (needs
    coin/direction/entry_price/stop_loss/take_profit) and
    `SignalService._signal_to_response` (needs every field below) - the
    same fake is used against both code paths in
    TestDisplayAndExecutionNeverDisagree to prove they see identical data."""

    def __init__(self, entry_price=100.0, stop_loss=95.0, take_profit=115.0,
                 initial_stop_loss=None):
        self.id = uuid.uuid4()
        self.coin_id = uuid.uuid4()
        self.coin = _FakeCoin()
        self.direction = Direction.LONG
        self.entry_price = entry_price
        # `initial_stop_loss` is what every risk/sizing read uses; it
        # defaults to stop_loss exactly as a real Signal is created. Passing
        # them separately simulates a trade whose live stop has already been
        # trailed (e.g. to breakeven) while the risk reference stays fixed.
        self.initial_stop_loss = stop_loss if initial_stop_loss is None else initial_stop_loss
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.risk_reward = 3.0
        self.confidence = 90
        self.reason = "test"
        self.status = SignalStatus.ACTIVE
        self.timeframe = "15m"
        self.ai_model_version = "1.0.0"
        self.created_at = NOW
        self.updated_at = NOW
        self.closed_at = None
        self.executed = False
        self.executed_order_id = None
        self.executed_at = None
        self.executed_environment = None


def _run(coro):
    return asyncio.run(coro)


def _with_context(ctx):
    """Patches SignalService.build_portfolio_risk_context to return `ctx`
    for the duration of the `with` block."""
    return patch.object(SignalService, "build_portfolio_risk_context", AsyncMock(return_value=ctx))


class TestRiskApproved:
    def test_clean_trade_is_approved_and_returns_position_size(self):
        ctx = PortfolioRiskContext(account_balance=10000.0, risk_percent=1.0)
        with _with_context(ctx):
            assessment = _run(assess_execution_risk(SignalService(None), _FakeSignal()))
        assert assessment.approved is True
        assert assessment.position_size is not None
        assert assessment.position_size["risk_usd"] == 100.0

    def test_approved_does_not_raise(self):
        ctx = PortfolioRiskContext(account_balance=10000.0, risk_percent=1.0)
        with _with_context(ctx):
            # Must not raise TradeRiskRejected.
            _run(assess_execution_risk(SignalService(None), _FakeSignal()))


class TestRiskRejected:
    def test_leverage_exceeded_blocks_with_reason(self):
        # RISK ENGINE 2.0.0: the gross-notional "exposure" cap became an
        # explicitly-named LEVERAGE ceiling (300%), because notional/equity
        # IS leverage x 100 and the old 50% cap permitted at most 0.5x while
        # making max_open_risk_percent unreachable - see
        # EXPOSURE_MODEL_ANALYSIS.md. `risk_usd=0` on the existing positions
        # keeps the open-risk gate from firing so this isolates leverage.
        # 4 x 8000 = 32,000 notional on 10,000 equity = 320% > 300%.
        ctx = PortfolioRiskContext(
            account_balance=10000.0, risk_percent=1.0,
            open_positions=tuple({"risk_usd": 0.0, "notional_usd": 8000.0} for _ in range(4)),
        )
        with pytest.raises(TradeRiskRejected) as exc_info:
            with _with_context(ctx):
                _run(assess_execution_risk(SignalService(None), _FakeSignal()))
        assert any("leverage" in r.lower() for r in exc_info.value.reasons)
        assert exc_info.value.extra["exposure_percent"] is not None

    def test_open_risk_exceeded_blocks_with_reason(self):
        # Six existing 1%-risk positions + this one = 7% > default 6% cap.
        ctx = PortfolioRiskContext(
            account_balance=10000.0, risk_percent=1.0,
            open_positions=tuple({"risk_usd": 100.0, "notional_usd": 500.0} for _ in range(6)),
        )
        with pytest.raises(TradeRiskRejected) as exc_info:
            with _with_context(ctx):
                _run(assess_execution_risk(SignalService(None), _FakeSignal()))
        assert any("Open risk" in r for r in exc_info.value.reasons)

    def test_daily_loss_exceeded_blocks_with_reason(self):
        # -600 on a 10000 balance = -6% > default 5% max daily loss.
        ctx = PortfolioRiskContext(
            account_balance=10000.0, risk_percent=1.0,
            closed_trades_today=({"realized_pnl_usd": -600.0},),
        )
        with pytest.raises(TradeRiskRejected) as exc_info:
            with _with_context(ctx):
                _run(assess_execution_risk(SignalService(None), _FakeSignal()))
        assert any("Daily loss" in r for r in exc_info.value.reasons)

    def test_drawdown_exceeded_blocks_with_reason(self):
        # equity_peak 13000, balance 10000 -> ~23% drawdown > default 20%.
        ctx = PortfolioRiskContext(account_balance=10000.0, risk_percent=1.0, equity_peak=13000.0)
        with pytest.raises(TradeRiskRejected) as exc_info:
            with _with_context(ctx):
                _run(assess_execution_risk(SignalService(None), _FakeSignal()))
        assert any("drawdown" in r.lower() for r in exc_info.value.reasons)

    def test_rejection_carries_every_portfolio_field(self):
        ctx = PortfolioRiskContext(
            account_balance=10000.0, risk_percent=1.0,
            closed_trades_today=({"realized_pnl_usd": -600.0},),
        )
        with pytest.raises(TradeRiskRejected) as exc_info:
            with _with_context(ctx):
                _run(assess_execution_risk(SignalService(None), _FakeSignal()))
        for key in ("open_risk_percent", "exposure_percent", "daily_pnl_percent", "current_drawdown_percent"):
            assert key in exc_info.value.extra

    def test_rejection_raises_before_any_return_value_exists(self):
        """The whole point of Objective 1: a caller literally cannot reach
        a position size / proceed to order placement on a rejected trade -
        `assess_execution_risk` raises instead of returning a
        `not-approved` assessment, so there is no `if assessment.approved`
        branch a caller could get wrong or skip."""
        ctx = PortfolioRiskContext(
            account_balance=10000.0, risk_percent=1.0,
            closed_trades_today=({"realized_pnl_usd": -600.0},),
        )
        reached_after_call = {"value": False}
        with pytest.raises(TradeRiskRejected):
            with _with_context(ctx):
                _run(assess_execution_risk(SignalService(None), _FakeSignal()))
            reached_after_call["value"] = True  # must never execute
        assert reached_after_call["value"] is False


class TestUnknownAccountBalance:
    def test_unavailable_context_blocks_execution(self):
        ctx = PortfolioRiskContext(account_balance=None, risk_percent=None)
        with pytest.raises(TradeRiskRejected) as exc_info:
            with _with_context(ctx):
                _run(assess_execution_risk(SignalService(None), _FakeSignal()))
        assert "no linked account balance" in exc_info.value.message.lower()

    def test_missing_risk_percent_alone_also_blocks(self):
        ctx = PortfolioRiskContext(account_balance=10000.0, risk_percent=None)
        with pytest.raises(TradeRiskRejected):
            with _with_context(ctx):
                _run(assess_execution_risk(SignalService(None), _FakeSignal()))


class TestDisplayAndExecutionNeverDisagree:
    """The core Objective 1 guarantee: the same PortfolioRiskContext produces
    the same approve/reject verdict whether it's read for display
    (SignalService._signal_to_response) or for execution
    (assess_execution_risk) - because both call the same RiskEngine with
    the same inputs."""

    def test_same_context_same_verdict_as_signal_to_response(self):
        ctx = PortfolioRiskContext(
            account_balance=10000.0, risk_percent=1.0,
            open_positions=tuple({"risk_usd": 100.0, "notional_usd": 500.0} for _ in range(6)),
        )
        signal = _FakeSignal()

        # Display path (existing, untouched).
        service = SignalService(None)
        display_resp = service._signal_to_response(signal, 10000.0, 1.0, ctx)

        # Execution path (new).
        with pytest.raises(TradeRiskRejected) as exc_info:
            with _with_context(ctx):
                _run(assess_execution_risk(service, signal))

        assert display_resp.risk_approved is False
        assert display_resp.risk_reasons == exc_info.value.reasons
