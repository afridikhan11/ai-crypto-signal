"""
Auto Trading Control Panel (2026-07-30) - tests for
app.services.trading_control_service: close_all_positions, cancel_all_orders,
kill_switch.

These three functions are pure orchestration over already-tested primitives
(`BinanceTradingService.close_position`/`.cancel_order`/`.get_all_open_orders`,
`BinanceAccountService.get_futures_account`) - these tests prove the
LOOPING/aggregation/failure-isolation logic added here, not the primitives
themselves (already covered in tests/test_binance_trading_service_stop_sync.py).
Real classes are used with their network-touching methods monkeypatched -
no real HTTP call is made, matching the pattern already established in
tests/test_signal_monitor.py::TestSyncExchangeStopEndToEnd.
"""
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.services import binance_credentials, trading_control_service
from app.services.binance_account_service import BinanceAccountService
from app.services.binance_trading_service import (
    BinanceTradingError,
    BinanceTradingService,
    OrderResult,
)
from app.services import trading_settings


def _run(coro):
    return asyncio.run(coro)


class _FakePosition:
    def __init__(self, symbol, position_amt):
        self.symbol = symbol
        self.position_amt = position_amt


class _FakeFuturesAccount:
    def __init__(self, open_positions):
        self.open_positions = open_positions


def _order(symbol, order_id=1):
    return OrderResult(order_id=order_id, symbol=symbol, side="SELL", order_type="MARKET", status="FILLED", quantity=1.0)


class TestCloseAllPositions:
    def test_no_credentials_returns_zero_attempted(self):
        with patch.object(binance_credentials, "build_service_from_saved", side_effect=FileNotFoundError("no creds saved")):
            result = _run(trading_control_service.close_all_positions())
        assert result.attempted == 0
        assert result.succeeded == 0
        assert "no creds saved" in (result.message or "")

    def test_no_open_positions_reports_nothing_to_do(self):
        account = _FakeFuturesAccount([])
        fake_account_service = BinanceAccountService(api_key="k", api_secret="s", testnet=True)
        with patch.object(binance_credentials, "build_service_from_saved", return_value=fake_account_service), \
             patch.object(BinanceAccountService, "get_futures_account", AsyncMock(return_value=account)), \
             patch.object(BinanceAccountService, "close", AsyncMock(return_value=None)):
            result = _run(trading_control_service.close_all_positions())
        assert result.attempted == 0
        assert result.succeeded == 0
        assert "No open positions" in result.message

    def test_closes_every_open_position_and_reports_success(self):
        account = _FakeFuturesAccount([_FakePosition("BTCUSDT", 1.5), _FakePosition("ETHUSDT", -2.0)])
        fake_account_service = BinanceAccountService(api_key="k", api_secret="s", testnet=True)
        fake_trading_service = BinanceTradingService(api_key="k", api_secret="s", testnet=True)
        close_calls = []

        async def fake_close_position(self, symbol, position_amt):
            close_calls.append((symbol, position_amt))
            return _order(symbol, order_id=len(close_calls))

        with patch.object(binance_credentials, "build_service_from_saved", return_value=fake_account_service), \
             patch.object(binance_credentials, "build_trading_service_from_saved", return_value=fake_trading_service), \
             patch.object(BinanceAccountService, "get_futures_account", AsyncMock(return_value=account)), \
             patch.object(BinanceAccountService, "close", AsyncMock(return_value=None)), \
             patch.object(BinanceTradingService, "close_position", fake_close_position), \
             patch.object(BinanceTradingService, "close", AsyncMock(return_value=None)):
            result = _run(trading_control_service.close_all_positions())

        assert result.attempted == 2
        assert result.succeeded == 2
        assert close_calls == [("BTCUSDT", 1.5), ("ETHUSDT", -2.0)]
        assert all(item.success for item in result.items)

    def test_zero_amount_positions_are_skipped(self):
        account = _FakeFuturesAccount([_FakePosition("BTCUSDT", 0.0), _FakePosition("ETHUSDT", 3.0)])
        fake_account_service = BinanceAccountService(api_key="k", api_secret="s", testnet=True)
        fake_trading_service = BinanceTradingService(api_key="k", api_secret="s", testnet=True)
        close_calls = []

        async def fake_close_position(self, symbol, position_amt):
            close_calls.append(symbol)
            return _order(symbol)

        with patch.object(binance_credentials, "build_service_from_saved", return_value=fake_account_service), \
             patch.object(binance_credentials, "build_trading_service_from_saved", return_value=fake_trading_service), \
             patch.object(BinanceAccountService, "get_futures_account", AsyncMock(return_value=account)), \
             patch.object(BinanceAccountService, "close", AsyncMock(return_value=None)), \
             patch.object(BinanceTradingService, "close_position", fake_close_position), \
             patch.object(BinanceTradingService, "close", AsyncMock(return_value=None)):
            result = _run(trading_control_service.close_all_positions())

        assert close_calls == ["ETHUSDT"]
        assert result.attempted == 1

    def test_one_failure_never_stops_the_rest(self):
        """Never-silently-continue discipline: a failure on one symbol must
        not prevent the loop from attempting every other open position, and
        the failure must be visible in the per-item results."""
        account = _FakeFuturesAccount([_FakePosition("BTCUSDT", 1.0), _FakePosition("ETHUSDT", 1.0)])
        fake_account_service = BinanceAccountService(api_key="k", api_secret="s", testnet=True)
        fake_trading_service = BinanceTradingService(api_key="k", api_secret="s", testnet=True)

        async def fake_close_position(self, symbol, position_amt):
            if symbol == "BTCUSDT":
                raise BinanceTradingError("simulated rejection")
            return _order(symbol)

        with patch.object(binance_credentials, "build_service_from_saved", return_value=fake_account_service), \
             patch.object(binance_credentials, "build_trading_service_from_saved", return_value=fake_trading_service), \
             patch.object(BinanceAccountService, "get_futures_account", AsyncMock(return_value=account)), \
             patch.object(BinanceAccountService, "close", AsyncMock(return_value=None)), \
             patch.object(BinanceTradingService, "close_position", fake_close_position), \
             patch.object(BinanceTradingService, "close", AsyncMock(return_value=None)):
            result = _run(trading_control_service.close_all_positions())

        assert result.attempted == 2
        assert result.succeeded == 1
        by_symbol = {item.symbol: item for item in result.items}
        assert by_symbol["BTCUSDT"].success is False
        assert by_symbol["ETHUSDT"].success is True
        assert "1 position(s) failed" in result.message


