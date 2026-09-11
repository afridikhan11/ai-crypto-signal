"""One poll's decisions, and the loop's promise to outlive a bad one."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from gold_signals.bot import PollResult, market_is_open, poll_once, run
from gold_signals.candles import CandleFeedError
from gold_signals.engulfing import LONG, Bar
from gold_signals.notify import NotifyError

from conftest import (
    FakeFeed,
    FakeSender,
    long_setup_bars,
    quiet_green_bars,
    to_candles,
)


def _poll(settings, store, candles, sender=None):
    sender = sender or FakeSender()
    feed = FakeFeed(candles)
    return poll_once(settings, feed, sender, store), sender, feed


class TestASetupIsSent:
    def test_a_fresh_long_goes_out(self, settings, store):
        result, sender, _ = _poll(settings, store, to_candles(long_setup_bars()))
        assert result.sent is True
        assert result.signal.direction == LONG
        assert len(sender.sent) == 1
        assert "BUY" in sender.sent[0]

    def test_the_message_carries_the_prices(self, settings, store):
        _, sender, _ = _poll(settings, store, to_candles(long_setup_bars()))
        assert "102.00" in sender.sent[0] and "90.00" in sender.sent[0]

    def test_the_feed_is_asked_for_the_configured_instrument(self, settings, store):
        _, _, feed = _poll(settings, store, to_candles(long_setup_bars()))
        assert feed.calls == [("XAU_USD", "M30", 120)]


class TestTheSameSetupIsNotSentTwice:
    def test_the_second_poll_stays_quiet(self, settings, store):
        candles = to_candles(long_setup_bars())
        first, sender, _ = _poll(settings, store, candles)
        assert first.sent is True

        second, sender2, _ = _poll(settings, store, candles)
        assert second.sent is False
        assert second.reason == "this setup was already sent"
        assert sender2.sent == []

    def test_it_stays_quiet_as_the_setup_ages(self, settings, store):
        """The setup survives several candles. Without this the owner's phone
        would get the same trade every minute until it expired."""
        _poll(settings, store, to_candles(long_setup_bars()))
        older = to_candles(long_setup_bars() + quiet_green_bars(2, start=115.0))
        result, sender, _ = _poll(settings, store, older)
        assert result.sent is False and sender.sent == []

    def test_it_stays_quiet_while_price_HOLDS_above_the_break(self, settings, store):
        """The regression this project's detection was rewritten for. While
        price holds beyond the level, every later candle used to re-qualify as
        the break, giving the setup a new break price - and so a new identity -
        every 30 minutes. The owner would have received the same trade over and
        over."""
        _poll(settings, store, to_candles(long_setup_bars()))
        holding = long_setup_bars() + [
            Bar(open=115.0, high=117.0, low=114.5, close=116.0),
            Bar(open=116.0, high=118.0, low=115.5, close=117.0),
        ]
        result, sender, _ = _poll(settings, store, to_candles(holding))
        assert result.sent is False
        assert sender.sent == []

    def test_a_failed_send_is_NOT_recorded_so_it_retries(self, settings, store):
        """Recording before the send would suppress the retry forever. A
        duplicate message is a nuisance; a missed signal is the product
        failing."""
        candles = to_candles(long_setup_bars())
        angry = FakeSender(error=NotifyError("Twilio is down", status=503))
        with pytest.raises(NotifyError):
            poll_once(settings, FakeFeed(candles), angry, store)

        result, sender, _ = _poll(settings, store, candles)
        assert result.sent is True and len(sender.sent) == 1


class TestNothingToSend:
    def test_no_setup_says_so_rather_than_going_silent(self, settings, store):
        """A bot that prints nothing for six hours is indistinguishable from
        one that died six hours ago."""
        result, sender, _ = _poll(settings, store, to_candles(quiet_green_bars(10, 3900.0)))
        assert result.sent is False
        assert "no engulfing break" in result.reason
        assert sender.sent == []

    def test_an_empty_feed_is_reported_not_crashed(self, settings, store):
        result, _, _ = _poll(settings, store, [])
        assert result.sent is False and "no candles" in result.reason

    def test_too_few_closed_candles_is_reported(self, settings, store):
        result, _, _ = _poll(settings, store, to_candles(long_setup_bars()[:2]))
        assert result.sent is False and "need 3" in result.reason

    def test_a_failed_break_is_not_sent(self, settings, store):
        """Price has traded back through the engulfed candle's low, so the
        break failed and the level is no longer support."""
        candles = to_candles(long_setup_bars(), forming=Bar(99.0, 100.0, 95.0, 96.0))
        result, sender, _ = _poll(settings, store, candles)
        assert result.sent is False
        assert "has failed" in result.reason
        assert sender.sent == []

    def test_a_break_that_is_still_valid_IS_sent(self, settings, store):
        candles = to_candles(long_setup_bars(), forming=Bar(115.0, 116.0, 112.0, 113.0))
        result, _, _ = _poll(settings, store, candles)
        assert result.sent is True

    def test_broken_pip_settings_are_reported_rather_than_sent(self, with_settings, store):
        broken = with_settings(entry_pips=0.0, stop_pips=0.0)
        result, sender, _ = _poll(broken, store, to_candles(long_setup_bars()))
        assert result.sent is False
        assert "PIP_SIZE" in result.reason
        assert sender.sent == []


class TestTheForminCandleIsNeverTraded:
    def test_a_break_that_has_not_closed_yet_is_ignored(self, settings, store):
        """The pattern is complete only on the forming candle. Acting now
        would fire on a close that has not happened."""
        candles = to_candles(long_setup_bars()[:2], forming=long_setup_bars()[2])
        result, sender, _ = _poll(settings, store, candles)
        assert result.sent is False
        assert sender.sent == []

    def test_the_signal_is_stamped_with_the_last_CLOSED_candle(self, settings, store):
        candles = to_candles(long_setup_bars(), forming=Bar(115.0, 116.0, 112.0, 113.0))
        result, _, _ = _poll(settings, store, candles)
        assert result.signal.candle_time == candles[-2].time


class TestMarketHours:
    """Used only to poll less often at the weekend - never to suppress a
    signal."""

    def test_a_weekday_is_open(self):
        assert market_is_open(datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)) is True

    def test_saturday_is_shut(self):
        assert market_is_open(datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)) is False

    def test_friday_evening_is_shut(self):
        assert market_is_open(datetime(2026, 9, 11, 22, 0, tzinfo=timezone.utc)) is False
        assert market_is_open(datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)) is True

    def test_sunday_reopens_in_the_evening(self):
        assert market_is_open(datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)) is False
        assert market_is_open(datetime(2026, 9, 13, 22, 0, tzinfo=timezone.utc)) is True


class TestTheLoop:
    def test_a_broken_configuration_refuses_to_start(self, with_settings):
        blind = with_settings(oanda_token="")
        with pytest.raises(SystemExit) as exc:
            run(blind, feed=FakeFeed([]), sender=FakeSender(), store=None, max_polls=1)
        assert ".env" in str(exc.value)

    def test_a_feed_outage_does_not_kill_the_loop(self, settings, store, monkeypatch):
        monkeypatch.setattr("gold_signals.bot.time.sleep", lambda _: None)
        feed = FakeFeed([], error=CandleFeedError("OANDA returned HTTP 503", status=503))
        run(settings, feed=feed, sender=FakeSender(), store=store, max_polls=3)
        assert len(feed.calls) == 3     # it kept going

    def test_a_delivery_outage_does_not_kill_the_loop(self, settings, store, monkeypatch):
        monkeypatch.setattr("gold_signals.bot.time.sleep", lambda _: None)
        feed = FakeFeed(to_candles(long_setup_bars()))
        angry = FakeSender(error=NotifyError("Twilio is down", status=503))
        run(settings, feed=feed, sender=angry, store=store, max_polls=2)
        assert len(feed.calls) == 2

    def test_an_unexpected_error_does_not_kill_the_loop_either(self, settings, store, monkeypatch):
        """The loop must outlive ANY one poll - the trading bot's seven-hour
        outage was an exception nobody had anticipated."""
        monkeypatch.setattr("gold_signals.bot.time.sleep", lambda _: None)
        feed = FakeFeed([], error=ZeroDivisionError("something nobody predicted"))
        run(settings, feed=feed, sender=FakeSender(), store=store, max_polls=2)
        assert len(feed.calls) == 2

    def test_it_sleeps_longer_when_the_market_is_shut(self, settings, store, monkeypatch):
        slept = []
        monkeypatch.setattr("gold_signals.bot.time.sleep", slept.append)
        monkeypatch.setattr("gold_signals.bot.market_is_open", lambda: False)
        run(settings, feed=FakeFeed([]), sender=FakeSender(), store=store, max_polls=2)
        assert slept == [settings.idle_poll_seconds]

    def test_the_last_poll_does_not_sleep_before_exiting(self, settings, store, monkeypatch):
        slept = []
        monkeypatch.setattr("gold_signals.bot.time.sleep", slept.append)
        run(settings, feed=FakeFeed([]), sender=FakeSender(), store=store, max_polls=1)
        assert slept == []


class TestPollResult:
    def test_a_quiet_poll_still_carries_a_reason(self):
        assert PollResult(False, "no engulfing break").reason
