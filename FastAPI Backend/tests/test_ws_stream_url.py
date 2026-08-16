"""
The market-data websocket URL must be Binance's COMBINED multi-stream form
(`/stream?streams=a/b/c`). Building it off the raw `/ws` base produced
`/ws/stream?streams=...`, which Binance accepts but sends NO data on - the
silent zombie that froze all live re-scanning. This locks the format.
"""
from app.services.binance_service import BinanceDataManager


def test_combined_url_uses_stream_endpoint_not_ws():
    url = BinanceDataManager._build_combined_stream_url(["btcusdt", "ethusdt"], ["15m", "1h"])
    assert url.startswith("wss://fstream.binance.com/stream?streams=")
    # The raw single-stream `/ws/` path must NOT appear - that was the bug.
    assert "/ws/stream" not in url
    assert "/ws?" not in url


def test_combined_url_lists_every_symbol_timeframe_pair():
    url = BinanceDataManager._build_combined_stream_url(["btcusdt", "ethusdt"], ["15m", "1h"])
    _, _, streams = url.partition("?streams=")
    assert set(streams.split("/")) == {
        "btcusdt@kline_15m", "btcusdt@kline_1h",
        "ethusdt@kline_15m", "ethusdt@kline_1h",
    }


def test_combined_base_constant_is_correct():
    assert BinanceDataManager.COMBINED_WS_URL == "wss://fstream.binance.com/stream"
