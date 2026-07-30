# Temporary Testnet Validation Change — Confidence Threshold

**Date:** 2026-07-30
**Scope:** Configuration only. No ICT logic, AI scoring algorithm, Universal Scanner, Risk Engine, Trade Management, or position sizing was modified.

---

## 1. Files Modified

- `app/ai/calibration_profiles.py` — added one new constant, `TESTNET_MIN_CONFIDENCE = 60`, next to the existing `min_confidence` documentation (the project's existing centralized home for confidence-threshold config, per each `CalibrationProfile`).
- `app/scheduler/universal_scanner.py` — added one method, `UniversalScanner._apply_testnet_confidence_override()`, called once at the end of `__init__` (right after the existing per-symbol `SignalGenerator` construction). Uses `dataclasses.replace()` — the exact same mechanism `app/backtest/engine.py` already uses for its own `min_confidence` override — to swap each generator's `.profile` for a copy with only `min_confidence` changed.

No other file was touched. `60` appears in exactly one place (`TESTNET_MIN_CONFIDENCE`); every call site references that constant, never a literal `60`.

---

## 2. Previous Values

Every asset class's `CalibrationProfile.min_confidence` (crypto, gold, silver, oil, forex — `app/ai/calibration_profiles.py`) was `85`, applied uniformly regardless of environment.

---

## 3. New Values

- **TESTNET:** `60` — applied automatically, at scanner startup, only when the currently saved Binance credentials are flagged `testnet: true`.
- **MAINNET:** `85` — unchanged.
- **BACKTEST:** `85` — unchanged.

Every `CalibrationProfile`'s own stored `min_confidence` is still `85` in every case — the override is applied as a runtime copy on the live scanner's generators only, never as a change to the underlying profile definitions.

---

## 4. How TESTNET / BACKTEST / MAINNET Select Their Threshold

This project already has exactly one source of truth for "which environment is this": `app/services/binance_credentials.load_credentials()`, the same read `account.py`, `dashboard.py`, and `trading_control.py` already use to label the environment as `"testnet"` or `"mainnet"` everywhere else in the app. This change reuses that, rather than introducing a second environment concept:

- **Live scanning (TESTNET/MAINNET):** `UniversalScanner.__init__` builds one `SignalGenerator` per symbol as it always has, then calls the new `_apply_testnet_confidence_override()`. That method reads `binance_credentials.load_credentials()` once. If credentials exist and `testnet` is `true`, every generator's `min_confidence` is replaced with `TESTNET_MIN_CONFIDENCE` (60). If there are no saved credentials, or `testnet` is `false` (Mainnet), the method is a no-op and every generator keeps its normal profile default (85). This is evaluated once, at scanner startup — matching how the scanner already resolves each symbol's profile once at construction rather than per candle.
- **BACKTEST:** `BacktestEngine.run()` (`app/backtest/engine.py`) never calls `UniversalScanner` at all — it builds its own, separate `SignalGenerator` instance directly and always uses that profile's real `min_confidence` (85), or an existing, unrelated `min_confidence` request parameter it already supported before this change. There is no code path connecting this override to backtesting.

---

## 5. Confirmation

Only configuration was changed — one new constant and one new read-only startup check that swaps a single numeric field via the exact override mechanism the backtest engine already used. No ICT engine, the AI scorer, the Universal Scanner's scan pipeline, the Risk Engine, Trade Management, or position sizing was modified. Verified directly, not just asserted:

- All four scenarios were exercised against the real code: no saved credentials → 85; Mainnet credentials → 85; Testnet credentials → 60; and a standalone `SignalGenerator` built the same way `BacktestEngine` builds one → 85 even while Testnet credentials are active elsewhere in the same process — confirming Backtest is fully isolated from this change.
- Every modified/related file (`universal_scanner.py`, `calibration_profiles.py`, `backtest/engine.py`, `strategy/signal_generator.py`) compiles cleanly.
- The full offline regression suite was re-run: 625/627 passing, 1 pre-existing self-skip (unrelated, documented in the prior validation report), and one pre-existing failure (`test_universal_scanner.py::test_triggers_on_primary_timeframe`) that is **not caused by this change** — it's a test that doesn't mock the engine's run-state gate and was already dependent on this sandbox's real, current `data/trading_settings.json` (which presently has `engine_run_state: "stopped"`, real persisted control-panel state, left untouched here since it isn't this task's to change). Confirmed by isolating the exact same code path with the run-state explicitly mocked to `"running"`: the scan proceeds normally and the confidence override is present and correct.
