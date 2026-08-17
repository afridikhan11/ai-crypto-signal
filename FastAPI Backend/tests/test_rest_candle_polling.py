"""
REST candle polling - the websocket-independent data feed. Verifies it uses
the last CLOSED candle (never the still-forming one), fires the on-candle
callback exactly once when a NEW candle closes (not on the first observation,
which the startup scan already covered), and keeps the cached series ordered.
"""
import asyncio

from app.services.binance_service import BinanceDataManager, Candle, SymbolData


def _run(coro):
    return asyncio.run(coro)


def _candle(ts, close):
    return Candle(timestamp=ts, open=close, high=close, low=close, close=close, volume=1.0)


class TestAddCandleOrdering:
    def test_newer_candle_appends(self):
        sd = SymbolData("x")
        sd.add_candle("15m", _candle(1000, 1.0))
        sd.add_candle("15m", _candle(2000, 2.0))
        assert [c.timestamp for c in sd.timeframes["15m"]] == [1000, 2000]

    def test_same_timestamp_replaces_in_place(self):
        # A forming candle later arrives completed at the same open time.
        sd = SymbolData("x")
        sd.add_candle("15m", _candle(1000, 1.0))       # forming
        sd.add_candle("15m", _candle(1000, 9.9))       # completed
        assert len(sd.timeframes["15m"]) == 1
        assert sd.timeframes["15m"][-1].close == 9.9

    def test_older_candle_is_ignored(self):
        sd = SymbolData("x")
        sd.add_candle("15m", _candle(2000, 2.0))
        sd.add_candle("15m", _candle(1000, 1.0))       # older -> ignored
        assert [c.timestamp for c in sd.timeframes["15m"]] == [2000]


class TestRestPolling:
    def _dm(self, fetch, symbols=("BTCUSDT",), tfs=("15m",)):
        dm = BinanceDataManager(list(symbols), timeframes=list(tfs))
        dm._rest_running = True
        dm.fetch_historical_klines = fetch
        return dm

    def test_uses_closed_candle_not_the_forming_one(self):
        closed, forming = _candle(1000, 100.0), _candle(2000, 111.0)

        async def fetch(symbol, interval, limit=500):
            return [closed, forming]

        dm = self._dm(fetch)
        _run(dm._poll_klines_once())
        # The closed candle (ts 1000) is cached; the forming one is never used.
        assert dm.data["btcusdt"].timeframes["15m"][-1].timestamp == 1000

    def test_first_observation_does_not_fire(self):
        fired = []

        async def cb(s, i, c):
            fired.append(c[-1].timestamp)

        async def fetch(symbol, interval, limit=500):
            return [_candle(1000, 100.0), _candle(2000, 1.0)]

        dm = self._dm(fetch)
        dm.callback = cb
        _run(dm._poll_klines_once())   # startup already scanned this candle
        assert fired == []

    def test_fires_once_when_a_new_candle_closes(self):
        fired = []

        async def cb(s, i, c):
            fired.append(c[-1].timestamp)

        state = {"closed": _candle(1000, 100.0)}

        async def fetch(symbol, interval, limit=500):
            return [state["closed"], _candle(9999, 1.0)]

        dm = self._dm(fetch)
        dm.callback = cb
        _run(dm._poll_klines_once())          # first observation -> no fire
        state["closed"] = _candle(1900, 105.0)  # a new 15m candle has closed
        _run(dm._poll_klines_once())          # -> fires for the new candle
        _run(dm._poll_klines_once())          # same candle again -> no re-fire
        assert fired == [1900]

    def test_survives_a_single_symbol_fetch_error(self):
        seen = []

        async def cb(s, i, c):
            seen.append(s)

        state = {"aaa": _candle(1000, 1.0), "bbb": _candle(1000, 1.0)}

        async def fetch(symbol, interval, limit=500):
            if symbol == "aaa" and state["aaa"] is None:
                raise RuntimeError("boom")
            return [state[symbol], _candle(5000, 1.0)]

        dm = self._dm(fetch, symbols=("AAA", "BBB"))
        dm.callback = cb
        _run(dm._poll_klines_once())              # first observation for both
        state["aaa"] = None                        # AAA now errors
        state["bbb"] = _candle(2000, 1.0)          # BBB gets a new closed candle
        _run(dm._poll_klines_once())               # AAA raises, BBB still fires
        assert seen == ["bbb"]
