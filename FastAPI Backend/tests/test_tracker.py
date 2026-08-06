"""Unit tests for the signal lifecycle state machine (pure ``_evaluate``)."""

from types import SimpleNamespace

from app.models.signal import Direction, SignalStatus
from app.scheduler.signal_tracker import SignalTracker


class _DummyDM:
    timeframes = ["5m", "15m", "1h"]


def make_tracker():
    return SignalTracker(_DummyDM(), interval_seconds=15)


def make_signal(direction=Direction.LONG, max_tp=0):
    # entry 100, SL 98, TP1 104, TP2 106, TP3 109 (LONG)
    if direction == Direction.LONG:
        s = SimpleNamespace(
            direction=direction, entry_price=100.0, stop_loss=98.0,
            take_profit_1=104.0, take_profit_2=106.0, take_profit_3=109.0,
            max_tp_hit=max_tp, status=SignalStatus.ACTIVE, closed_at=None,
            risk_reward=2.0, coin=SimpleNamespace(symbol="TESTUSDT"),
        )
    else:
        s = SimpleNamespace(
            direction=direction, entry_price=100.0, stop_loss=102.0,
            take_profit_1=96.0, take_profit_2=94.0, take_profit_3=91.0,
            max_tp_hit=max_tp, status=SignalStatus.ACTIVE, closed_at=None,
            risk_reward=2.0, coin=SimpleNamespace(symbol="TESTUSDT"),
        )
    return s


def test_long_stopped_out():
    t = make_tracker()
    sig = make_signal()
    res = t._evaluate(sig, high=99.0, low=97.5)  # low pierces SL
    assert res == ("STOPPED", True)
    assert sig.status == SignalStatus.STOPPED
    assert sig.closed_at is not None


def test_long_tp1_progress_then_breakeven_close():
    t = make_tracker()
    sig = make_signal()
    # TP1 tagged (non-terminal), stop armed to breakeven.
    res = t._evaluate(sig, high=104.5, low=101.0)
    assert res == ("TP1_PROGRESS", False)
    assert sig.max_tp_hit == 1
    assert sig.status == SignalStatus.ACTIVE
    # Price falls back to breakeven (entry) -> close as TP1_HIT (a win).
    res2 = t._evaluate(sig, high=100.5, low=99.9)
    assert res2 == ("TP1_HIT", True)
    assert sig.status == SignalStatus.TP1_HIT


def test_long_full_tp3():
    t = make_tracker()
    sig = make_signal()
    res = t._evaluate(sig, high=109.5, low=105.0)
    assert res == ("TP3_HIT", True)
    assert sig.status == SignalStatus.TP3_HIT
    assert sig.max_tp_hit == 3


def test_short_stopped_out():
    t = make_tracker()
    sig = make_signal(direction=Direction.SHORT)
    res = t._evaluate(sig, high=102.5, low=101.0)  # high pierces SL
    assert res == ("STOPPED", True)
    assert sig.status == SignalStatus.STOPPED


def test_short_tp2_then_close_at_breakeven():
    t = make_tracker()
    sig = make_signal(direction=Direction.SHORT)
    res = t._evaluate(sig, high=99.0, low=93.5)  # reaches TP2
    assert res == ("TP2_PROGRESS", False)
    assert sig.max_tp_hit == 2
    res2 = t._evaluate(sig, high=100.1, low=99.0)  # back to breakeven
    assert res2 == ("TP2_HIT", True)
    assert sig.status == SignalStatus.TP2_HIT
