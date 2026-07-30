from app.services.position_sizing import calculate_position_size


def test_normal_long_position():
    r = calculate_position_size(account_balance=1000.0, entry_price=100.0, stop_loss=95.0, risk_percent=1.0)
    assert r["risk_usd"] == 10.0
    assert abs(r["quantity"] - 2.0) < 1e-9
    assert r["notional_usd"] == 200.0


def test_no_account_balance_returns_none():
    assert calculate_position_size(None, 100, 95, 1.0) is None
    assert calculate_position_size(0, 100, 95, 1.0) is None


def test_zero_risk_distance_returns_none():
    assert calculate_position_size(1000, 100, 100, 1.0) is None


def test_short_direction_uses_absolute_distance():
    r = calculate_position_size(5000.0, 200.0, 210.0, 2.0)
    assert r["risk_usd"] == 100.0
    assert abs(r["quantity"] - 10.0) < 1e-9


def test_profit_and_loss_fields():
    r = calculate_position_size(1000.0, 100.0, 95.0, 1.0, take_profit=108.0)
    assert r["profit_usd"] == 16.0
    assert r["loss_sl_usd"] == r["risk_usd"] == 10.0


def test_missing_tp_levels_omit_profit_fields():
    r = calculate_position_size(1000.0, 100.0, 95.0, 1.0)
    assert "profit_usd" not in r
    assert "loss_sl_usd" in r
