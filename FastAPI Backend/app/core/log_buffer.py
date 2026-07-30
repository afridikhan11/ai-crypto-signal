"""
In-memory log ring buffer - Auto Trading Control Panel (2026-07-30).

Loguru (see app/core/logging.py) already writes every log line - including
every execution/risk/stop-sync decision from the Live Execution Safety
phase - to stdout and to a rotating file on disk. Neither is reachable from
the WPF app, so the Trading Logs panel had no real data source. This adds
ONE more loguru sink: a bounded in-memory deque, appended to on every log
record exactly like the existing sinks, read by `GET /trading/logs`
(app/api/v1/endpoints/trading_control.py). Purely additive - the stdout and
rotating-file sinks in app/core/logging.py are untouched.

Bounded at MAX_ENTRIES so a long-running process can never leak memory here;
oldest entries are dropped first (a `collections.deque(maxlen=...)` does
this automatically, discarding silently - there is nothing to warn about,
this is expected, ordinary ring-buffer behavior, not a failure).
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Deque, Dict, List

MAX_ENTRIES = 500

_buffer: Deque[Dict[str, object]] = deque(maxlen=MAX_ENTRIES)
_installed = False

# Loguru's own standard numeric severities (stable, documented values - see
# loguru's `_defaults.py`) - hardcoded here rather than calling
# `logger.level(name).no` so `get_recent_logs()`'s level filter has no
# runtime dependency on loguru beyond the plain string already stored in
# each captured record.
_LEVEL_SEVERITY = {"TRACE": 5, "DEBUG": 10, "INFO": 20, "SUCCESS": 25, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}


def _capture(message) -> None:
    """Loguru sink callback - `message` is a `loguru.Message` (str subclass)
    with a `.record` dict attached. Never raises: a malformed record must
    never take down whatever call triggered the log line that produced it."""
    try:
        record = message.record
        _buffer.append({
            "time": record["time"].astimezone(timezone.utc).isoformat(),
            "level": record["level"].name,
            "logger": record["name"],
            "message": record["message"],
        })
    except Exception:
        # Deliberately swallowed - a broken log CAPTURE must never raise
        # into the real log call site that triggered it.
        pass


def install() -> None:
    """Registers the ring-buffer sink. Call once at import time of
    app/core/logging.py, alongside the existing stdout/file sinks.
    Idempotent - a second call is a no-op, so this is also safe to call
    defensively from a test or any other module that wants to guarantee
    the sink exists without risking a duplicate registration (which would
    otherwise append every log line twice)."""
    global _installed
    if _installed:
        return
    from loguru import logger
    logger.add(_capture, level="DEBUG", format="{message}")
    _installed = True


def get_recent_logs(limit: int = 200, level: str | None = None) -> List[Dict[str, object]]:
    """Most-recent-first, optionally filtered to a minimum level name (e.g.
    'WARNING' returns WARNING and above, using loguru's own standard
    numeric severities - see `_LEVEL_SEVERITY` above). An unrecognized
    `level` value is treated as "no filter" rather than raising, since this
    is a display/visualization endpoint, not a validation gate. `limit` is
    capped in the endpoint, not here, but defensively re-applied."""
    entries = list(_buffer)
    if level:
        min_severity = _LEVEL_SEVERITY.get(level.upper())
        if min_severity is not None:
            entries = [e for e in entries if _LEVEL_SEVERITY.get(str(e["level"]).upper(), 0) >= min_severity]

    entries.reverse()
    return entries[:limit]
