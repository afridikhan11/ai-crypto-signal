"""
Phase 3 - real Binance Futures data wired into RiskContext.

Proves the three metrics that previously had no data (Margin Usage,
Effective Leverage, Liquidation Ratio) now evaluate on real exchange
facts, that equity is `margin_balance` rather than `wallet_balance`, and
that missing inputs still yield UNKNOWN rather than a fabricated zero.
"""
from types import SimpleNamespace

import pytest

from app.risk.context_builder import build_risk_context, candidate_from_position_size
from app.risk.limits import COHERENCE_REFERENCE_STOP_FRACTION, RiskLimits
from app.risk.risk_engine import RiskEngine
from app.risk.result import MetricStatus


def _position(symbol="BTCUSDT", amt=0.1, entry=98000.0, mark=99000.0,
              leverage=20, margin=495.0):
    return SimpleNamespace(
        symbol=symbol, position_amt=amt, entry_price=entry, mark_price=mark,
        leverage=leverage, margin=margin, unrealized_pnl=0.0,
        liquidation_price=None, margin_type="cross", position_side="BOTH",
    )


def _futures(wallet=4694.0, margin_balance=4800.0, maintenance=40.0, positions=None):
    return SimpleNamespace(
        wallet_balance=wallet, available_balance=wallet - 500,
        margin_balance=margin_balance, unrealized_pnl=margin_balance - wallet,
        total_maintenance_margin=maintenance,
        open_positions=list(positions if positions is not None else [_position()]),
    )


class TestEquityDenominator:
    def test_equity_is_margin_balance_not_wallet_balance(self):
        ctx = build_risk_context(_futures(wallet=4694.0, margin_balance=4800.0),
                                 risk_percent=1.0)
        assert ctx.equity == 4800.0
        assert ctx.wallet_balance == 4694.0

    def test_wallet_balance_is_still_retained_for_display(self):
        ctx = build_risk_context(_futures(), risk_percent=1.0)
        assert ctx.wallet_balance is not None
        assert ctx.unrealized_pnl is not None


class TestPositionMapping:
    def test_notional_uses_mark_price_not_entry_price(self):
        # Margin and liquidation are computed against MARK price.
        ctx = build_risk_context(
            _futures(positions=[_position(amt=0.1, entry=98000.0, mark=99000.0)]),
            risk_percent=1.0,
        )
        assert ctx.open_positions[0].notional_usd == pytest.approx(9900.0)

    def test_short_position_contributes_positive_notional(self):
        ctx = build_risk_context(
            _futures(positions=[_position(amt=-0.1, mark=99000.0)]), risk_percent=1.0,
        )
        assert ctx.open_positions[0].notional_usd == pytest.approx(9900.0)

    def test_real_binance_margin_is_used_verbatim(self):
        ctx = build_risk_context(
            _futures(positions=[_position(margin=495.0, leverage=20)]), risk_percent=1.0,
        )
        assert ctx.open_positions[0].initial_margin_usd == 495.0

    def test_margin_is_derived_from_leverage_only_when_binance_omits_it(self):
        ctx = build_risk_context(
            _futures(positions=[_position(amt=0.1, mark=99000.0, margin=None, leverage=10)]),
            risk_percent=1.0,
        )
        assert ctx.open_positions[0].initial_margin_usd == pytest.approx(990.0)

    def test_margin_stays_unknown_when_neither_margin_nor_leverage_is_known(self):
        ctx = build_risk_context(
            _futures(positions=[_position(margin=None, leverage=None)]), risk_percent=1.0,
        )
        assert ctx.open_positions[0].initial_margin_usd is None

    def test_zero_size_positions_are_excluded(self):
        ctx = build_risk_context(
            _futures(positions=[_position(amt=0.0), _position(symbol="ETHUSDT", amt=1.0)]),
            risk_percent=1.0,
        )
        assert [p.symbol for p in ctx.open_positions] == ["ETHUSDT"]

    def test_risk_comes_from_our_signals_not_from_binance(self):
        ctx = build_risk_context(
            _futures(positions=[_position(symbol="BTCUSDT")]),
            risk_percent=1.0, signal_risk_by_symbol={"BTCUSDT": 46.94},
        )
        assert ctx.open_positions[0].risk_usd == 46.94

    def test_externally_opened_position_has_unknown_risk_not_zero(self):
        # Binance knows the size; only WE know the stop. A position we never
        # created must not read as risk-free.
        ctx = build_risk_context(
            _futures(positions=[_position(symbol="SOLUSDT")]),
            risk_percent=1.0, signal_risk_by_symbol={"BTCUSDT": 46.94},
        )
        assert ctx.open_positions[0].risk_usd is None


