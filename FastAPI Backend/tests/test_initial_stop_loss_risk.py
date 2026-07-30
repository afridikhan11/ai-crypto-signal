"""
Regression suite for the initial_stop_loss / live stop_loss separation
(2026-07-30) and the committed-only exposure fix.

ISSUE 1 - Trade Management moves `signal.stop_loss`, including to EXACTLY
`entry_price` at breakeven. Sizing off that live stop collapsed the risk
distance to zero and made position sizing return None, surfacing as
"unknown account balance, or a degenerate entry/stop pair" even though the
balance was known.

ISSUE 2 - the execution gate counted un-executed ACTIVE signals as open
exposure, so hypothetical positions consumed the real exposure budget.
"""
from types import SimpleNamespace

import pytest

from app.risk.risk_engine import RiskEngine
from app.services.position_sizing import calculate_position_size

BAL = 4694.0
RISK = 1.0


# ==========================================================================
# ISSUE 1 - the degenerate-pair failure and why the frozen stop fixes it
# ==========================================================================
class TestBreakevenBreaksSizingOnTheLiveStop:
    def test_stop_moved_to_entry_makes_sizing_return_none(self):
        # This is EXACTLY what TradeManagementEngine does at 1R:
        # ManagementAction(MOVE_TO_BREAKEVEN, new_stop_loss=entry_price).
        entry = 98491.00
        live_stop_after_breakeven = entry
        assert calculate_position_size(BAL, entry, live_stop_after_breakeven, RISK) is None

    def test_the_original_stop_still_sizes_correctly(self):
        entry, initial_stop = 98491.00, 97185.00
        pos = calculate_position_size(BAL, entry, initial_stop, RISK)
        assert pos is not None
        assert pos["risk_usd"] == round(BAL * RISK / 100, 2)
        assert pos["quantity"] > 0

    def test_risk_engine_reports_the_misleading_reason_on_the_live_stop(self):
        entry = 98491.00
        a = RiskEngine().assess_new_trade(
            account_balance=BAL, entry_price=entry, stop_loss=entry, risk_percent=RISK,
        )
        assert a.approved is False
        # RISK ENGINE 2.0.0 split the old combined "unknown balance OR
        # degenerate pair" message into two distinct reasons - conflating
        # them is exactly what made this bug so hard to diagnose.
        assert any("risk distance is zero" in r for r in a.reasons)
        assert not any("balance is unknown" in r for r in a.reasons)
        # The balance was perfectly well known - the message's "unknown
        # account balance" clause is what made this so hard to diagnose.
        assert a.exposure_percent == 0.0

    def test_same_trade_is_approved_when_measured_from_the_original_stop(self):
        a = RiskEngine().assess_new_trade(
            account_balance=BAL, entry_price=98491.00, stop_loss=97185.00,
            risk_percent=RISK,
        )
        assert a.position_size is not None
        assert not any("degenerate" in r for r in a.reasons)


class TestTrailedStopInflatesExposure:
    """Short of breakeven, a partly-trailed stop does not break sizing - it
    silently INFLATES notional, which is the other half of the same bug."""

    def test_trailing_the_stop_inflates_notional_without_any_real_change(self):
        entry, initial_stop, trailed_stop = 3524.93, 3466.00, 3508.00

        from_initial = calculate_position_size(BAL, entry, initial_stop, RISK)
        from_trailed = calculate_position_size(BAL, entry, trailed_stop, RISK)

        # Dollar risk is identical - only the measured notional moves.
        assert from_initial["risk_usd"] == from_trailed["risk_usd"]
        assert from_trailed["notional_usd"] > from_initial["notional_usd"] * 3

    def test_exposure_is_stable_across_trailing_when_measured_from_initial(self):
        entry, initial_stop = 3524.93, 3466.00
        first = calculate_position_size(BAL, entry, initial_stop, RISK)
        # ... trade management trails the stop; the ORIGINAL is unchanged ...
        later = calculate_position_size(BAL, entry, initial_stop, RISK)
        assert first["notional_usd"] == later["notional_usd"]


# ==========================================================================
# ISSUE 2 - only real positions may consume the exposure budget
# ==========================================================================
def _signal(executed: bool, status: str):
    return SimpleNamespace(executed=executed, status=status)


class TestCommittedOnlyExposure:
    def test_unexecuted_signals_must_not_consume_the_exposure_budget(self):
        # Three hypothetical positions the user never executed.
        # 4 x 4000 = 16,000 notional on 4,694 equity = 341% leverage,
        # above the 300% ceiling. (Pre-2.0.0 this test used the superseded
        # 50% gross-notional cap - see EXPOSURE_MODEL_ANALYSIS.md.)
        hypothetical = [
            {"risk_usd": 46.94, "notional_usd": 4000.0} for _ in range(4)
        ]
        engine = RiskEngine()

        counted = engine.assess_new_trade(
            account_balance=BAL, entry_price=100.0, stop_loss=95.0,
            risk_percent=RISK, open_positions=hypothetical,
        )
        not_counted = engine.assess_new_trade(
            account_balance=BAL, entry_price=100.0, stop_loss=95.0,
            risk_percent=RISK, open_positions=[],
        )

        assert counted.exposure_percent > not_counted.exposure_percent
        assert any("leverage" in r.lower() for r in counted.reasons)
        assert not any("leverage" in r.lower() for r in not_counted.reasons)
        assert not_counted.approved is True

    def test_a_real_committed_position_still_counts(self):
        # The fix must not disable the check - genuine exposure still blocks.
        # 16,000 notional on 4,694 equity = 341% leverage > 300% ceiling.
        real = [{"risk_usd": 46.94, "notional_usd": 16000.0}]
        a = RiskEngine().assess_new_trade(
            account_balance=BAL, entry_price=100.0, stop_loss=95.0,
            risk_percent=RISK, open_positions=real,
        )
        assert a.approved is False
        assert any("leverage" in r.lower() for r in a.reasons)


class TestExposureArithmeticIsUnchanged:
    """The fixes change which STOP and which SIGNALS feed the calculation.
    They must not change the calculation itself, or any limit."""

    def test_limits_are_untouched(self):
        limits = RiskEngine().limits
        # Retained for backward compatibility (WPF RiskLimitsDto,
        # GET /trading/status) but NO LONGER ENFORCED - max_leverage_percent
        # is the control. Value unchanged so nothing displaying it shifts
        # meaning silently.
        assert limits.max_exposure_percent == 50.0
        assert limits.max_leverage_percent == 300.0
        assert limits.max_open_risk_percent == 6.0
        assert limits.max_risk_percent_per_trade == 2.0
        assert limits.max_daily_loss_percent == 5.0
        assert limits.max_drawdown_percent == 20.0

    def test_exposure_formula_is_still_notional_over_balance(self):
        a = RiskEngine().assess_new_trade(
            account_balance=1000.0, entry_price=100.0, stop_loss=90.0,
            risk_percent=1.0, open_positions=[],
        )
        # risk_usd 10 / distance 10 = qty 1 -> notional 100 -> 10% of 1000.
        assert a.position_size["quantity"] == 1.0
        assert a.position_size["notional_usd"] == 100.0
        assert a.exposure_percent == 10.0
