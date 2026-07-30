"""Regression suite for app.risk.risk_engine."""
import pytest

from app.risk.risk_engine import RiskEngine, RiskLimits


@pytest.fixture(scope="module")
def engine():
    return RiskEngine()


class TestBasicApproval:
    def test_clean_trade_is_approved(self, engine):
        # entry/stop 10 apart (10% distance) keeps notional exposure sane;
        # a 2%-wide stop combined with 2% risk-per-trade implies 100%
        # notional exposure by construction and would trip the exposure
        # gate below, which is a sizing-math fact, not an engine bug.
        result = engine.assess_new_trade(
            account_balance=10000.0, entry_price=100.0, stop_loss=90.0, take_profit=112.0,
        )
        assert result.approved is True
        assert result.reasons == []
        assert result.position_size["risk_usd"] == 200.0  # 2% default
        assert result.position_size["profit_usd"] == 240.0

    def test_unknown_balance_is_not_approved(self, engine):
        result = engine.assess_new_trade(account_balance=None, entry_price=100.0, stop_loss=98.0)
        assert result.approved is False
        assert result.position_size is None
        assert any("unavailable" in r for r in result.reasons)

    def test_degenerate_stop_equal_entry_is_not_approved(self, engine):
        result = engine.assess_new_trade(account_balance=10000.0, entry_price=100.0, stop_loss=100.0)
        assert result.approved is False
        assert result.position_size is None


class TestOpenRiskAndExposureLimits:
    def test_open_risk_exceeded_blocks(self, engine):
        open_positions = [{"risk_usd": 550.0, "notional_usd": 1000.0}]
        result = engine.assess_new_trade(
            account_balance=10000.0, entry_price=100.0, stop_loss=98.0, open_positions=open_positions,
        )
        # existing 550 + new 200 = 750 -> 7.5% > default max 6%
        assert result.approved is False
        assert any("Open risk" in r for r in result.reasons)

    def test_leverage_exceeded_blocks(self, engine):
        # RISK ENGINE 2.0.0: the gross-notional "exposure" cap was replaced
        # by an explicitly-named LEVERAGE ceiling. `notional / equity` IS
        # leverage x 100, so the old 50% limit permitted at most 0.5x and
        # made max_open_risk_percent mathematically unreachable (proven in
        # EXPOSURE_MODEL_ANALYSIS.md). The control was not weakened - it was
        # renamed to what it measures and set to a coherent threshold, which
        # RiskLimits.assert_coherent() now enforces at startup.
        # 400% of a 10,000 equity = 40,000 notional, above the 300% ceiling.
        open_positions = [{"risk_usd": 0.0, "notional_usd": 38000.0}]
        result = engine.assess_new_trade(
            account_balance=10000.0, entry_price=100.0, stop_loss=90.0, open_positions=open_positions,
        )
        assert result.approved is False
        assert any("leverage" in r.lower() for r in result.reasons)

    def test_within_limits_approved(self, engine):
        open_positions = [{"risk_usd": 50.0, "notional_usd": 500.0}]
        result = engine.assess_new_trade(
            account_balance=10000.0, entry_price=100.0, stop_loss=90.0, open_positions=open_positions,
        )
        assert result.approved is True


class TestDailyLossLimit:
    def test_daily_loss_at_limit_blocks(self, engine):
        closed = [{"realized_pnl_usd": -500.0}]  # -5% == default max
        result = engine.assess_new_trade(
            account_balance=10000.0, entry_price=100.0, stop_loss=98.0, closed_trades_today=closed,
        )
        assert result.approved is False
        assert any("Daily loss" in r for r in result.reasons)

    def test_daily_profit_does_not_block(self, engine):
        closed = [{"realized_pnl_usd": 300.0}]
        result = engine.assess_new_trade(
            account_balance=10000.0, entry_price=100.0, stop_loss=90.0, closed_trades_today=closed,
        )
        assert result.approved is True
        assert result.daily_pnl_percent == 3.0


class TestDrawdown:
    def test_drawdown_at_limit_blocks(self, engine):
        result = engine.assess_new_trade(
            account_balance=8000.0, entry_price=100.0, stop_loss=98.0, equity_peak=10000.0,
        )
        # (10000-8000)/10000 = 20% == default max
        assert result.approved is False
        assert any("drawdown" in r for r in result.reasons)

    def test_no_peak_supplied_means_zero_drawdown(self, engine):
        result = engine.assess_new_trade(account_balance=8000.0, entry_price=100.0, stop_loss=98.0)
        assert result.current_drawdown_percent == 0.0


class TestCorrelationWarningIsAdvisoryOnly:
    def test_correlation_warning_present_but_does_not_block(self, engine):
        result = engine.assess_new_trade(
            account_balance=10000.0, entry_price=100.0, stop_loss=90.0,
            correlation_warning="High correlation with open BTCUSDT position (0.85).",
        )
        assert result.approved is True
        assert result.correlation_warning == "High correlation with open BTCUSDT position (0.85)."
        assert result.correlation_warning in result.reasons


class TestCustomLimits:
    def test_tighter_custom_limits_block_earlier(self):
        tight = RiskEngine(limits=RiskLimits(max_risk_percent_per_trade=1.0, max_open_risk_percent=1.0))
        result = tight.assess_new_trade(account_balance=10000.0, entry_price=100.0, stop_loss=98.0)
        # 1% of 10000 = 100 risk_usd -> 1.0% open risk, right at the tight limit (not exceeding)
        assert result.position_size["risk_usd"] == 100.0