class TestMetricsNowHaveRealData:
    def test_margin_usage_evaluates_instead_of_unknown(self):
        ctx = build_risk_context(
            _futures(margin_balance=4800.0, positions=[_position(margin=495.0)]),
            risk_percent=1.0,
        )
        m = RiskEngine().assess(ctx).metric("margin_usage")
        assert m.status is MetricStatus.PASS
        assert m.value == pytest.approx(495.0 / 4800.0 * 100, abs=0.01)

    def test_effective_leverage_evaluates_instead_of_unknown(self):
        ctx = build_risk_context(
            _futures(margin_balance=4800.0,
                     positions=[_position(amt=0.1, mark=99000.0)]),
            risk_percent=1.0,
        )
        m = RiskEngine().assess(ctx).metric("effective_leverage")
        assert m.status is MetricStatus.PASS
        assert m.value == pytest.approx(9900.0 / 4800.0 * 100, abs=0.01)
        assert m.extra["leverage_x"] == pytest.approx(2.06, abs=0.01)

    def test_liquidation_ratio_evaluates_instead_of_unknown(self):
        ctx = build_risk_context(
            _futures(margin_balance=4800.0, maintenance=40.0), risk_percent=1.0,
        )
        m = RiskEngine().assess(ctx).metric("margin_ratio")
        assert m.status is MetricStatus.PASS
        assert m.value == pytest.approx(40.0 / 4800.0 * 100, abs=0.01)

    def test_liquidation_ratio_blocks_when_close_to_liquidation(self):
        ctx = build_risk_context(
            _futures(margin_balance=1000.0, maintenance=800.0), risk_percent=1.0,
        )
        m = RiskEngine().assess(ctx).metric("margin_ratio")
        assert m.status is MetricStatus.BLOCK
        assert m.value == pytest.approx(80.0)

    def test_leverage_blocks_above_the_ceiling(self):
        ctx = build_risk_context(
            _futures(margin_balance=1000.0,
                     positions=[_position(amt=1.0, mark=4000.0, margin=200.0)]),
            risk_percent=1.0,
        )
        a = RiskEngine().assess(ctx)
        assert a.approved is False
        assert any("leverage" in r.lower() for r in a.reasons)


class TestUnknownIsPreserved:
    def test_no_linked_account_leaves_margin_metrics_unknown(self):
        ctx = build_risk_context(None, risk_percent=1.0, fallback_equity=4694.0)
        a = RiskEngine().assess(ctx)
        for name in ("margin_usage", "margin_ratio"):
            assert a.metric(name).status is MetricStatus.UNKNOWN
        # ...and equity still falls back so open_risk remains measurable.
        assert ctx.equity == 4694.0

    def test_unknown_never_appears_in_reasons_but_is_never_dropped(self):
        ctx = build_risk_context(None, risk_percent=1.0, fallback_equity=4694.0)
        a = RiskEngine().assess(ctx)
        assert not any("NOT EVALUATED" in r for r in a.reasons)
        assert any("margin_ratio" in n for n in a.unknown_notices)
        assert "margin_ratio" in a.metric_snapshot()["unknown"]

    def test_position_with_unknown_risk_makes_open_risk_unknown(self):
        ctx = build_risk_context(
            _futures(positions=[_position(symbol="SOLUSDT")]), risk_percent=1.0,
        )
        assert RiskEngine().assess(ctx).metric("open_risk").status is MetricStatus.UNKNOWN


class TestCandidateSizing:
    def test_candidate_margin_derived_from_configured_leverage(self):
        c = candidate_from_position_size(
            "BTCUSDT", {"notional_usd": 4000.0, "risk_usd": 46.94}, leverage=20,
        )
        assert c.initial_margin_usd == pytest.approx(200.0)
        assert c.is_candidate is True

    def test_candidate_margin_unknown_without_leverage(self):
        c = candidate_from_position_size(
            "BTCUSDT", {"notional_usd": 4000.0, "risk_usd": 46.94}, leverage=None,
        )
        assert c.initial_margin_usd is None

    def test_no_size_yields_no_candidate(self):
        assert candidate_from_position_size("BTCUSDT", None) is None


class TestCoherenceAssertion:
    def test_default_limits_are_coherent(self):
        RiskLimits().assert_coherent()  # must not raise

    def test_the_historical_misconfiguration_is_rejected(self):
        # The exact production bug: a 50% gross-notional ceiling made
        # max_open_risk_percent unreachable and nothing said so.
        bad = RiskLimits(max_leverage_percent=50.0, max_open_risk_percent=6.0)
        with pytest.raises(ValueError) as exc:
            bad.assert_coherent()
        assert "UNREACHABLE" in str(exc.value)

    def test_per_trade_risk_above_portfolio_risk_is_rejected(self):
        with pytest.raises(ValueError):
            RiskLimits(max_risk_percent_per_trade=10.0, max_open_risk_percent=6.0).assert_coherent()

    def test_warn_band_above_block_band_is_rejected(self):
        with pytest.raises(ValueError):
            RiskLimits(warn_cluster_concentration_percent=80.0,
                       max_cluster_concentration_percent=50.0).assert_coherent()

    def test_required_leverage_headroom_is_derived_not_hardcoded(self):
        limits = RiskLimits()
        implied = limits.max_open_risk_percent / COHERENCE_REFERENCE_STOP_FRACTION
        assert limits.max_leverage_percent >= implied
