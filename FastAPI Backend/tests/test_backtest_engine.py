import pandas as pd

from app.backtest.engine import BacktestEngine, _candles_to_df
from app.services.binance_service import Candle


def _make_engine():
    return BacktestEngine("BTCUSDT", "15m")


def _flat_candles(n=10, price=100.0, base_ts=1_700_000_000_000, step_ms=900_000):
    return [
        Candle(timestamp=base_ts + i * step_ms, open=price, high=price + 0.5, low=price - 0.5, close=price, volume=1.0)
        for i in range(n)
    ]


def test_candles_to_df_sorts_and_dedups():
    rows = [
        Candle(timestamp=3000, open=1, high=2, low=0.5, close=1.5, volume=10),
        Candle(timestamp=1000, open=1, high=2, low=0.5, close=1.5, volume=10),
        Candle(timestamp=2000, open=1, high=2, low=0.5, close=1.5, volume=10),
        Candle(timestamp=2000, open=9, high=9, low=9, close=9, volume=9),  # duplicate timestamp
    ]
    df = _candles_to_df(rows)
    assert len(df) == 3
    assert list(df.index) == sorted(df.index)


def test_candles_to_df_empty():
    df = _candles_to_df([])
    assert df.empty


def test_resolve_trade_long_hits_tp_first():
    engine = _make_engine()
    df = _candles_to_df(_flat_candles(10))
    df.iloc[5, df.columns.get_loc("high")] = 110  # spikes to hit tp

    signal = {"direction": "LONG", "entry": 100.0, "stop_loss": 95.0, "take_profit": 108.0}
    outcome, ts, idx, rr = engine._resolve_trade(df, 0, signal)

    assert outcome == "TP_HIT"
    assert rr == round((108.0 - 100.0) / 5.0, 2)


def test_resolve_trade_short_hits_stop():
    engine = _make_engine()
    df = _candles_to_df(_flat_candles(10))
    df.iloc[3, df.columns.get_loc("high")] = 106  # stop for short at 105

    signal = {"direction": "SHORT", "entry": 100.0, "stop_loss": 105.0, "take_profit": 92.0}
    outcome, ts, idx, rr = engine._resolve_trade(df, 0, signal)

    assert outcome == "STOPPED"
    assert rr == -1.0


def test_resolve_trade_unresolved_when_nothing_hit():
    engine = _make_engine()
    df = _candles_to_df(_flat_candles(5))
    signal = {"direction": "LONG", "entry": 100.0, "stop_loss": 90.0, "take_profit": 200.0}
    outcome, ts, idx, rr = engine._resolve_trade(df, 0, signal)
    assert outcome == "UNRESOLVED"
    assert ts is None and idx is None


def test_summarize_aggregates_correctly():
    trades = [
        {"outcome": "TP_HIT", "realized_rr": 1.6, "confidence": 82},
        {"outcome": "TP_HIT", "realized_rr": 3.0, "confidence": 91},
        {"outcome": "STOPPED", "realized_rr": -1.0, "confidence": 77},
        {"outcome": "UNRESOLVED", "realized_rr": 0.0, "confidence": 80},
    ]
    engine = _make_engine()
    summary = engine._summarize(trades)

    assert summary["total_signals"] == 4
    assert summary["wins"] == 2
    assert summary["losses"] == 1
    assert summary["unresolved"] == 1
    assert summary["win_rate"] == round(2 / 3 * 100, 2)
    assert summary["avg_realized_rr"] == round((1.6 + 3.0 - 1.0) / 3, 2)
    assert summary["by_confidence_bucket"]["80-84"]["total"] == 2
    assert summary["by_confidence_bucket"]["80-84"]["wins"] == 1
