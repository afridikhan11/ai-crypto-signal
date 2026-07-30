"""
Regression suite for app.scheduler.signal_monitor - outcome resolution plus
the ICT Trade Management integration (dynamic stops, structure-failure
closure, and the safety invariants documented in that module).

Exercises the real `SignalMonitor` methods against lightweight stand-ins
for the `Signal` ORM row and the data manager. The ORM row is stubbed
rather than constructed because building a real `Signal` requires a live
SQLAlchemy metadata/engine binding; every attribute the monitor actually
touches (direction/entry_price/stop_loss/take_profit/status/closed_at/
created_at/confidence) is a plain value on the real model too, so the
monitor code under test is genuinely exercised.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

import app.scheduler.signal_monitor as signal_monitor_module
from app.models.signal import Direction, SignalStatus
from app.scheduler.signal_monitor import SignalMonitor
from app.services import binance_credentials
from app.services.binance_account_service import BinanceAccountService
from app.services.binance_trading_service import BinanceTradingService
from app.smc.market_structure_engine import (
    StructureBreak, StructureScope, SwingPoint, SwingStrength, SwingType,
)
from app.strategy.trade_management_engine import ManagementAction, ManagementActionType


CREATED_AT = datetime(2026, 1, 5, 12, 0, 0, tzinfo=timezone.utc)


def _run(coro):
    return asyncio.run(coro)


class _FakeSignal:
    """Mirrors every Signal attribute the monitor reads or writes.

    `executed` defaults to False (a signal that was never sent to Binance)
    so every pre-existing test below exercises the UNCHANGED, DB-only
    behavior `_apply_management`/`_sync_exchange_stop` still have for a
    non-executed signal - see TestExchangeSyncForExecutedSignals for the
    executed=True paths added for Live Execution Safety."""

    def __init__(self, direction=Direction.LONG, entry_price=100.0, stop_loss=95.0,
                 take_profit=115.0, created_at=CREATED_AT, executed=False,
                 executed_environment=None):
        self.id = uuid.uuid4()
        self.direction = direction
        self.entry_price = entry_price
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.status = SignalStatus.ACTIVE
        self.closed_at = None
        self.created_at = created_at
        self.confidence = 90
        self.executed = executed
        self.executed_environment = executed_environment


class _FakeDataManager:
    def __init__(self, frames=None):
        self.frames = frames or {}

    def get_dataframe(self, symbol, timeframe, limit=None):
        return self.frames.get(timeframe, pd.DataFrame())


def _swing(price, ts):
    return SwingPoint(timestamp=ts, price=price, type=SwingType.HL, index=0,
                      strength=SwingStrength.STRONG, displacement_ratio=1.0)


def _break(direction, level, ts, type_="BOS"):
    return StructureBreak(
        timestamp=ts, type=type_, direction=direction, broken_swing=_swing(level, ts),
        level=level, scope=StructureScope.EXTERNAL, displacement_ratio=1.5,
        is_mss=(type_ == "CHoCH"),
    )


@pytest.fixture(scope="module")
def monitor():
    return SignalMonitor(_FakeDataManager())


class TestOutcomeResolution:
    def test_long_take_profit(self, monitor):
        assert monitor._resolve_status(_FakeSignal(), 116.0) is SignalStatus.TP_HIT

    def test_long_stop_loss(self, monitor):
        assert monitor._resolve_status(_FakeSignal(), 94.0) is SignalStatus.STOPPED

    def test_long_between_levels_stays_active(self, monitor):
        assert monitor._resolve_status(_FakeSignal(), 105.0) is None

    def test_short_take_profit(self, monitor):
        s = _FakeSignal(direction=Direction.SHORT, entry_price=100.0, stop_loss=105.0, take_profit=90.0)
        assert monitor._resolve_status(s, 89.0) is SignalStatus.TP_HIT

    def test_short_stop_loss(self, monitor):
        s = _FakeSignal(direction=Direction.SHORT, entry_price=100.0, stop_loss=105.0, take_profit=90.0)
        assert monitor._resolve_status(s, 106.0) is SignalStatus.STOPPED


class TestBreaksAfterFilter:
    """Safety invariant 3 - the break that created the signal must never be
    re-read as a structure event against it."""

    def test_older_breaks_excluded(self, monitor):
        older = _break("bearish", 99.0, pd.Timestamp("2026-01-05 11:00:00"))
        assert monitor._breaks_after([older], CREATED_AT) == []

    def test_newer_breaks_included(self, monitor):
        newer = _break("bearish", 99.0, pd.Timestamp("2026-01-05 13:00:00"))
        assert monitor._breaks_after([newer], CREATED_AT) == [newer]

    def test_break_at_exact_creation_time_excluded(self, monitor):
        same = _break("bearish", 99.0, pd.Timestamp("2026-01-05 12:00:00"))
        assert monitor._breaks_after([same], CREATED_AT) == []

    def test_tz_aware_created_at_normalized(self, monitor):
        newer = _break("bullish", 101.0, pd.Timestamp("2026-01-05 12:30:00"))
        assert monitor._breaks_after([newer], CREATED_AT) == [newer]

    def test_no_created_at_yields_nothing(self, monitor):
        newer = _break("bullish", 101.0, pd.Timestamp("2026-01-05 13:00:00"))
        assert monitor._breaks_after([newer], None) == []

    def test_empty_input_yields_nothing(self, monitor):
        assert monitor._breaks_after([], CREATED_AT) == []


class TestStopImprovementInvariant:
    """Safety invariant 1 - a stop may only ever move toward profit."""

    def test_long_higher_stop_improves(self, monitor):
        assert monitor._improves_stop(_FakeSignal(), 98.0) is True

    def test_long_lower_stop_rejected(self, monitor):
        assert monitor._improves_stop(_FakeSignal(), 90.0) is False

    def test_short_lower_stop_improves(self, monitor):
        s = _FakeSignal(direction=Direction.SHORT, stop_loss=105.0)
        assert monitor._improves_stop(s, 102.0) is True

    def test_short_higher_stop_rejected(self, monitor):
        s = _FakeSignal(direction=Direction.SHORT, stop_loss=105.0)
        assert monitor._improves_stop(s, 110.0) is False


class TestApplyManagement:
    """These signals all default to executed=False (never sent to
    Binance), so `_apply_management` behaves exactly as it did before
    Exchange Stop Synchronization existed - DB-only, unchanged. See
    TestExchangeSyncForExecutedSignals for the executed=True paths."""

    def test_breakeven_moves_stop_and_emits_event(self, monitor):
        signal = _FakeSignal()
        actions = [ManagementAction(ManagementActionType.MOVE_TO_BREAKEVEN,
                                     reason="1R reached", new_stop_loss=100.0)]
        events = _run(monitor._apply_management(signal, "BTCUSDT", 105.0, actions))
        assert signal.stop_loss == 100.0
        assert len(events) == 1
        assert events[0]["event"] == "signal_stop_updated"
        assert events[0]["previous_stop_loss"] == 95.0

    def test_trail_preferred_over_breakeven_when_more_protective(self, monitor):
        signal = _FakeSignal()
        actions = [
            ManagementAction(ManagementActionType.MOVE_TO_BREAKEVEN, reason="1R", new_stop_loss=100.0),
            ManagementAction(ManagementActionType.TRAIL_STOP, reason="BOS", new_stop_loss=103.0),
        ]
        _run(monitor._apply_management(signal, "BTCUSDT", 110.0, actions))
        assert signal.stop_loss == 103.0

    def test_less_protective_trail_not_applied_over_breakeven(self, monitor):
        signal = _FakeSignal()
        actions = [
            ManagementAction(ManagementActionType.MOVE_TO_BREAKEVEN, reason="1R", new_stop_loss=100.0),
            ManagementAction(ManagementActionType.TRAIL_STOP, reason="BOS", new_stop_loss=97.0),
        ]
        _run(monitor._apply_management(signal, "BTCUSDT", 110.0, actions))
        assert signal.stop_loss == 100.0

    def test_worsening_stop_never_applied(self, monitor):
        signal = _FakeSignal()
        actions = [ManagementAction(ManagementActionType.TRAIL_STOP,
                                     reason="bad data", new_stop_loss=80.0)]
        events = _run(monitor._apply_management(signal, "BTCUSDT", 105.0, actions))
        assert signal.stop_loss == 95.0
        assert events == []

    def test_structure_failure_cancels_signal(self, monitor):
        signal = _FakeSignal()
        actions = [ManagementAction(ManagementActionType.CLOSE_STRUCTURE_FAILURE,
                                     reason="Opposing CHoCH at 98.0")]
        events = _run(monitor._apply_management(signal, "BTCUSDT", 99.0, actions))
        assert signal.status is SignalStatus.CANCELLED
        assert signal.closed_at is not None
        assert events[0]["status"] == SignalStatus.CANCELLED.value
        assert events[0]["live_position_closed"] is False

    def test_structure_failure_takes_precedence_over_stop_move(self, monitor):
        signal = _FakeSignal()
        actions = [
            ManagementAction(ManagementActionType.MOVE_TO_BREAKEVEN, reason="1R", new_stop_loss=100.0),
            ManagementAction(ManagementActionType.CLOSE_STRUCTURE_FAILURE, reason="failed"),
        ]
        events = _run(monitor._apply_management(signal, "BTCUSDT", 105.0, actions))
        assert signal.status is SignalStatus.CANCELLED
        assert signal.stop_loss == 95.0  # untouched - closing supersedes bookkeeping
        assert len(events) == 1

    def test_structure_failure_is_cancelled_not_stopped(self, monitor):
        """Price never reached the stop - recording STOPPED would corrupt
        win-rate stats and the scorer calibration that reads them."""
        signal = _FakeSignal()
        actions = [ManagementAction(ManagementActionType.CLOSE_STRUCTURE_FAILURE, reason="failed")]
        _run(monitor._apply_management(signal, "BTCUSDT", 101.0, actions))
        assert signal.status is not SignalStatus.STOPPED

    def test_hold_changes_nothing(self, monitor):
        signal = _FakeSignal()
        events = _run(monitor._apply_management(
            signal, "BTCUSDT", 101.0,
            [ManagementAction(ManagementActionType.HOLD, reason="nothing to do")],
        ))
        assert events == []
        assert signal.stop_loss == 95.0
        assert signal.status is SignalStatus.ACTIVE

    def test_partial_close_is_advisory_only(self, monitor):
        signal = _FakeSignal()
        events = _run(monitor._apply_management(
            signal, "BTCUSDT", 110.0,
            [ManagementAction(ManagementActionType.PARTIAL_CLOSE,
                              reason="halfway", close_fraction=0.5)],
        ))
        assert events == []
        assert signal.status is SignalStatus.ACTIVE
        assert signal.stop_loss == 95.0


class TestEvaluateManagementDegradation:
    def test_empty_dataframe_yields_no_actions(self):
        monitor = SignalMonitor(_FakeDataManager({}))
        assert monitor._evaluate_management(_FakeSignal(), "BTCUSDT", 100.0) == []

    def test_too_short_dataframe_yields_no_actions(self):
        idx = pd.date_range("2026-01-05", periods=10, freq="15min")
        df = pd.DataFrame({
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0,
        }, index=idx)
        monitor = SignalMonitor(_FakeDataManager({"15m": df}))
        assert monitor._evaluate_management(_FakeSignal(), "BTCUSDT", 100.0) == []


class _FakeFuturesPosition:
    def __init__(self, symbol, position_amt):
        self.symbol = symbol
        self.position_amt = position_amt


class _FakeFuturesAccount:
    def __init__(self, open_positions):
        self.open_positions = open_positions


class _FakeStopReplacementResult:
    def __init__(self, success, warning=None):
        self.success = success
        self.new_order = None
        self.cancelled_order_ids = []
        self.warning = warning


class TestBuildTradingServiceFor:
    """Live Execution Safety, Objective 2 - `_build_trading_service_for`
    never guesses: a non-executed signal, missing credentials, or a
    MISMATCHED environment must all skip the sync (return None) rather
    than firing a request against the wrong account."""

    def test_non_executed_signal_returns_none_without_reading_credentials(self):
        monitor = SignalMonitor(_FakeDataManager())

        def boom():
            raise AssertionError("load_credentials must not be called for a non-executed signal")

        with patch.object(binance_credentials, "load_credentials", boom):
            signal = _FakeSignal(executed=False)
            assert monitor._build_trading_service_for(signal) is None

    def test_no_saved_credentials_returns_none(self):
        monitor = SignalMonitor(_FakeDataManager())
        with patch.object(binance_credentials, "load_credentials", return_value=None):
            signal = _FakeSignal(executed=True, executed_environment="testnet")
            assert monitor._build_trading_service_for(signal) is None

    def test_environment_mismatch_returns_none(self):
        monitor = SignalMonitor(_FakeDataManager())
        creds = {"api_key": "k", "api_secret": "s", "testnet": False}  # currently mainnet
        with patch.object(binance_credentials, "load_credentials", return_value=creds):
            signal = _FakeSignal(executed=True, executed_environment="testnet")  # executed on testnet
            assert monitor._build_trading_service_for(signal) is None

    def test_matching_environment_returns_a_trading_service(self):
        monitor = SignalMonitor(_FakeDataManager())
        creds = {"api_key": "k", "api_secret": "s", "testnet": True}
        with patch.object(binance_credentials, "load_credentials", return_value=creds):
            signal = _FakeSignal(executed=True, executed_environment="testnet")
            service = monitor._build_trading_service_for(signal)
        assert isinstance(service, BinanceTradingService)
        assert service.testnet is True

    def test_unrecorded_execution_environment_does_not_block(self):
        """An older signal executed before `executed_environment` was
        recorded (None) must not become permanently un-syncable - only an
        explicit MISMATCH refuses, never an unknown value."""
        monitor = SignalMonitor(_FakeDataManager())
        creds = {"api_key": "k", "api_secret": "s", "testnet": True}
        with patch.object(binance_credentials, "load_credentials", return_value=creds):
            signal = _FakeSignal(executed=True, executed_environment=None)
            assert monitor._build_trading_service_for(signal) is not None


class TestSyncExchangeStopShortCircuits:
    """The real (not monkeypatched) `_sync_exchange_stop` for a
    non-executed signal must return True immediately without touching
    credentials at all - this is the unchanged, DB-only behavior every
    pre-existing test above already relies on."""

    def test_non_executed_signal_returns_true_without_touching_credentials(self):
        monitor = SignalMonitor(_FakeDataManager())

        def boom():
            raise AssertionError("load_credentials must not be called for a non-executed signal")

        with patch.object(binance_credentials, "load_credentials", boom):
            signal = _FakeSignal(executed=False)
            result = _run(monitor._sync_exchange_stop(signal, "BTCUSDT", 100.0))
        assert result is True

    def test_executed_signal_with_no_credentials_returns_false(self):
        monitor = SignalMonitor(_FakeDataManager())
        with patch.object(binance_credentials, "load_credentials", return_value=None):
            signal = _FakeSignal(executed=True, executed_environment="testnet")
            result = _run(monitor._sync_exchange_stop(signal, "BTCUSDT", 100.0))
        assert result is False


class TestApplyManagementGatesOnExchangeSync:
    """Live Execution Safety, Objective 2/3 - for an EXECUTED signal,
    `Signal.stop_loss` in the database must only advance once
    `_sync_exchange_stop` actually confirms the exchange - never before,
    never on a failed sync. Isolates `_apply_management`'s own gating
    decision from the deeper exchange-service mechanics, which are covered
    directly against `BinanceTradingService.replace_stop_loss` in
    tests/test_binance_trading_service_stop_sync.py and against
    `_build_trading_service_for`/`_sync_exchange_stop` themselves above."""

    def test_successful_sync_advances_db_stop_and_tags_the_event(self):
        monitor = SignalMonitor(_FakeDataManager())
        signal = _FakeSignal(executed=True, executed_environment="testnet")
        actions = [ManagementAction(ManagementActionType.MOVE_TO_BREAKEVEN,
                                     reason="1R reached", new_stop_loss=100.0)]

        async def fake_sync(sig, symbol, new_stop):
            return True
        monitor._sync_exchange_stop = fake_sync

        events = _run(monitor._apply_management(signal, "BTCUSDT", 105.0, actions))
        assert signal.stop_loss == 100.0
        assert len(events) == 1
        assert events[0]["event"] == "signal_stop_updated"
        assert events[0]["exchange_synced"] is True

    def test_failed_sync_leaves_db_stop_unchanged_and_emits_no_event(self):
        monitor = SignalMonitor(_FakeDataManager())
        signal = _FakeSignal(executed=True, executed_environment="testnet")
        actions = [ManagementAction(ManagementActionType.MOVE_TO_BREAKEVEN,
                                     reason="1R reached", new_stop_loss=100.0)]

        async def fake_sync(sig, symbol, new_stop):
            return False
        monitor._sync_exchange_stop = fake_sync

        events = _run(monitor._apply_management(signal, "BTCUSDT", 105.0, actions))
        assert signal.stop_loss == 95.0  # untouched
        assert events == []

    def test_non_executed_signal_event_reports_exchange_synced_false(self):
        """Real `_sync_exchange_stop` (not monkeypatched) - proves the
        non-executed short-circuit wires all the way through
        `_apply_management`'s own event shape, not just its own return
        value."""
        monitor = SignalMonitor(_FakeDataManager())
        signal = _FakeSignal(executed=False)
        actions = [ManagementAction(ManagementActionType.MOVE_TO_BREAKEVEN,
                                     reason="1R reached", new_stop_loss=100.0)]

        events = _run(monitor._apply_management(signal, "BTCUSDT", 105.0, actions))
        assert signal.stop_loss == 100.0
        assert events[0]["exchange_synced"] is False


class TestStructureFailureClosesLivePosition:
    """Live Execution Safety, Objective 2 - a structure-failure verdict on
    an EXECUTED signal must also close the real position; a non-executed
    signal has nothing real to close. Isolates `_apply_management`'s
    decision of WHETHER to call `_close_live_position_on_structure_failure`,
    not that helper's own Binance-facing behavior (which has its own
    exception containment tested by inspection above in the module
    docstring's ordering guarantee and in the BinanceTradingService suite)."""

    def test_executed_signal_triggers_live_close(self):
        monitor = SignalMonitor(_FakeDataManager())
        signal = _FakeSignal(executed=True, executed_environment="testnet")
        calls = []

        async def fake_close(sig, symbol):
            calls.append((sig.id, symbol))
        monitor._close_live_position_on_structure_failure = fake_close

        actions = [ManagementAction(ManagementActionType.CLOSE_STRUCTURE_FAILURE, reason="opposing CHoCH")]
        events = _run(monitor._apply_management(signal, "BTCUSDT", 90.0, actions))

        assert calls == [(signal.id, "BTCUSDT")]
        assert events[0]["live_position_closed"] is True
        assert signal.status is SignalStatus.CANCELLED

    def test_non_executed_signal_does_not_trigger_live_close(self):
        monitor = SignalMonitor(_FakeDataManager())
        signal = _FakeSignal(executed=False)
        calls = []

        async def fake_close(sig, symbol):
            calls.append((sig.id, symbol))
        monitor._close_live_position_on_structure_failure = fake_close

        actions = [ManagementAction(ManagementActionType.CLOSE_STRUCTURE_FAILURE, reason="opposing CHoCH")]
        events = _run(monitor._apply_management(signal, "BTCUSDT", 90.0, actions))

        assert calls == []
        assert events[0]["live_position_closed"] is False