class TestCancelAllOrders:
    def test_no_credentials_returns_zero_attempted(self):
        with patch.object(binance_credentials, "build_trading_service_from_saved", side_effect=FileNotFoundError("no creds")):
            result = _run(trading_control_service.cancel_all_orders())
        assert result.attempted == 0
        assert "no creds" in (result.message or "")

    def test_no_open_orders_reports_nothing_to_do(self):
        fake_trading_service = BinanceTradingService(api_key="k", api_secret="s", testnet=True)
        with patch.object(binance_credentials, "build_trading_service_from_saved", return_value=fake_trading_service), \
             patch.object(BinanceTradingService, "get_all_open_orders", AsyncMock(return_value=[])), \
             patch.object(BinanceTradingService, "close", AsyncMock(return_value=None)):
            result = _run(trading_control_service.cancel_all_orders())
        assert result.attempted == 0
        assert "No pending orders" in result.message

    def test_cancels_every_open_order(self):
        fake_trading_service = BinanceTradingService(api_key="k", api_secret="s", testnet=True)
        open_orders = [
            {"symbol": "BTCUSDT", "orderId": 111},
            {"symbol": "ETHUSDT", "orderId": 222},
        ]
        cancel_calls = []

        async def fake_cancel_order(self, symbol, order_id):
            cancel_calls.append((symbol, order_id))

        with patch.object(binance_credentials, "build_trading_service_from_saved", return_value=fake_trading_service), \
             patch.object(BinanceTradingService, "get_all_open_orders", AsyncMock(return_value=open_orders)), \
             patch.object(BinanceTradingService, "cancel_order", fake_cancel_order), \
             patch.object(BinanceTradingService, "close", AsyncMock(return_value=None)):
            result = _run(trading_control_service.cancel_all_orders())

        assert result.attempted == 2
        assert result.succeeded == 2
        assert cancel_calls == [("BTCUSDT", 111), ("ETHUSDT", 222)]

    def test_cannot_read_open_orders_returns_zero_attempted(self):
        fake_trading_service = BinanceTradingService(api_key="k", api_secret="s", testnet=True)

        async def boom(self):
            raise BinanceTradingError("openOrders lookup failed")

        with patch.object(binance_credentials, "build_trading_service_from_saved", return_value=fake_trading_service), \
             patch.object(BinanceTradingService, "get_all_open_orders", boom), \
             patch.object(BinanceTradingService, "close", AsyncMock(return_value=None)):
            result = _run(trading_control_service.cancel_all_orders())

        assert result.attempted == 0
        assert "Could not read open orders" in result.message

    def test_one_cancel_failure_never_stops_the_rest(self):
        fake_trading_service = BinanceTradingService(api_key="k", api_secret="s", testnet=True)
        open_orders = [{"symbol": "BTCUSDT", "orderId": 111}, {"symbol": "ETHUSDT", "orderId": 222}]

        async def fake_cancel_order(self, symbol, order_id):
            if symbol == "BTCUSDT":
                raise BinanceTradingError("simulated cancel failure")

        with patch.object(binance_credentials, "build_trading_service_from_saved", return_value=fake_trading_service), \
             patch.object(BinanceTradingService, "get_all_open_orders", AsyncMock(return_value=open_orders)), \
             patch.object(BinanceTradingService, "cancel_order", fake_cancel_order), \
             patch.object(BinanceTradingService, "close", AsyncMock(return_value=None)):
            result = _run(trading_control_service.cancel_all_orders())

        assert result.attempted == 2
        assert result.succeeded == 1
        by_symbol = {item.symbol: item for item in result.items}
        assert by_symbol["BTCUSDT"].success is False
        assert by_symbol["ETHUSDT"].success is True


