"""What has already been sent, so the same setup is not sent every minute.

A setup stays alive for several candles, and the bot polls every minute.
Without this table the owner's phone would receive the same signal sixty
times before the setup expired - which is how a signal bot gets muted, and a
muted bot is a bot that does nothing.

SQLite, one file, no server. It survives a restart, which is the whole
requirement: a bot that forgets on restart re-sends everything the moment it
is redeployed.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS sent_signals (
    key           TEXT PRIMARY KEY,
    sent_at       TEXT NOT NULL,
    instrument    TEXT NOT NULL,
    direction     TEXT NOT NULL,
    entry         REAL NOT NULL,
    stop          REAL NOT NULL,
    take_profit   REAL NOT NULL,
    candle_time   TEXT NOT NULL,
    payload       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sent_signals_sent_at ON sent_signals (sent_at);
"""


class SignalStore:
    def __init__(self, path: str = "signals.db") -> None:
        self.path = path
        if path != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def already_sent(self, key: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sent_signals WHERE key = ?", (key,)
        ).fetchone()
        return row is not None

    def record(self, signal, now: Optional[datetime] = None) -> None:
        """Write the send down.

        Called AFTER the message leaves, never before: a send that fails must
        be retried on the next poll, and a row written first would suppress
        it forever. The cost is that a crash in the instant between the send
        and this write sends one duplicate - a duplicate message being far
        better than a missed signal.

        INSERT OR IGNORE so a race or a retry cannot raise.
        """
        stamp = (now or datetime.now(timezone.utc)).isoformat()
        self._conn.execute(
            """
            INSERT OR IGNORE INTO sent_signals
                (key, sent_at, instrument, direction, entry, stop, take_profit,
                 candle_time, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal.key(),
                stamp,
                signal.instrument,
                signal.direction,
                signal.entry,
                signal.stop,
                signal.take_profit,
                signal.candle_time.isoformat(),
                signal.as_json(),
            ),
        )
        self._conn.commit()

    def recent(self, limit: int = 20) -> List[sqlite3.Row]:
        return list(self._conn.execute(
            "SELECT * FROM sent_signals ORDER BY sent_at DESC LIMIT ?", (limit,)
        ).fetchall())

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM sent_signals").fetchone()[0])

    def close(self) -> None:
        self._conn.close()
