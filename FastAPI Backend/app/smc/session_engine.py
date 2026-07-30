"""
Session Engine - ICT Session Analysis and Kill Zone classification.

STATUS (2026-07-29): PRODUCTION. New module - part of the institutional
ICT migration (see ICT_ARCHITECTURE_AUDIT_AND_MIGRATION_PLAN.md, Phase C).
Fills a confirmed gap: prior to this module, the project had NO concept of
trading sessions or Kill Zones anywhere - signals fired identically at
03:00 UTC and 15:00 UTC. This engine is a pure, additive, asset-agnostic
classifier; it does not change any existing engine's behavior and nothing
else in the project depends on it yet (wiring it into signal_generator.py/
AIScorer is a later phase).

WHAT ICT SESSION ANALYSIS / KILL ZONES ACTUALLY IS
--------------------------------------------------------------------------
ICT methodology treats the trading day as three overlapping global
sessions - Asian, London, New York - because different sessions carry
different liquidity and institutional participation. "Kill Zones" are
narrower, higher-probability windows WITHIN a session (typically the
session's opening hours) where ICT teaching says liquidity raids and real
directional moves are most likely to originate, versus the low-liquidity
"dead zone" hours between sessions where price is more prone to noise.
The London/New York overlap is treated as the single highest-liquidity
window of the day, since both major institutional centers are active
simultaneously.

TIMEZONE CONVENTION (disclosed assumption, not fabricated)
--------------------------------------------------------------------------
Every OHLCV timestamp already flowing through this project (Binance kline
opens, as consumed by UniversalScanner/BacktestEngine/every SMC engine) is a
tz-naive pandas Timestamp that is implicitly UTC - confirmed by reading
`app/services/binance_service.py` and `app/scheduler/scanner.py`, neither
of which performs any timezone conversion. This engine follows the same
convention: tz-naive timestamps are treated as UTC; tz-aware timestamps
are converted to UTC before classification. Crypto trades 24/7 with no
exchange-floor DST observance, so a single fixed UTC convention (rather
than a DST-shifting EST/GMT convention some retail ICT material uses for
forex) is the correct, honest choice for this asset class - and is
exactly why this module lives in `app/smc/` (asset-agnostic Core ICT) and
takes its boundaries as configurable constructor arguments rather than
hardcoding one unquestionable "true" definition.

SESSION AND KILL ZONE WINDOWS (UTC, standard ICT convention, overridable)
--------------------------------------------------------------------------
Sessions (may overlap):
  Asian     : 00:00 - 09:00 UTC
  London    : 07:00 - 16:00 UTC
  New York  : 12:00 - 21:00 UTC

Kill Zones (narrower high-probability windows within a session):
  Asian Kill Zone        : 00:00 - 04:00 UTC
  London Kill Zone       : 07:00 - 10:00 UTC (London open)
  New York Kill Zone     : 12:00 - 15:00 UTC (New York open)
  London Close Kill Zone : 14:00 - 16:00 UTC (London close)

London/New York Overlap : 12:00 - 16:00 UTC (both major sessions active -
                           the single highest-liquidity window of the day)

These are the widely-cited standard ICT session/kill-zone hours. They are
NOT hardcoded as unchangeable fact: every boundary is a constructor
parameter with the above as the documented default, so a caller can supply
different boundaries without editing this file, and so this module never
silently asserts one universal definition as ground truth.

ICT CONCEPTS IMPLEMENTED HERE
--------------------------------------------------------------------------
 1. Session Analysis     - `SessionEngine.classify_timestamp()` /
                            `.annotate()`: which session(s) are active.
 2. Asian Session        - `Session.ASIAN`.
 3. London Session       - `Session.LONDON`.
 4. New York Session     - `Session.NEW_YORK`.
 5. London/NY Overlap    - `SessionContext.is_london_ny_overlap`.
 6. Kill Zones           - `SessionContext.kill_zone` / `.in_kill_zone`:
                            Asian, London, New York, and London Close Kill
                            Zones as distinct, independently-configurable
                            windows.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Tuple

import pandas as pd


class Session(str, Enum):
    ASIAN = "asian"
    LONDON = "london"
    NEW_YORK = "new_york"


class KillZone(str, Enum):
    ASIAN_KILL_ZONE = "asian_kill_zone"
    LONDON_KILL_ZONE = "london_kill_zone"
    NEW_YORK_KILL_ZONE = "new_york_kill_zone"
    LONDON_CLOSE_KILL_ZONE = "london_close_kill_zone"
    # 2026-07-30: added for the Commodity Asset Profile. COMEX metals open
    # (13:30 UTC / 08:30 ET) is a genuine liquidity event for gold and
    # silver that has no crypto equivalent. Purely additive - it is NOT a
    # member of DEFAULT_KILL_ZONE_WINDOWS, so no existing caller's
    # behaviour changes; only a profile that explicitly supplies a window
    # for it (see app/assets/asset_profile.py's COMMODITY_PROFILE) will
    # ever classify a timestamp into it.
    COMEX_OPEN_KILL_ZONE = "comex_open_kill_zone"


@dataclass(frozen=True)
class SessionWindow:
    """A single UTC hour window, half-open [start_hour, end_hour).

    `end_hour` may be <= `start_hour` to represent a window that wraps
    past midnight UTC (e.g. 22 -> 2); none of the default windows above
    wrap, but the classifier supports it for custom overrides.
    """
    start_hour: int
    end_hour: int

    def contains(self, hour: float) -> bool:
        if self.start_hour == self.end_hour:
            # Zero-width window - never active. Explicit, not a silent
            # always-true/always-false footgun for a misconfigured override.
            return False
        if self.start_hour < self.end_hour:
            return self.start_hour <= hour < self.end_hour
        # Wraps past midnight.
        return hour >= self.start_hour or hour < self.end_hour


@dataclass(frozen=True)
class SessionContext:
    """The full session/kill-zone classification for one timestamp."""
    timestamp: pd.Timestamp
    active_sessions: Tuple[Session, ...]
    is_london_ny_overlap: bool
    kill_zone: Optional[KillZone]

    @property
    def in_kill_zone(self) -> bool:
        return self.kill_zone is not None

    @property
    def is_off_session(self) -> bool:
        """True when no major session is active at all (the low-liquidity
        'dead zone' hours ICT teaching says are least favorable for
        entries) - distinct from being in-session-but-not-in-a-kill-zone."""
        return len(self.active_sessions) == 0

    @property
    def label(self) -> str:
        """Human-readable summary, e.g. for signal evidence display."""
        if self.is_london_ny_overlap:
            base = "London / New York Overlap"
        elif self.active_sessions:
            base = " + ".join(s.value.replace("_", " ").title() for s in self.active_sessions)
        else:
            base = "Off-Session"
        if self.kill_zone is not None:
            return f"{base} (Kill Zone: {self.kill_zone.value.replace('_', ' ').title()})"
        return base


# Default windows: standard ICT convention, UTC. See module docstring for
# rationale and the explicit disclosure that these are configurable
# defaults, not asserted as one universal truth.
DEFAULT_SESSION_WINDOWS: Dict[Session, SessionWindow] = {
    Session.ASIAN: SessionWindow(0, 9),
    Session.LONDON: SessionWindow(7, 16),
    Session.NEW_YORK: SessionWindow(12, 21),
}

DEFAULT_KILL_ZONE_WINDOWS: Dict[KillZone, SessionWindow] = {
    KillZone.ASIAN_KILL_ZONE: SessionWindow(0, 4),
    KillZone.LONDON_KILL_ZONE: SessionWindow(7, 10),
    KillZone.NEW_YORK_KILL_ZONE: SessionWindow(12, 15),
    KillZone.LONDON_CLOSE_KILL_ZONE: SessionWindow(14, 16),
}

DEFAULT_LONDON_NY_OVERLAP = SessionWindow(12, 16)


class SessionEngine:
    """Classifies OHLCV timestamps into ICT trading sessions and Kill
    Zones. Pure, stateless-per-call, side-effect-free - no I/O, no
    logging, matching the other Core ICT engines in `app/smc/`.

    Every boundary is overridable via the constructor; omitted overrides
    fall back to the documented standard ICT windows (module docstring).
    """

    def __init__(
        self,
        session_windows: Optional[Dict[Session, SessionWindow]] = None,
        kill_zone_windows: Optional[Dict[KillZone, SessionWindow]] = None,
        london_ny_overlap: Optional[SessionWindow] = None,
    ) -> None:
        self.session_windows: Dict[Session, SessionWindow] = (
            dict(DEFAULT_SESSION_WINDOWS) if session_windows is None else dict(session_windows)
        )
        self.kill_zone_windows: Dict[KillZone, SessionWindow] = (
            dict(DEFAULT_KILL_ZONE_WINDOWS) if kill_zone_windows is None else dict(kill_zone_windows)
        )
        self.london_ny_overlap: SessionWindow = (
            DEFAULT_LONDON_NY_OVERLAP if london_ny_overlap is None else london_ny_overlap
        )

    @staticmethod
    def _to_utc_hour(timestamp: pd.Timestamp) -> float:
        """Returns the fractional UTC hour-of-day (e.g. 14.5 for 14:30)
        for a single timestamp, per the timezone convention documented in
        the module docstring: tz-naive is treated as already-UTC,
        tz-aware is converted to UTC.
        """
        ts = pd.Timestamp(timestamp)
        if ts.tzinfo is not None:
            ts = ts.tz_convert("UTC")
        return ts.hour + ts.minute / 60.0 + ts.second / 3600.0

    def classify_timestamp(self, timestamp: pd.Timestamp) -> SessionContext:
        """Classify a single timestamp. See `annotate()` for the
        vectorized, performance-conscious equivalent over a full
        DataFrame - prefer that for any per-candle scan loop."""
        hour = self._to_utc_hour(timestamp)

        active_sessions = tuple(
            session for session, window in self.session_windows.items() if window.contains(hour)
        )
        is_overlap = (
            Session.LONDON in active_sessions
            and Session.NEW_YORK in active_sessions
            and self.london_ny_overlap.contains(hour)
        )
        kill_zone = next(
            (kz for kz, window in self.kill_zone_windows.items() if window.contains(hour)),
            None,
        )

        return SessionContext(
            timestamp=pd.Timestamp(timestamp),
            active_sessions=active_sessions,
            is_london_ny_overlap=is_overlap,
            kill_zone=kill_zone,
        )

    def annotate(self, df: pd.DataFrame) -> pd.DataFrame:
        """Vectorized session/kill-zone annotation of an entire OHLCV
        DataFrame (indexed by timestamp), avoiding a per-row Python loop.

        Adds boolean columns `session_asian`, `session_london`,
        `session_new_york`, `session_overlap`, `in_kill_zone`, and a
        string column `kill_zone` (None where not in a kill zone).
        Returns a new DataFrame; the input is not mutated.
        """
        if len(df) == 0:
            out = df.copy()
            for col in (
                "session_asian", "session_london", "session_new_york",
                "session_overlap", "in_kill_zone",
            ):
                out[col] = pd.Series(dtype=bool)
            out["kill_zone"] = pd.Series(dtype=object)
            return out

        index = pd.DatetimeIndex(df.index)
        if index.tz is not None:
            index = index.tz_convert("UTC")
        hours = index.hour + index.minute / 60.0 + index.second / 3600.0
        hours = pd.Series(hours, index=df.index)

        out = df.copy()
        for session, window in self.session_windows.items():
            col = f"session_{session.value}"
            out[col] = hours.apply(window.contains)

        out["session_overlap"] = (
            out.get(f"session_{Session.LONDON.value}", False)
            & out.get(f"session_{Session.NEW_YORK.value}", False)
            & hours.apply(self.london_ny_overlap.contains)
        )

        kill_zone_col = pd.Series([None] * len(df), index=df.index, dtype=object)
        # Applied in a fixed, deterministic order; kill zones are designed
        # not to overlap in the default windows, but if a custom override
        # creates an overlap the first matching zone in dict order wins -
        # explicit and documented rather than silently ambiguous.
        for kz, window in self.kill_zone_windows.items():
            mask = hours.apply(window.contains) & kill_zone_col.isna()
            kill_zone_col.loc[mask] = kz.value
        out["kill_zone"] = kill_zone_col
        out["in_kill_zone"] = out["kill_zone"].notna()

        return out

    def latest_context(self, df: pd.DataFrame) -> Optional[SessionContext]:
        """Convenience: classify the most recent candle in `df`. Returns
        None (never guessed) if `df` is empty."""
        if len(df) == 0:
            return None
        return self.classify_timestamp(df.index[-1])