class TestKillSwitch:
    def test_disables_auto_trading_and_stops_engine_before_bulk_actions(self):
        path = Path(tempfile.mkdtemp()) / "trading_settings.json"
        account = _FakeFuturesAccount([])
        fake_account_service = BinanceAccountService(api_key="k", api_secret="s", testnet=True)
        fake_trading_service = BinanceTradingService(api_key="k", api_secret="s", testnet=True)

        with patch.object(trading_settings, "SETTINGS_FILE", path), \
             patch.object(binance_credentials, "build_service_from_saved", return_value=fake_account_service), \
             patch.object(binance_credentials, "build_trading_service_from_saved", return_value=fake_trading_service), \
             patch.object(BinanceAccountService, "get_futures_account", AsyncMock(return_value=account)), \
             patch.object(BinanceAccountService, "close", AsyncMock(return_value=None)), \
             patch.object(BinanceTradingService, "get_all_open_orders", AsyncMock(return_value=[])), \
             patch.object(BinanceTradingService, "close", AsyncMock(return_value=None)):
            result = _run(trading_control_service.kill_switch())

            # Asserted INSIDE the patch context - trading_settings.SETTINGS_FILE
            # is only pointed at the temp path for the duration of this block.
            assert trading_settings.get_auto_trading_enabled() is False
            assert trading_settings.get_engine_run_state() == "stopped"

        assert result.auto_trading_disabled is True
        assert result.engine_stopped is True
        assert result.close_positions.action == "close_all_positions"
        assert result.cancel_orders.action == "cancel_all_orders"

    def test_composes_close_all_and_cancel_all_results_verbatim(self):
        """Kill switch must not re-implement close/cancel - it delegates to
        the exact same functions used by the individual actions."""
        path = Path(tempfile.mkdtemp()) / "trading_settings.json"
        account = _FakeFuturesAccount([_FakePosition("BTCUSDT", 1.0)])
        fake_account_service = BinanceAccountService(api_key="k", api_secret="s", testnet=True)
        fake_trading_service = BinanceTradingService(api_key="k", api_secret="s", testnet=True)
        open_orders = [{"symbol": "ETHUSDT", "orderId": 999}]

        async def fake_close_position(self, symbol, position_amt):
            return _order(symbol)

        async def fake_cancel_order(self, symbol, order_id):
            return None

        with patch.object(trading_settings, "SETTINGS_FILE", path), \
             patch.object(binance_credentials, "build_service_from_saved", return_value=fake_account_service), \
             patch.object(binance_credentials, "build_trading_service_from_saved", return_value=fake_trading_service), \
             patch.object(BinanceAccountService, "get_futures_account", AsyncMock(return_value=account)), \
             patch.object(BinanceAccountService, "close", AsyncMock(return_value=None)), \
             patch.object(BinanceTradingService, "close_position", fake_close_position), \
             patch.object(BinanceTradingService, "get_all_open_orders", AsyncMock(return_value=open_orders)), \
             patch.object(BinanceTradingService, "cancel_order", fake_cancel_order), \
             patch.object(BinanceTradingService, "close", AsyncMock(return_value=None)):
            result = _run(trading_control_service.kill_switch())

        assert result.close_positions.attempted == 1
        assert result.close_positions.succeeded == 1
        assert result.cancel_orders.attempted == 1
        assert result.cancel_orders.succeeded == 1
