"""
REST candle polling - the websocket-independent data feed. Verifies it uses
the last CLOSED candle (never the still-forming one), caches it, and drives
the on-candle callback exactly once per new candle - the behaviour that keeps
scanning alive when the websocket stream delivers no data.
"""
import asyncio

from app.services.binance_service import BinanceDataManager, Candle


def _run(coro):
    return asyncio.run(coro)


def _candle(ts, close):
    return Candle(timestamp=ts, open=close, high=close, low=close, close=close, volume=1.0)


def test_poll_uses_last_closed_candle_and_fires_callback():
    dm = BinanceDataManager(["BTCUSDT"], timeframes=["15m"])
    dm._rest_running = True
    fired = []

    async def cb(symbol, interval, candles):
        fired.append((symbol, interval, candles[-1].timestamp, candles[-1].close))

    dm.callback = cb

    closed = _candle(1000, 100.0)
    forming = _candle(2000, 111.0)  # still-forming: must be ignored

    async def fake_fetch(symbol, interval, limit=500):
        return [closed, forming]

    dm.fetch_historical_klines = fake_fetch
    _run(dm._poll_klines_once())

    # Cached + callback used the CLOSED candle (ts 1000), never the forming one.
    assert dm.data["btcusdt"].timeframes["15m"][-1].timestamp == 1000
    assert fired == [("btcusdt", "15m", 1000, 100.0)]


def test_poll_does_not_refire_on_an_unchanged_candle():
    dm = BinanceDataManager(["BTCUSDT"], timeframes=["15m"])
    dm._rest_running = True
    fired = []

    async def cb(symbol, interval, candles):
        fired.append(candles[-1].timestamp)

    dm.callback = cb
    closed = _candle(1000, 100.0)

    async def fake_fetch(symbol, interval, limit=500):
        return [closed, _candle(2000, 111.0)]

    dm.fetch_historical_klines = fake_fetch
    _run(dm._poll_klines_once())
    _run(dm._poll_klines_once())  # same closed candle -> no second callback

    assert fired == [1000]


def test_poll_fires_again_when_a_new_candle_closes():
    dm = BinanceDataManager(["BTCUSDT"], timeframes=["15m"])
    dm._rest_running = True
    fired = []

    async def cb(symbol, interval, candles):
        fired.append(candles[-1].timestamp)

    dm.callback = cb
    state = {"closed": _candle(1000, 100.0)}

    async def fake_fetch(symbol, interval, limit=500):
        return [state["closed"], _candle(9999, 1.0)]

    dm.fetch_historical_klines = fake_fetch
    _run(dm._poll_klines_once())
    state["closed"] = _candle(1900, 105.0)  # a new 15m candle has closed
    _run(dm._poll_klines_once())

    assert fired == [1000, 1900]


def test_poll_survives_a_single_symbol_fetch_error():
    dm = BinanceDataManager(["AAA", "BBB"], timeframes=["15m"])
    dm._rest_running = True
    fired = []

    async def cb(symbol, interval, candles):
        fired.append(symbol)

    dm.callback = cb

    async def fake_fetch(symbol, interval, limit=500):
        if symbol == "aaa":
            raise RuntimeError("boom")
        return [_candle(1000, 100.0), _candle(2000, 1.0)]

    dm.fetch_historical_klines = fake_fetch
    _run(dm._poll_klines_once())  # AAA raises, BBB must still be processed

    assert fired == ["bbb"]
