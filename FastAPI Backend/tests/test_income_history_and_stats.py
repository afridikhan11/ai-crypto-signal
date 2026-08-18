"""
Real-money reconciliation plumbing for signal_stats:
  1. `BinanceAccountService.get_income_history` forwards the new
     start_time / end_time / limit / incomeType params to the signed GET and
     parses the ledger rows (so the weekly-window paging in the stats script
     can actually bound its queries).
  2. `signal_stats._aggregate_income` folds a signed income ledger into
     correct totals + per-symbol net, and NET excludes capital movements
     (transfers/bonuses), which are not trading performance.
"""
import asyncio
import importlib.util
import os

import httpx

from app.services.binance_account_service import BinanceAccountService


def _run(coro):
    return asyncio.run(coro)


def _load_stats_module():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "signal_stats.py")
    spec = importlib.util.spec_from_file_location("signal_stats_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Income:
    def __init__(self, income_type, income, symbol):
        self.income_type = income_type
        self.income = income
        self.symbol = symbol


# ======================================================================
# 1. get_income_history param forwarding + parsing
# ======================================================================
class TestGetIncomeHistory:
    def _svc(self, captured, rows):
        svc = BinanceAccountService(api_key="k", api_secret="s", testnet=True)

        async def fake_signed_get(base_url, path, params=None):
            captured["base_url"] = base_url
            captured["path"] = path
            captured["params"] = dict(params or {})
            return httpx.Response(status_code=200, json=rows)

        svc._signed_get = fake_signed_get
        return svc

    def test_forwards_time_bounds_and_type(self):
        captured = {}
        svc = self._svc(captured, [])
        _run(svc.get_income_history(
            limit=1000, income_type="COMMISSION", start_time=111, end_time=222,
        ))
        p = captured["params"]
        assert p["limit"] == 1000
        assert p["incomeType"] == "COMMISSION"
        assert p["startTime"] == 111
        assert p["endTime"] == 222
        assert captured["path"] == "/fapi/v1/income"

    def test_omits_bounds_when_not_given(self):
        captured = {}
        svc = self._svc(captured, [])
        _run(svc.get_income_history(limit=50))
        p = captured["params"]
        assert "startTime" not in p and "endTime" not in p and "incomeType" not in p

    def test_parses_rows(self):
        rows = [
            {"symbol": "SOLUSDT", "incomeType": "REALIZED_PNL", "income": "-44.08", "asset": "USDT", "time": 5},
            {"symbol": "SOLUSDT", "incomeType": "COMMISSION", "income": "-5.19", "asset": "USDT", "time": 4},
        ]
        svc = self._svc({}, rows)
        items = _run(svc.get_income_history(start_time=1, end_time=9))
        assert {i.income_type for i in items} == {"REALIZED_PNL", "COMMISSION"}
        assert any(abs(i.income + 44.08) < 1e-9 for i in items)


# ======================================================================
# 2. _aggregate_income - the pure fold used by the stats report
# ======================================================================
class TestAggregateIncome:
    def setup_method(self):
        self.stats = _load_stats_module()

    def test_totals_and_net_and_per_symbol(self):
        items = [
            _Income("REALIZED_PNL", -44.08, "SOLUSDT"),
            _Income("COMMISSION", -5.19, "SOLUSDT"),
            _Income("FUNDING_FEE", 0.12, "SOLUSDT"),
            _Income("REALIZED_PNL", 2.88, "NEARUSDT"),
            _Income("COMMISSION", -0.40, "NEARUSDT"),
        ]
        agg = self.stats._aggregate_income(items)
        assert round(agg["totals"]["REALIZED_PNL"], 2) == -41.20
        assert round(agg["totals"]["COMMISSION"], 2) == -5.59
        assert round(agg["totals"]["FUNDING_FEE"], 2) == 0.12
        assert round(agg["net"], 2) == -46.67  # realized + fees + funding
        assert round(sum(agg["per_symbol"]["SOLUSDT"].values()), 2) == -49.15

    def test_net_excludes_transfers_and_bonuses(self):
        items = [
            _Income("REALIZED_PNL", 10.0, "BTCUSDT"),
            _Income("TRANSFER", 1000.0, None),      # capital in - NOT performance
            _Income("WELCOME_BONUS", 500.0, None),  # ditto
        ]
        agg = self.stats._aggregate_income(items)
        assert round(agg["net"], 2) == 10.0             # only the realized P/L
        assert round(agg["totals"]["OTHER"], 2) == 1500.0  # tracked, but apart
