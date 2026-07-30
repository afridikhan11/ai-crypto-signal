"""
Regression suite for app.smc.session_engine (Phase C of the institutional
ICT migration - see ICT_ARCHITECTURE_AUDIT_AND_MIGRATION_PLAN.md).

Covers: session classification (Asian/London/New York), the London/New
York overlap, all four default Kill Zones, off-session ("dead zone")
detection, boundary edges (start inclusive, end exclusive), timezone
handling (naive-as-UTC and tz-aware conversion), the vectorized
`annotate()` path matching `classify_timestamp()` row-by-row, empty-input
handling, and custom window overrides (including midnight-wrapping and
zero-width windows).
"""
import pandas as pd
import pytest

from app.smc.session_engine import (
    DEFAULT_KILL_ZONE_WINDOWS,
    DEFAULT_SESSION_WINDOWS,
    KillZone,
    Session,
    SessionEngine,
    SessionWindow,
)


@pytest.fixture(scope="module")
def engine():
    return SessionEngine()


class TestSessionClassification:
    def test_asian_only_hour(self, engine):
        ctx = engine.classify_timestamp(pd.Timestamp("2026-01-05 02:00:00"))
        assert ctx.active_sessions == (Session.ASIAN,)

    def test_asian_kill_zone(self, engine):
        ctx = engine.classify_timestamp(pd.Timestamp("2026-01-05 02:00:00"))
        assert ctx.kill_zone == KillZone.ASIAN_KILL_ZONE
        assert ctx.in_kill_zone is True

    def test_asian_session_outside_its_kill_zone(self, engine):
        ctx = engine.classify_timestamp(pd.Timestamp("2026-01-05 06:00:00"))
        assert ctx.active_sessions == (Session.ASIAN,)
        assert ctx.kill_zone is None
        assert ctx.in_kill_zone is False

    def test_asian_london_overlap_within_london_kill_zone(self, engine):
        # 08:00 UTC: Asian (00-09) and London (07-16) both active - a real
        # overlap under the default windows, and still inside the London
        # Kill Zone (07-10).
        ctx = engine.classify_timestamp(pd.Timestamp("2026-01-05 08:00:00"))
        assert Session.ASIAN in ctx.active_sessions
        assert Session.LONDON in ctx.active_sessions
        assert ctx.kill_zone == KillZone.LONDON_KILL_ZONE
        assert ctx.is_london_ny_overlap is False

    def test_london_only_kill_zone(self, engine):
        # 09:30 UTC: Asian has ended (00-09), New York hasn't started
        # (12-21) - genuinely London-only, still inside the London Kill
        # Zone (07-10).
        ctx = engine.classify_timestamp(pd.Timestamp("2026-01-05 09:30:00"))
        assert ctx.active_sessions == (Session.LONDON,)
        assert ctx.kill_zone == KillZone.LONDON_KILL_ZONE
        assert ctx.is_london_ny_overlap is False

    def test_london_ny_overlap_and_ny_kill_zone(self, engine):
        ctx = engine.classify_timestamp(pd.Timestamp("2026-01-05 13:00:00"))
        assert Session.LONDON in ctx.active_sessions
        assert Session.NEW_YORK in ctx.active_sessions
        assert ctx.is_london_ny_overlap is True
        assert ctx.kill_zone == KillZone.NEW_YORK_KILL_ZONE

    def test_london_close_kill_zone_still_in_overlap(self, engine):
        ctx = engine.classify_timestamp(pd.Timestamp("2026-01-05 15:00:00"))
        assert ctx.is_london_ny_overlap is True
        assert ctx.kill_zone == KillZone.LONDON_CLOSE_KILL_ZONE

    def test_new_york_only_after_london_closes(self, engine):
        ctx = engine.classify_timestamp(pd.Timestamp("2026-01-05 18:00:00"))
        assert ctx.active_sessions == (Session.NEW_YORK,)
        assert ctx.is_london_ny_overlap is False
        assert ctx.kill_zone is None

    def test_off_session_dead_zone(self, engine):
        ctx = engine.classify_timestamp(pd.Timestamp("2026-01-05 22:30:00"))
        assert ctx.active_sessions == ()
        assert ctx.is_off_session is True
        assert ctx.kill_zone is None
        assert ctx.label == "Off-Session"


class TestBoundaryEdges:
    def test_asian_session_start_inclusive(self, engine):
        ctx = engine.classify_timestamp(pd.Timestamp("2026-01-05 00:00:00"))
        assert Session.ASIAN in ctx.active_sessions

    def test_asian_session_end_exclusive(self, engine):
        ctx = engine.classify_timestamp(pd.Timestamp("2026-01-05 09:00:00"))
        assert Session.ASIAN not in ctx.active_sessions

    def test_london_session_start_inclusive(self, engine):
        ctx = engine.classify_timestamp(pd.Timestamp("2026-01-05 07:00:00"))
        assert Session.LONDON in ctx.active_sessions


