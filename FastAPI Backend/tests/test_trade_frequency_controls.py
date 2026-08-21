"""
Trade-frequency & directional risk controls (2026-08-21).

Measured on testnet: fees consumed 72% of the bot's gross P/L, driven by
same-symbol churn (XRP shorted 6x into one rally) and a one-sided book (32 of
33 executed trades short). Three controls answer that:

  1. reentry_blocked()        - per-coin cooldown after a DECIDED trade closes
  2. direction_cap_reached()  - cap on live same-direction signals
  3. counter_trend_exception  - the HTF-opposition gate softens from hard-block
                                to a confidence bar, mirrored consistently in
                                EntryValidationEngine

All three are exercised here as pure logic - no DB, no network.
"""
from datetime import datetime, timedelta, timezone

from app.core.config import get_settings
from app.scheduler.universal_scanner import direction_cap_reached, reentry_blocked
from app.strategy.entry_validation_engine import EntryValidationEngine


def _now():
    return datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


# ======================================================================
# 1. Re-entry cooldown (fee churn)
# ======================================================================
class TestReentryCooldown:
    def test_blocks_within_cooldown(self):
        closed = _now() - timedelta(minutes=30)
        assert reentry_blocked(closed, _now(), cooldown_minutes=60) is True

    def test_allows_after_cooldown(self):
        closed = _now() - timedelta(minutes=61)
        assert reentry_blocked(closed, _now(), cooldown_minutes=60) is False

    def test_exact_boundary_is_allowed(self):
        closed = _now() - timedelta(minutes=60)
        assert reentry_blocked(closed, _now(), cooldown_minutes=60) is False

    def test_no_prior_trade_never_blocks(self):
        assert reentry_blocked(None, _now(), cooldown_minutes=60) is False

    def test_zero_disables(self):
        closed = _now() - timedelta(seconds=1)
        assert reentry_blocked(closed, _now(), cooldown_minutes=0) is False


# ======================================================================
# 2. Same-direction concurrency cap
# ======================================================================
class TestDirectionCap:
    def test_reached_at_cap(self):
        assert direction_cap_reached(3, cap=3) is True

    def test_below_cap_allows(self):
        assert direction_cap_reached(2, cap=3) is False

    def test_zero_disables(self):
        assert direction_cap_reached(50, cap=0) is False


# ======================================================================
# 3. Counter-trend exception mirrored in EntryValidationEngine
# ======================================================================
class _Bias:
    def __init__(self, direction):
        self.direction = direction


class TestCounterTrendException:
    def _validate(self, exception):
        return EntryValidationEngine(min_risk_reward=1.0).validate(
            "LONG",
            institutional_bias=_Bias("bearish"),   # opposes the LONG
            liquidity_present=True,
            risk_reward=3.0,
            invalidation_level=95.0,
            counter_trend_exception=exception,
        )

    def test_opposing_bias_fails_without_exception(self):
        result = self._validate(exception=False)
        assert "institutional_bias" in result.failed_checks

    def test_opposing_bias_passes_with_exception(self):
        result = self._validate(exception=True)
        assert "institutional_bias" not in result.failed_checks
        # The detail records the exception honestly, not a fake alignment.
        assert "exception" in result.detail_for("institutional_bias")

    def test_aligned_bias_unaffected_by_flag(self):
        result = EntryValidationEngine(min_risk_reward=1.0).validate(
            "SHORT",
            institutional_bias=_Bias("bearish"),   # aligned
            liquidity_present=True,
            risk_reward=3.0,
            invalidation_level=105.0,
            counter_trend_exception=True,
        )
        assert "institutional_bias" not in result.failed_checks
        assert "aligned" in result.detail_for("institutional_bias")


# ======================================================================
# Config defaults - the controls ship on with conservative values
# ======================================================================
class TestConfigDefaults:
    def test_defaults(self):
        s = get_settings()
        assert s.signal_reentry_cooldown_minutes == 60
        assert s.max_concurrent_same_direction == 3
        assert s.htf_opposition_mode == "confidence_bar"
        # Above every confidence the pipeline produced in its first 136
        # signals (68-79): the bar admits only exceptional counter-trend setups.
        assert s.counter_trend_min_confidence == 80
