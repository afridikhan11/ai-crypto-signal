"""
Auto Trading Control Panel (2026-07-30) - tests for the two new persisted
switches in app.services.trading_settings: `auto_trading_enabled` and
`engine_run_state`. Each test swaps `SETTINGS_FILE` for a fresh temp path
(restored after, via a `with` block) so these tests never touch the real
data/trading_settings.json on disk - same read-modify-write module every
other trading setting already uses, just a new key.
"""
import tempfile
from pathlib import Path
from unittest.mock import patch

from app.services import trading_settings


def _temp_settings_path():
    return Path(tempfile.mkdtemp()) / "trading_settings.json"


class TestAutoTradingEnabled:
    def test_defaults_to_true(self):
        with patch.object(trading_settings, "SETTINGS_FILE", _temp_settings_path()):
            assert trading_settings.get_auto_trading_enabled() is True

    def test_set_false_then_get_returns_false(self):
        with patch.object(trading_settings, "SETTINGS_FILE", _temp_settings_path()):
            trading_settings.set_auto_trading_enabled(False)
            assert trading_settings.get_auto_trading_enabled() is False

    def test_set_true_after_false_returns_true(self):
        with patch.object(trading_settings, "SETTINGS_FILE", _temp_settings_path()):
            trading_settings.set_auto_trading_enabled(False)
            trading_settings.set_auto_trading_enabled(True)
            assert trading_settings.get_auto_trading_enabled() is True

    def test_non_boolean_rejected(self):
        with patch.object(trading_settings, "SETTINGS_FILE", _temp_settings_path()):
            try:
                trading_settings.set_auto_trading_enabled("yes")  # type: ignore[arg-type]
                assert False, "expected ValueError"
            except ValueError:
                pass

    def test_does_not_clobber_sibling_setting(self):
        """Read-modify-write: setting auto_trading_enabled must not drop an
        already-saved risk_percent (or any other sibling key) - the exact
        regression trading_settings.py's own module docstring calls out."""
        path = _temp_settings_path()
        with patch.object(trading_settings, "SETTINGS_FILE", path):
            trading_settings.set_risk_percent(3.5)
            trading_settings.set_auto_trading_enabled(False)
            assert trading_settings.get_risk_percent() == 3.5
            assert trading_settings.get_auto_trading_enabled() is False


class TestEngineRunState:
    def test_defaults_to_running(self):
        with patch.object(trading_settings, "SETTINGS_FILE", _temp_settings_path()):
            assert trading_settings.get_engine_run_state() == "running"

    def test_set_paused_then_get_returns_paused(self):
        with patch.object(trading_settings, "SETTINGS_FILE", _temp_settings_path()):
            trading_settings.set_engine_run_state("paused")
            assert trading_settings.get_engine_run_state() == "paused"

    def test_set_stopped_then_get_returns_stopped(self):
        with patch.object(trading_settings, "SETTINGS_FILE", _temp_settings_path()):
            trading_settings.set_engine_run_state("stopped")
            assert trading_settings.get_engine_run_state() == "stopped"

    def test_invalid_state_rejected(self):
        with patch.object(trading_settings, "SETTINGS_FILE", _temp_settings_path()):
            try:
                trading_settings.set_engine_run_state("kaboom")
                assert False, "expected ValueError"
            except ValueError:
                pass

    def test_corrupted_saved_value_falls_back_to_default(self):
        path = _temp_settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"engine_run_state": 12345}')
        with patch.object(trading_settings, "SETTINGS_FILE", path):
            assert trading_settings.get_engine_run_state() == "running"

    def test_transition_running_paused_running_round_trips(self):
        with patch.object(trading_settings, "SETTINGS_FILE", _temp_settings_path()):
            assert trading_settings.get_engine_run_state() == "running"
            trading_settings.set_engine_run_state("paused")
            assert trading_settings.get_engine_run_state() == "paused"
            trading_settings.set_engine_run_state("running")
            assert trading_settings.get_engine_run_state() == "running"
