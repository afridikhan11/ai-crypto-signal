"""The message that reaches the phone, and the identity that stops it
arriving sixty times."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from gold_signals.engulfing import LONG, SHORT, find_engulfing_break
from gold_signals.signal import build_signal

from conftest import long_setup_bars, short_setup_bars

WHEN = datetime(2026, 9, 11, 14, 30, tzinfo=timezone.utc)


def _long(settings):
    return build_signal(find_engulfing_break(long_setup_bars()), settings, WHEN)


def _short(settings):
    return build_signal(find_engulfing_break(short_setup_bars()), settings, WHEN)


class TestPrices:
    def test_a_long_carries_the_owners_numbers(self, settings):
        signal = _long(settings)
        assert signal.direction == LONG
        assert signal.entry == 102.0          # engulfed low 99 + 30 pips
        assert signal.stop == 90.0            # engulfed low 99 - 90 pips
        assert signal.take_profit == 126.0    # 1:2 on $12 of risk
        assert signal.risk_pips == 120

    def test_a_short_is_the_mirror_image(self, settings):
        signal = _short(settings)
        assert signal.direction == SHORT
        assert signal.entry == 108.0
        assert signal.stop == 120.0
        assert signal.take_profit == 84.0
        assert signal.risk_pips == 120

    def test_the_zone_is_reported_low_to_high_whichever_edge_is_the_entry(self, settings):
        long_signal = _long(settings)
        assert long_signal.zone_low == 99.0 and long_signal.zone_high == 102.0
        short_signal = _short(settings)
        assert short_signal.zone_low == 108.0 and short_signal.zone_high == 111.0

    def test_a_zero_risk_configuration_produces_no_signal(self, with_settings):
        """Entry and stop landing on the same price would mean a take-profit
        equal to the entry - a message that says nothing. Refuse it."""
        broken = with_settings(entry_pips=0.0, stop_pips=0.0)
        assert _long(broken) is None

    def test_a_zero_pip_size_produces_no_signal(self, with_settings):
        assert _long(with_settings(pip_size=0.0)) is None

    def test_the_risk_reward_setting_moves_the_target(self, with_settings):
        signal = _long(with_settings(rr=3.0))
        assert signal.take_profit == pytest.approx(138.0)   # 102 + 3 * 12


class TestIdentity:
    def test_the_same_setup_produces_the_same_key(self, settings):
        assert _long(settings).key() == _long(settings).key()

    def test_a_long_and_a_short_are_different_setups(self, settings):
        assert _long(settings).key() != _short(settings).key()

    def test_the_key_ignores_how_many_candles_ago_the_break_was(self, settings):
        """A setup stays the same setup while it ages. If `bars_since_break`
        were in the key, every new candle would re-send the same trade."""
        from conftest import quiet_green_bars

        fresh = _long(settings)
        aged_setup = find_engulfing_break(
            long_setup_bars() + quiet_green_bars(2, start=115.0), max_bars_since_break=3
        )
        aged = build_signal(aged_setup, settings, WHEN)
        assert aged.bars_since_break == 2
        assert aged.key() == fresh.key()

    def test_the_key_survives_float_noise(self, settings):
        """Rounded to the cent, because a float re-read as 98.99999999 would
        otherwise look like a brand new setup and send a duplicate."""
        signal = _long(settings)
        assert signal.key().count("|") == 4
        for part in signal.key().split("|")[2:]:
            assert len(part.split(".")[1]) == 2


class TestMessage:
    def test_a_long_reads_as_a_buy_and_names_every_price(self, settings):
        text = _long(settings).message()
        assert "BUY" in text and "SELL" not in text
        assert "GOLD (XAU/USD)" in text
        assert "102.00" in text      # entry
        assert "90.00" in text       # stop
        assert "126.00" in text      # target
        assert "120 pips" in text
        assert "1:2" in text

    def test_a_short_reads_as_a_sell(self, settings):
        assert "SELL" in _short(settings).message()

    def test_the_message_says_no_order_was_placed(self, settings):
        """This bot cannot trade - there is no exchange client in the project.
        Saying so on every message is the difference between a signal and an
        instruction someone assumes was already acted on."""
        assert "no order has been placed" in _long(settings).message().lower()

    def test_the_message_carries_the_closed_candles_time(self, settings):
        assert "11 Sep 2026 14:30" in _long(settings).message()

    def test_it_says_how_stale_the_break_is(self, settings):
        assert "just closed" in _long(settings).message()

    def test_json_round_trips(self, settings):
        import json

        data = json.loads(_long(settings).as_json())
        assert data["direction"] == LONG
        assert data["entry"] == 102.0
        assert data["candle_time"].startswith("2026-09-11T14:30")