class TestSyncExchangeStopEndToEnd:
    """Live Execution Safety, Objective 2/6 - one deeper wiring test proving
    the REAL `_sync_exchange_stop` correctly threads credentials through to
    both `BinanceAccountService.get_futures_account` (for the live
    quantity) and `BinanceTradingService.replace_stop_loss` (for the actual
    order swap), and honors that result for the DB-update gate.
    `replace_stop_loss`'s own ordering/retry/idempotency logic is already
    exhaustively covered directly in
    tests/test_binance_trading_service_stop_sync.py - this class only
    proves the monitor calls it with the right arguments in the right
    circumstances."""

    CREDS = {"api_key": "k", "api_secret": "s", "testnet": True}

    def test_successful_end_to_end_sync_returns_true(self):
        monitor = SignalMonitor(_FakeDataManager())
        signal = _FakeSignal(executed=True, executed_environment="testnet")
        account = _FakeFuturesAccount([_FakeFuturesPosition("BTCUSDT", 1.5)])
        replace_calls = []

        async def fake_replace(self, symbol, direction, quantity, new_stop_price, signal_id=None):
            replace_calls.append((symbol, direction, quantity, new_stop_price, signal_id))
            return _FakeStopReplacementResult(success=True)

        with patch.object(binance_credentials, "load_credentials", return_value=self.CREDS), \
             patch.object(BinanceAccountService, "get_futures_account", AsyncMock(return_value=account)), \
             patch.object(BinanceAccountService, "close", AsyncMock(return_value=None)), \
             patch.object(BinanceTradingService, "replace_stop_loss", fake_replace), \
             patch.object(BinanceTradingService, "close", AsyncMock(return_value=None)):
            result = _run(monitor._sync_exchange_stop(signal, "BTCUSDT", 100.0))

        assert result is True
        assert replace_calls == [("BTCUSDT", "LONG", 1.5, 100.0, str(signal.id))]

    def test_no_live_position_returns_false_without_calling_replace(self):
        monitor = SignalMonitor(_FakeDataManager())
        signal = _FakeSignal(executed=True, executed_environment="testnet")
        account = _FakeFuturesAccount([])  # nothing open on this symbol
        replace_calls = []

        async def fake_replace(self, symbol, direction, quantity, new_stop_price, signal_id=None):
            replace_calls.append(True)
            return _FakeStopReplacementResult(success=True)

        with patch.object(binance_credentials, "load_credentials", return_value=self.CREDS), \
             patch.object(BinanceAccountService, "get_futures_account", AsyncMock(return_value=account)), \
             patch.object(BinanceAccountService, "close", AsyncMock(return_value=None)), \
             patch.object(BinanceTradingService, "replace_stop_loss", fake_replace), \
             patch.object(BinanceTradingService, "close", AsyncMock(return_value=None)):
            result = _run(monitor._sync_exchange_stop(signal, "BTCUSDT", 100.0))

        assert result is False
        assert replace_calls == []

    def test_replace_stop_loss_failure_returns_false(self):
        monitor = SignalMonitor(_FakeDataManager())
        signal = _FakeSignal(executed=True, executed_environment="testnet")
        account = _FakeFuturesAccount([_FakeFuturesPosition("BTCUSDT", 1.5)])

        async def fake_replace(self, symbol, direction, quantity, new_stop_price, signal_id=None):
            return _FakeStopReplacementResult(success=False, warning="new stop rejected")

        with patch.object(binance_credentials, "load_credentials", return_value=self.CREDS), \
             patch.object(BinanceAccountService, "get_futures_account", AsyncMock(return_value=account)), \
             patch.object(BinanceAccountService, "close", AsyncMock(return_value=None)), \
             patch.object(BinanceTradingService, "replace_stop_loss", fake_replace), \
             patch.object(BinanceTradingService, "close", AsyncMock(return_value=None)):
            result = _run(monitor._sync_exchange_stop(signal, "BTCUSDT", 100.0))

        assert result is False

    def test_environment_mismatch_returns_false_without_touching_the_account(self):
        monitor = SignalMonitor(_FakeDataManager())
        signal = _FakeSignal(executed=True, executed_environment="mainnet")  # saved creds below are testnet

        def boom(self):
            raise AssertionError("must not fetch the live account when the environment mismatches")

        with patch.object(binance_credentials, "load_credentials", return_value=self.CREDS), \
             patch.object(BinanceAccountService, "get_futures_account", boom):
            result = _run(monitor._sync_exchange_stop(signal, "BTCUSDT", 100.0))

        assert result is False


class TestEngineRunStateGateOnMonitorPoll:
    """Auto Trading Control Panel (2026-07-30) - Pause/Stop must skip the
    ENTIRE poll before it ever opens a database session: real orders
    already resting on Binance for an executed signal are untouched, and no
    DB-backed work runs at all. Proven here by the fact that calling
    `check_active_signals()` completes cleanly with no real database
    available in this offline test environment - if the gate did not
    return before `async with AsyncSessionLocal()`, this would error
    instead of returning None."""

    def test_paused_returns_before_touching_the_database(self):
        monitor = SignalMonitor(_FakeDataManager())
        with patch.object(signal_monitor_module, "get_engine_run_state", return_value="paused"):
            assert _run(monitor.check_active_signals()) is None

    def test_stopped_returns_before_touching_the_database(self):
        monitor = SignalMonitor(_FakeDataManager())
        with patch.object(signal_monitor_module, "get_engine_run_state", return_value="stopped"):
            assert _run(monitor.check_active_signals()) is None

    def test_is_running_property_reflects_lifecycle_flag(self):
        monitor = SignalMonitor(_FakeDataManager())
        assert monitor.is_running is False
        monitor._running = True
        assert monitor.is_running is True
