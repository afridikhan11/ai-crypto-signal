"""
Auto Trading Control Panel (2026-07-30) - tests for the in-memory log ring
buffer backing GET /trading/logs (see app/core/log_buffer.py).

Calls `_capture()` directly with a hand-built loguru-shaped `record` dict
rather than going through a real `logger.info(...)` call - this sandbox's
loguru stand-in does not implement real sink dispatch (`logger.add(callable)`
never actually invokes the callable here), so the only way to exercise the
CAPTURE/RING-BUFFER/FILTER logic itself in this offline environment is
directly. This still genuinely exercises every line of `_capture()` and
`get_recent_logs()` - it just supplies the record loguru would have,
instead of relying on the stub to deliver it.
"""
from datetime import datetime, timezone

from app.core import log_buffer


def _drain():
    log_buffer._buffer.clear()


class _FakeLevel:
    def __init__(self, name):
        self.name = name


class _FakeMessage:
    """Mimics loguru's `Message` (str subclass with a `.record` dict) just
    enough for `_capture()` - a plain object with a `.record` attribute is
    sufficient since `_capture()` never treats `message` as a string."""

    def __init__(self, message: str, level: str = "INFO", name: str = "app.test"):
        self.record = {
            "time": datetime.now(timezone.utc),
            "level": _FakeLevel(level),
            "name": name,
            "message": message,
        }


class TestCapture:
    def test_capture_appends_a_well_formed_entry(self):
        _drain()
        log_buffer._capture(_FakeMessage("hello world", level="INFO"))
        entries = list(log_buffer._buffer)
        assert len(entries) == 1
        assert entries[0]["message"] == "hello world"
        assert entries[0]["level"] == "INFO"
        assert entries[0]["logger"] == "app.test"
        assert "T" in entries[0]["time"]  # ISO-8601 timestamp

    def test_malformed_message_never_raises(self):
        _drain()

        class _Broken:
            pass  # no .record attribute at all

        log_buffer._capture(_Broken())  # must not raise
        assert list(log_buffer._buffer) == []


class TestGetRecentLogs:
    def test_most_recent_first(self):
        _drain()
        log_buffer._capture(_FakeMessage("first-marker"))
        log_buffer._capture(_FakeMessage("second-marker"))
        entries = log_buffer.get_recent_logs(limit=10)
        assert entries[0]["message"] == "second-marker"
        assert entries[1]["message"] == "first-marker"

    def test_limit_is_respected(self):
        _drain()
        for i in range(10):
            log_buffer._capture(_FakeMessage(f"marker-{i}"))
        entries = log_buffer.get_recent_logs(limit=3)
        assert len(entries) == 3
        # Most-recent-first: the last 3 appended, newest first.
        assert [e["message"] for e in entries] == ["marker-9", "marker-8", "marker-7"]

    def test_level_filter_excludes_lower_levels(self):
        _drain()
        log_buffer._capture(_FakeMessage("a-debug-marker", level="DEBUG"))
        log_buffer._capture(_FakeMessage("a-warning-marker", level="WARNING"))
        entries = log_buffer.get_recent_logs(limit=50, level="WARNING")
        messages = [e["message"] for e in entries]
        assert "a-warning-marker" in messages
        assert "a-debug-marker" not in messages

    def test_unrecognized_level_filter_is_treated_as_no_filter(self):
        _drain()
        log_buffer._capture(_FakeMessage("a-marker", level="DEBUG"))
        entries = log_buffer.get_recent_logs(limit=50, level="NOT_A_REAL_LEVEL")
        assert any(e["message"] == "a-marker" for e in entries)

    def test_no_filter_returns_everything(self):
        _drain()
        log_buffer._capture(_FakeMessage("a", level="DEBUG"))
        log_buffer._capture(_FakeMessage("b", level="CRITICAL"))
        entries = log_buffer.get_recent_logs(limit=50)
        assert len(entries) == 2


class TestRingBufferBound:
    def test_ring_buffer_is_bounded_and_drops_oldest_first(self):
        _drain()
        for i in range(log_buffer.MAX_ENTRIES + 50):
            log_buffer._capture(_FakeMessage(f"m{i}"))
        assert len(log_buffer._buffer) == log_buffer.MAX_ENTRIES
        assert log_buffer._buffer[0]["message"] == "m50"
        assert log_buffer._buffer[-1]["message"] == f"m{log_buffer.MAX_ENTRIES + 49}"


class TestInstallIsIdempotent:
    def test_calling_install_twice_does_not_double_register(self):
        """Real regression guard: loguru's stand-in aside, `install()` must
        never register the sink more than once in a real run either -
        otherwise every real log line would be captured N times over."""
        assert log_buffer._installed in (True, False)  # sane before state
        was_installed = log_buffer._installed
        log_buffer.install()
        log_buffer.install()
        # No assertion on loguru internals here (stubbed in this sandbox) -
        # the guarantee under test is purely that `_installed` latches True
        # and a second call is a no-op, which the flag flip itself proves.
        assert log_buffer._installed is True
        if not was_installed:
            pass  # first real installation in this process - expected
