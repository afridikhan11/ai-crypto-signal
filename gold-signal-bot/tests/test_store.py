"""The table that stops one setup arriving sixty times."""
from __future__ import annotations

from datetime import datetime, timezone

from gold_signals.engulfing import find_engulfing_break
from gold_signals.signal import build_signal
from gold_signals.store import SignalStore

from conftest import long_setup_bars, short_setup_bars

WHEN = datetime(2026, 9, 11, 14, 30, tzinfo=timezone.utc)


def _signal(settings, bars=None):
    return build_signal(find_engulfing_break(bars or long_setup_bars()), settings, WHEN)


class TestDeduplication:
    def test_an_unseen_setup_has_not_been_sent(self, store, settings):
        assert store.already_sent(_signal(settings).key()) is False

    def test_a_recorded_setup_has(self, store, settings):
        signal = _signal(settings)
        store.record(signal)
        assert store.already_sent(signal.key()) is True

    def test_a_different_setup_is_unaffected(self, store, settings):
        store.record(_signal(settings))
        other = _signal(settings, short_setup_bars())
        assert store.already_sent(other.key()) is False

    def test_recording_twice_does_not_raise(self, store, settings):
        """A retry or a race must never crash the loop; INSERT OR IGNORE."""
        signal = _signal(settings)
        store.record(signal)
        store.record(signal)
        assert store.count() == 1


class TestItSurvivesARestart:
    def test_a_reopened_database_still_remembers(self, tmp_path, settings):
        """A bot that forgets on restart re-sends everything the moment it is
        redeployed - which on a 30 minute chart means every open setup at
        once."""
        path = str(tmp_path / "signals.db")
        signal = _signal(settings)

        first = SignalStore(path)
        first.record(signal)
        first.close()

        second = SignalStore(path)
        assert second.already_sent(signal.key()) is True
        second.close()

    def test_the_directory_is_created_if_it_does_not_exist(self, tmp_path):
        path = str(tmp_path / "data" / "nested" / "signals.db")
        store = SignalStore(path)
        assert store.count() == 0
        store.close()


class TestTheLedger:
    def test_recent_returns_what_was_sent_newest_first(self, store, settings):
        early = _signal(settings)
        late = _signal(settings, short_setup_bars())
        store.record(early, now=datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc))
        store.record(late, now=datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc))

        rows = store.recent()
        assert [r["direction"] for r in rows] == [late.direction, early.direction]

    def test_the_full_signal_is_kept_as_json(self, store, settings):
        import json

        signal = _signal(settings)
        store.record(signal)
        row = store.recent()[0]
        assert json.loads(row["payload"])["take_profit"] == signal.take_profit

    def test_recent_respects_its_limit(self, store, settings):
        store.record(_signal(settings))
        store.record(_signal(settings, short_setup_bars()))
        assert len(store.recent(limit=1)) == 1
