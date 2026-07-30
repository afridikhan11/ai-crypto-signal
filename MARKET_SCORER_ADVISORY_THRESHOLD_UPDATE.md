# market_scorer.py Advisory Threshold — Made Environment-Aware

**Date:** 2026-07-30
**Scope:** Configuration only, in the one file confirmed purely advisory. No scoring logic, feature weights, calculations, AI algorithm, execution, or signal-generation code was touched.

---

## Why

The confidence-threshold audit found one real hardcoded duplicate: `app/services/market_scorer.py`'s `MIN_CONFIDENCE = 85`, used only by the read-only "scan any pair on demand" report (Token Scanner, AI Trading Agent, Portfolio Intelligence exposure enrichment — confirmed via its own docstring: "read-only, non-persisting... never touches the Signal/Coin tables and never places or queues any order"). It never gates a real signal or an order, but while Testnet mode has the live gate at 60, this tool was still comparing against a stale 85.

## What Changed

- **`app/ai/calibration_profiles.py`** — added one new function, `effective_min_confidence(base_min_confidence)`. Returns `TESTNET_MIN_CONFIDENCE` (60) when saved Binance credentials are pointed at Testnet, otherwise returns whatever base value the caller passed in, unchanged. Reuses the exact same `binance_credentials.load_credentials()` check `UniversalScanner`'s live gate already uses — no new environment-detection logic, no new hardcoded `60`.
- **`app/services/market_scorer.py`** — `MIN_CONFIDENCE = 85` still exists as the Mainnet/Backtest base value (unchanged number, unchanged meaning). The three places that actually USE it (`passes_confidence`, and the two "reasons" messages) now read `live_min_confidence = effective_min_confidence(MIN_CONFIDENCE)` instead, resolved fresh on every scan.

**Nothing else changed.** `signal_generator.py` (the live gate), `universal_scanner.py` (the execution/scanning pipeline), the AI scorer's feature weights, and every calculation in `market_scorer.py` (entry/stop/take-profit, RR, HTF checks, liquidity/order-block checks) are byte-for-byte untouched.

## Result

| Environment | Advisory threshold shown |
|---|---|
| TESTNET (saved credentials flagged testnet) | 60 |
| BACKTEST (never calls this function) | 85 |
| MAINNET / no saved credentials | 85 |

The Token Scanner / AI Agent / Portfolio Intelligence "would this pass the live bar" verdict now always matches whatever the live scanner is actually gating on at that moment.

## Verification

- `effective_min_confidence()` tested directly against all three scenarios (no credentials, Mainnet, Testnet) — returns 85, 85, 60 respectively.
- Confirmed against the real saved credentials already on disk in this environment (`testnet: true`): `effective_min_confidence(85)` correctly returns `60`.
- Both modified files compile cleanly and import with no circularity.
- Full regression suite re-run: 625/627 passing — identical result to before this change, including the one pre-existing, already-diagnosed failure unrelated to any of this work (a test that depends on the real, currently-stopped `engine_run_state` on disk, not on anything touched here).
