"""
Phase 4 - equity persistence, risk audit trail, versioning.

Covers Mandatory Addition #1 (risk_engine_version on every assessment) and
#2 (the complete metric snapshot persisted for approved AND rejected
decisions), plus the provenance fields that make a stored decision
interpretable later.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.risk.context_builder import build_risk_context
from app.risk.limits import RiskLimits
from app.risk.result import RISK_ENGINE_VERSION, MetricStatus
from app.risk.risk_engine import RiskEngine


def _futures(wallet=4694.0, margin_balance=4800.0, maintenance=40.0, positions=()):
    return SimpleNamespace(
        wallet_balance=wallet, available_balance=wallet - 500,
        margin_balance=margin_balance, unrealized_pnl=margin_balance - wallet,
        total_maintenance_margin=maintenance, open_positions=list(positions),
    )


# ==========================================================================
# MANDATORY ADDITION #1 - risk engine versioning
# ==========================================================================
class TestRiskEngineVersioning:
    def test_every_assessment_carries_the_engine_version(self):
        a = RiskEngine().assess(build_risk_context(_futures(), risk_percent=1.0))
        assert a.risk_engine_version == RISK_ENGINE_VERSION
        assert a.risk_engine_version == "2.0.0"

    def test_the_legacy_entry_point_also_carries_it(self):
        a = RiskEngine().assess_new_trade(
            account_balance=1000.0, entry_price=100.0, stop_loss=90.0, risk_percent=1.0,
        )
        assert a.risk_engine_version == RISK_ENGINE_VERSION

    def test_version_is_in_the_persisted_snapshot(self):
        a = RiskEngine().assess(build_risk_context(_futures(), risk_percent=1.0))
        assert a.metric_snapshot()["risk_engine_version"] == RISK_ENGINE_VERSION


# ==========================================================================
# MANDATORY ADDITION #2 - the metric snapshot
# ==========================================================================
class TestMetricSnapshot:
    def test_snapshot_contains_every_metric_by_name(self):
        a = RiskEngine().assess(build_risk_context(_futures(), risk_percent=1.0))
        snap = a.metric_snapshot(RiskLimits())
        for name in ("open_risk", "margin_usage", "effective_leverage", "margin_ratio",
                     "daily_loss", "drawdown", "cluster_concentration", "correlation"):
            assert name in snap["metrics"], f"{name} missing from snapshot"
            assert name in snap["status"]

    def test_snapshot_records_the_limits_in_force(self):
        snap = RiskEngine().assess(
            build_risk_context(_futures(), risk_percent=1.0)
        ).metric_snapshot(RiskLimits())
        assert snap["limits"]["max_leverage_percent"] == 300.0
        assert snap["limits"]["max_open_risk_percent"] == 6.0

    def test_snapshot_is_produced_for_a_REJECTED_assessment_too(self):
        # A rejection must be as auditable as an approval.
        ctx = build_risk_context(
            _futures(margin_balance=1000.0, maintenance=900.0), risk_percent=1.0,
        )
        a = RiskEngine().assess(ctx)
        assert a.approved is False
        snap = a.metric_snapshot(RiskLimits())
        assert snap["approved"] is False
        assert snap["metrics"]["margin_ratio"]["status"] == "BLOCK"
        assert snap["reasons"]

    def test_snapshot_records_each_metric_value_and_limit(self):
        snap = RiskEngine().assess(
            build_risk_context(_futures(margin_balance=4800.0, maintenance=40.0), risk_percent=1.0)
        ).metric_snapshot(RiskLimits())
        mr = snap["metrics"]["margin_ratio"]
        assert mr["value"] == pytest.approx(40.0 / 4800.0 * 100, abs=0.01)
        assert mr["limit"] == 50.0

    def test_unknown_metrics_are_named_explicitly_not_hidden(self):
        snap = RiskEngine().assess(
            build_risk_context(None, risk_percent=1.0, fallback_equity=1000.0)
        ).metric_snapshot(RiskLimits())
        assert "margin_ratio" in snap["unknown"]
        assert any("margin_ratio" in n for n in snap["unknown_notices"])

    def test_snapshot_is_json_serialisable(self):
        # It is persisted into a JSON column - a non-builtin would fail the
        # write at runtime rather than here.
        import json
        snap = RiskEngine().assess(
            build_risk_context(_futures(), risk_percent=1.0)
        ).metric_snapshot(RiskLimits())
        assert json.loads(json.dumps(snap))["risk_engine_version"] == RISK_ENGINE_VERSION


# ==========================================================================
# PROVENANCE - context_source + exchange_snapshot_timestamp
# ==========================================================================
class TestProvenance:
    def test_exchange_backed_decision_is_labelled_binance_exchange(self):
        ts = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
        ctx = build_risk_context(_futures(), risk_percent=1.0, exchange_snapshot_at=ts)
        a = RiskEngine().assess(ctx)
        assert a.context_source == "binance_exchange"
        assert a.exchange_snapshot_at == ts

    def test_fallback_decision_is_labelled_database_fallback(self):
        a = RiskEngine().assess(
            build_risk_context(None, risk_percent=1.0, fallback_equity=1000.0)
        )
        assert a.context_source == "database_fallback"
        assert a.exchange_snapshot_at is None

    def test_provenance_is_in_the_persisted_snapshot(self):
        ts = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
        snap = RiskEngine().assess(
            build_risk_context(_futures(), risk_percent=1.0, exchange_snapshot_at=ts)
        ).metric_snapshot()
        assert snap["context_source"] == "binance_exchange"
        assert snap["exchange_snapshot_timestamp"] == ts.isoformat()

    def test_timestamp_is_serialised_as_iso_not_a_datetime_object(self):
        ts = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
        snap = RiskEngine().assess(
            build_risk_context(_futures(), risk_percent=1.0, exchange_snapshot_at=ts)
        ).metric_snapshot()
        assert isinstance(snap["exchange_snapshot_timestamp"], str)

    def test_fallback_snapshot_timestamp_is_null_not_fabricated(self):
        snap = RiskEngine().assess(
            build_risk_context(None, risk_percent=1.0, fallback_equity=1000.0)
        ).metric_snapshot()
        assert snap["exchange_snapshot_timestamp"] is None
        assert snap["context_source"] == "database_fallback"

    def test_legacy_entry_point_defaults_to_database_fallback(self):
        a = RiskEngine().assess_new_trade(
            account_balance=1000.0, entry_price=100.0, stop_loss=90.0, risk_percent=1.0,
        )
        assert a.context_source == "database_fallback"


# ==========================================================================
# DRAWDOWN - the metric the equity series exists for
# ==========================================================================
class TestDrawdownUsesPersistedPeak:
    def test_drawdown_is_unknown_without_a_recorded_peak(self):
        m = RiskEngine().assess(
            build_risk_context(_futures(margin_balance=4000.0), risk_percent=1.0)
        ).metric("drawdown")
        assert m.status is MetricStatus.UNKNOWN
        assert m.value is None, "must not report 0% - that would claim 'no drawdown'"

    def test_drawdown_is_measured_once_a_peak_exists(self):
        ctx = build_risk_context(
            _futures(margin_balance=8000.0), risk_percent=1.0, peak_equity=10000.0,
        )
        m = RiskEngine().assess(ctx).metric("drawdown")
        assert m.status is MetricStatus.BLOCK       # 20% == the default limit
        assert m.value == pytest.approx(20.0)
        assert m.extra["peak_equity"] == 10000.0

    def test_equity_above_the_peak_is_zero_drawdown_not_negative(self):
        ctx = build_risk_context(
            _futures(margin_balance=12000.0), risk_percent=1.0, peak_equity=10000.0,
        )
        assert RiskEngine().assess(ctx).metric("drawdown").value == 0.0


# ==========================================================================
# AUDIT LOGGING - never raises, always states provenance
# ==========================================================================
class TestAuditLogging:
    def test_log_assessment_handles_approved_and_rejected(self):
        from app.services.risk_audit import log_assessment

        approved = RiskEngine().assess(build_risk_context(_futures(), risk_percent=1.0))
        rejected = RiskEngine().assess(
            build_risk_context(_futures(margin_balance=1000.0, maintenance=900.0), risk_percent=1.0)
        )
        # Must not raise for either verdict.
        log_assessment(approved, "BTCUSDT", None)
        log_assessment(rejected, "BTCUSDT", None)

    def test_log_assessment_survives_a_missing_audit_id(self):
        from app.services.risk_audit import log_assessment
        a = RiskEngine().assess(build_risk_context(_futures(), risk_percent=1.0))
        log_assessment(a, "ETHUSDT")  # audit row failed to write - still logs