class TestTimezoneHandling:
    def test_naive_timestamp_treated_as_utc(self, engine):
        ctx = engine.classify_timestamp(pd.Timestamp("2026-01-05 13:00:00"))
        assert ctx.is_london_ny_overlap is True

    def test_tz_aware_timestamp_converted_to_utc(self, engine):
        # 08:00 US/Eastern (EST, UTC-5 in January) == 13:00 UTC.
        ts = pd.Timestamp("2026-01-05 08:00:00", tz="US/Eastern")
        ctx = engine.classify_timestamp(ts)
        assert ctx.is_london_ny_overlap is True
        assert ctx.kill_zone == KillZone.NEW_YORK_KILL_ZONE


class TestSessionLabel:
    def test_overlap_label(self, engine):
        ctx = engine.classify_timestamp(pd.Timestamp("2026-01-05 13:00:00"))
        assert "Overlap" in ctx.label
        assert "Kill Zone" in ctx.label

    def test_single_session_label(self, engine):
        ctx = engine.classify_timestamp(pd.Timestamp("2026-01-05 06:00:00"))
        assert ctx.label == "Asian"


class TestVectorizedAnnotate:
    def test_annotate_matches_classify_timestamp_row_by_row(self, engine):
        idx = pd.date_range("2026-01-05 00:00:00", periods=96, freq="15min")  # a full UTC day
        df = pd.DataFrame(
            {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=idx
        )
        annotated = engine.annotate(df)
        for ts in idx:
            ctx = engine.classify_timestamp(ts)
            row = annotated.loc[ts]
            assert row["session_asian"] == (Session.ASIAN in ctx.active_sessions)
            assert row["session_london"] == (Session.LONDON in ctx.active_sessions)
            assert row["session_new_york"] == (Session.NEW_YORK in ctx.active_sessions)
            assert row["session_overlap"] == ctx.is_london_ny_overlap
            assert row["in_kill_zone"] == ctx.in_kill_zone
            expected_kz = ctx.kill_zone.value if ctx.kill_zone is not None else None
            assert row["kill_zone"] == expected_kz

    def test_annotate_does_not_mutate_input(self, engine):
        idx = pd.date_range("2026-01-05 00:00:00", periods=10, freq="15min")
        df = pd.DataFrame(
            {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=idx
        )
        original_columns = list(df.columns)
        engine.annotate(df)
        assert list(df.columns) == original_columns

    def test_annotate_empty_dataframe_no_crash(self, engine):
        df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        out = engine.annotate(df)
        assert len(out) == 0
        assert "session_asian" in out.columns
        assert "kill_zone" in out.columns


class TestLatestContext:
    def test_latest_context_returns_context_for_last_row(self, engine):
        idx = pd.date_range("2026-01-05 12:45:00", periods=1, freq="15min")
        df = pd.DataFrame(
            {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=idx
        )
        ctx = engine.latest_context(df)
        assert ctx is not None
        assert ctx.timestamp == idx[-1]

    def test_latest_context_empty_dataframe_returns_none(self, engine):
        df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        assert engine.latest_context(df) is None


class TestSessionWindowPrimitive:
    def test_normal_window_contains(self):
        window = SessionWindow(7, 16)
        assert window.contains(7.0) is True
        assert window.contains(15.99) is True
        assert window.contains(16.0) is False
        assert window.contains(6.99) is False

    def test_zero_width_window_never_active(self):
        window = SessionWindow(5, 5)
        assert window.contains(5.0) is False
        assert window.contains(0.0) is False
        assert window.contains(23.9) is False

    def test_midnight_wrapping_window(self):
        window = SessionWindow(22, 2)
        assert window.contains(23.0) is True
        assert window.contains(0.5) is True
        assert window.contains(1.99) is True
        assert window.contains(2.0) is False
        assert window.contains(12.0) is False


class TestCustomOverrides:
    def test_custom_session_window_overrides_default(self):
        custom = SessionEngine(session_windows={Session.ASIAN: SessionWindow(1, 3)})
        ctx = custom.classify_timestamp(pd.Timestamp("2026-01-05 02:00:00"))
        assert ctx.active_sessions == (Session.ASIAN,)
        ctx_outside = custom.classify_timestamp(pd.Timestamp("2026-01-05 00:00:00"))
        assert ctx_outside.active_sessions == ()

    def test_default_windows_unaffected_by_custom_engine(self):
        # Constructing a custom-override engine must not mutate the shared
        # module-level default dicts (defensive-copy check).
        SessionEngine(session_windows={Session.ASIAN: SessionWindow(1, 3)})
        assert DEFAULT_SESSION_WINDOWS[Session.ASIAN] == SessionWindow(0, 9)
        assert DEFAULT_KILL_ZONE_WINDOWS[KillZone.ASIAN_KILL_ZONE] == SessionWindow(0, 4)
