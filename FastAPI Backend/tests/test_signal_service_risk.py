"""
Regression suite for the Unified Risk Engine wiring in
app.services.signal_service - specifically `_signal_to_response()`'s
portfolio-aware risk assessment and its fallback behaviour.

Only the pure mapping path is exercised here; the async DB-backed
`_build_risk_context()` needs a live session and is covered by the
integration tests that run against a real database. `SignalService` is
constructed with a `None` session, which is safe because none of the code
paths under test touch it.
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.models.signal import Direction, SignalStatus
from app.services.signal_service import PortfolioRiskContext, SignalService


NOW = datetime(2026, 1, 5, 12, 0, 0, tzinfo=timezone.utc)


class _FakeCoin:
    def __init__(self, symbol="BTCUSDT"):
        self.symbol = symbol


class _FakeSignal:
    def __init__(self, entry_price=100.0, stop_loss=90.0, take_profit=130.0,
                 initial_stop_loss=None):
        self.id = uuid.uuid4()
        self.coin_id = uuid.uuid4()
        self.coin = _FakeCoin()
        self.direction = Direction.LONG
        self.entry_price = entry_price
        self.stop_loss = stop_loss
        # Defaults to stop_loss, mirroring how a real Signal is created.
        # Passing them separately lets a test simulate a trade whose live
        # stop has already been trailed away from the original.
        self.initial_stop_loss = stop_loss if initial_stop_loss is None else initial_stop_loss
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


@pytest.fixture(scope="module")
def service():
    return SignalService(None)


class TestPortfolioRiskContext:
    def test_unavailable_without_balance(self):
        assert PortfolioRiskContext(account_balance=None, risk_percent=1.0).is_available is False

    def test_unavailable_without_risk_percent(self):
        assert PortfolioRiskContext(account_balance=10000.0, risk_percent=None).is_available is False

    def test_available_with_both(self):
        assert PortfolioRiskContext(account_balance=10000.0, risk_percent=1.0).is_available is True

    def test_defaults_are_empty_not_none(self):
        ctx = PortfolioRiskContext()
        assert ctx.open_positions == ()
        assert ctx.closed_trades_today == ()


class TestRiskEngineWiring:
    def test_clean_trade_is_approved_with_portfolio_fields(self, service):
        ctx = PortfolioRiskContext(account_balance=10000.0, risk_percent=1.0)
        resp = service._signal_to_response(_FakeSignal(), 10000.0, 1.0, ctx)
        assert resp.risk_approved is True
        assert resp.risk_reasons == []
        assert resp.suggested_risk_usd == 100.0
        assert resp.portfolio_open_risk_percent == 1.0
        assert resp.portfolio_exposure_percent is not None

    def test_open_risk_breach_is_reported_not_hidden(self, service):
        # Six existing positions at 1% each + this one = 7% > the 6% default.
        ctx = PortfolioRiskContext(
            account_balance=10000.0, risk_percent=1.0,
            open_positions=tuple({"risk_usd": 100.0, "notional_usd": 500.0} for _ in range(6)),
        )
        resp = service._signal_to_response(_FakeSignal(), 10000.0, 1.0, ctx)
        assert resp.risk_approved is False
        assert any("Open risk" in r for r in resp.risk_reasons)
        # Sizing is still returned - the user needs to see what they'd be
        # taking, alongside the warning that it breaches a limit.
        assert resp.suggested_risk_usd == 100.0

    def test_daily_loss_breach_is_reported(self, service):
        ctx = PortfolioRiskContext(
            account_balance=10000.0, risk_percent=1.0,
            closed_trades_today=({"realized_pnl_usd": -600.0},),
        )
        resp = service._signal_to_response(_FakeSignal(), 10000.0, 1.0, ctx)
        assert resp.risk_approved is False
        assert any("Daily loss" in r for r in resp.risk_reasons)

    def test_drawdown_breach_is_reported(self, service):
        ctx = PortfolioRiskContext(
            account_balance=8000.0, risk_percent=1.0, equity_peak=10000.0,
        )
        resp = service._signal_to_response(_FakeSignal(), 8000.0, 1.0, ctx)
        assert resp.risk_approved is False
        assert any("drawdown" in r for r in resp.risk_reasons)

    def test_leverage_breach_is_reported(self, service):
        # RISK ENGINE 2.0.0 - see tests/test_risk_engine.py's
        # test_leverage_exceeded_blocks for why the gross-notional
        # "exposure" cap became an explicit leverage ceiling.
        ctx = PortfolioRiskContext(
            account_balance=10000.0, risk_percent=1.0,
            open_positions=({"risk_usd": 0.0, "notional_usd": 38000.0},),
        )
        resp = service._signal_to_response(_FakeSignal(), 10000.0, 1.0, ctx)
        assert resp.risk_approved is False
        assert any("leverage" in r.lower() for r in resp.risk_reasons)


class TestFallbackWithoutContext:
    def test_no_context_still_sizes_the_position(self, service):
        resp = service._signal_to_response(_FakeSignal(), 10000.0, 1.0, None)
        assert resp.suggested_risk_usd == 100.0
        assert resp.risk_approved is None
        assert resp.risk_reasons == []

    def test_sizing_identical_with_and_without_context(self, service):
        """RiskEngine reuses calculate_position_size internally, so adding
        portfolio awareness must not change the suggested numbers."""
        ctx = PortfolioRiskContext(account_balance=10000.0, risk_percent=1.0)
        with_ctx = service._signal_to_response(_FakeSignal(), 10000.0, 1.0, ctx)
        without_ctx = service._signal_to_response(_FakeSignal(), 10000.0, 1.0, None)
        assert with_ctx.suggested_risk_usd == without_ctx.suggested_risk_usd
        assert with_ctx.suggested_quantity == without_ctx.suggested_quantity
        assert with_ctx.suggested_notional_usd == without_ctx.suggested_notional_usd

    def test_no_balance_yields_no_sizing_and_no_fabricated_zero(self, service):
        resp = service._signal_to_response(_FakeSignal(), None, None, None)
        assert resp.suggested_risk_usd is None
        assert resp.suggested_quantity is None
        assert resp.risk_approved is None

    def test_unavailable_context_falls_back_cleanly(self, service):
        ctx = PortfolioRiskContext(account_balance=None, risk_percent=1.0)
        resp = service._signal_to_response(_FakeSignal(), None, 1.0, ctx)
        assert resp.risk_approved is None
        assert resp.suggested_risk_usd is None


class TestResponseIntegrity:
    def test_core_signal_fields_still_mapped(self, service):
        signal = _FakeSignal()
        resp = service._signal_to_response(signal, 10000.0, 1.0, None)
        assert resp.coin_symbol == "BTCUSDT"
        assert resp.direction == "LONG"
        assert resp.entry_price == 100.0
        assert resp.stop_loss == 90.0
        assert resp.take_profit == 130.0
        assert resp.status == "ACTIVE"
        assert resp.confidence == 90

    def test_missing_coin_degrades_to_unknown(self, service):
        signal = _FakeSignal()
        signal.coin = None
        assert service._signal_to_response(signal, None, None, None).coin_symbol == "UNKNOWN"
