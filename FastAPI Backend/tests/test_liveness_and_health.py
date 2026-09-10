"""
Liveness beats and the /health contract.

WHY (2026-09-10): the suite passed 1159 tests, the deploy reported success,
`docker compose ps` said `Up`, and the bot had not monitored a signal for
seven hours. Nothing in the system could tell the difference between "the
process is alive" and "the process is doing its job", so nothing noticed.

These tests pin the distinction:
  - a loop that RAISES every cycle must not look alive;
  - a loop that is deliberately PAUSED must;
  - the database probe must touch a mapper, because `SELECT 1` reported
    "ok" throughout the outage.
"""
import pytest

from app.core import liveness


@pytest.fixture(autouse=True)
def _clean_beats():
    liveness.reset()
    yield
    liveness.reset()


class TestBeats:
    def test_a_component_that_never_beat_is_unhealthy_once_the_grace_passes(self):
        # Startup grace: a loop that has not run YET is not a failure.
        assert liveness.component_status(liveness.MONITOR, grace_seconds=1e9)["healthy"] is True
        # ...but once the process has been up long enough, it is.
        stale = liveness.component_status(liveness.MONITOR, grace_seconds=0.0)
        assert stale["healthy"] is False
        assert stale["detail"] == "never ran"

    def test_a_fresh_beat_is_healthy(self):
        liveness.beat(liveness.MONITOR)
        status = liveness.component_status(liveness.MONITOR)
        assert status["healthy"] is True
        assert status["seconds_since_last_run"] < 1.0

    def test_a_stale_beat_is_unhealthy_and_says_how_stale(self, monkeypatch):
        liveness.beat(liveness.MONITOR)
        limit = liveness.STALE_AFTER_SECONDS[liveness.MONITOR]
        real_time = liveness.time.time
        monkeypatch.setattr(liveness.time, "time", lambda: real_time() + limit + 60)

        status = liveness.component_status(liveness.MONITOR)
        assert status["healthy"] is False
        assert "last ran" in status["detail"]

    def test_components_are_independent(self):
        liveness.beat(liveness.SCANNER)
        assert liveness.component_status(liveness.SCANNER)["healthy"] is True
        assert liveness.component_status(liveness.MONITOR, grace_seconds=0.0)["healthy"] is False

    def test_reporting_never_raises_for_an_unknown_component(self):
        status = liveness.component_status("nothing_by_that_name", grace_seconds=0.0)
        assert status["healthy"] is False


class TestTheLoopsBeatInTheRightPlace:
    """Where the beat is placed IS the guarantee."""

    def test_monitor_beats_only_after_a_poll_that_COMPLETED(self):
        import inspect

        from app.scheduler.signal_monitor import SignalMonitor

        src = inspect.getsource(SignalMonitor.start)
        beat_at = src.index("liveness.beat")
        except_at = src.index("except Exception")
        assert beat_at < except_at, (
            "The beat must be inside the try, after check_active_signals(). "
            "This loop catches everything and sleeps, so a poll that raises "
            "every cycle keeps the task alive while doing no work - the exact "
            "shape of the 2026-09-10 outage."
        )

    def test_scanner_beats_after_analysis_and_when_deliberately_paused(self):
        import inspect

        from app.scheduler.universal_scanner import UniversalScanner

        src = inspect.getsource(UniversalScanner.on_new_candle)
        assert src.count("liveness.beat") == 2, (
            "Two beats are required: one after analyze_symbol completes, and "
            "one on the pause gate - a paused scanner is working correctly by "
            "doing nothing and must not be reported dead."
        )
        assert src.index("liveness.beat") > src.index("get_engine_run_state"), (
            "The pause-gate beat must come after the gate decides."
        )


class TestHealthProbesTheORMNotJustTheSocket:
    def test_the_database_probe_selects_from_a_mapped_entity(self):
        import inspect

        from app.api.v1.endpoints import health

        src = inspect.getsource(health)
        assert "select(Signal.id)" in src, (
            "`SELECT 1` reported 'ok' for all seven hours of the outage - raw "
            "text never resolves a mapper. The probe must select from a MAPPED "
            "entity so it forces configure_mappers()."
        )

    def test_unhealthy_returns_503_so_a_machine_can_act(self):
        import inspect

        from app.api.v1.endpoints import health

        src = inspect.getsource(health.health_check)
        assert "503" in src, (
            "Health must fail with a status code, not just a JSON field - "
            "Docker's healthcheck and the smoke script both read the code."
        )

    def test_the_original_response_keys_are_preserved(self):
        import inspect

        from app.api.v1.endpoints import health

        src = inspect.getsource(health.health_check)
        for key in ('"status"', '"database"', '"redis"', '"binance"'):
            assert key in src, f"{key} was part of the response before; removing it breaks consumers"
