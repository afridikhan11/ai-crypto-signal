# Production Readiness Audit — 2026-07-31

Scope: security, trading correctness, capital safety, performance, reliability. Cosmetic findings excluded.
Every item below is backed by a specific file and line. Findings I could not confirm from code are not listed.

**Suite after fixes: 766 passing, 0 failed, 0 errors, 1 skipped** (44 files).

---

## 1. CRITICAL

### C-1. Binance API credentials are committed to git and decryptable with public inputs

**Evidence**

```
$ git ls-files --error-unmatch "FastAPI Backend/data/binance_credentials.enc"
FastAPI Backend/data/binance_credentials.enc          <-- TRACKED

$ git log --oneline --all -- "FastAPI Backend/data/binance_credentials.enc"
810edaf Complete Risk Engine v2.0.0 (Phases 1-4)      <-- committed here
```

`FastAPI Backend/.gitignore` contained only `.venv/`, `__pycache__/`, `*.pyc`, `logs/`, `.env` — `data/` was never excluded.

The file is Fernet-encrypted, but every input to the key derivation is public:

| Input | Where | Public? |
|---|---|---|
| PBKDF2 salt | `api_key_cipher.py:41` `b"ai-crypto-signal-binance-account-service-v1"` | yes, literal in repo |
| Iterations | `api_key_cipher.py:42` `390_000` | yes, literal in repo |
| Password | `Settings.secret_key` | **yes — still `change_me_in_production`** |

The placeholder `SECRET_KEY` is confirmed by your own startup log:

> `STARTUP CHECK: SECRET_KEY is still the placeholder default ('change_me_in_production')`

Anyone with a copy of the repository can reconstruct the Fernet key and decrypt the file. The encryption design is sound; it is defeated entirely by the unset secret. Currently Testnet keys, so the immediate blast radius is a Testnet account — but the same path stores Mainnet keys, and **git history retains the blob even after deletion**.

**Fixed automatically**

- `.gitignore` now excludes `data/binance_credentials.enc` and `data/*.enc`
- `git rm --cached` executed — untracked, file left on disk
- `.dockerignore` created (see H-2) so it is no longer copied into images
- `tests/test_production_hardening.py::TestSecretsAreNotDistributable` locks all of it

**You must still do these three things — I cannot:**

1. **Rotate the Binance API keys.** Revoke the current pair in the Binance account and issue new ones. Untracking does not un-publish what has already been committed.
2. **Purge git history** (`git filter-repo --path "FastAPI Backend/data/binance_credentials.enc" --invert-paths`, then force-push). Required if this repo has ever been pushed anywhere.
3. **Generate a real `SECRET_KEY`** with `scripts/generate_secret_key.py` and re-save credentials through the app so they are re-encrypted under it. Changing the key invalidates the existing file by design.

Do these in order: rotating first means a leaked history no longer matters.

---

## 2. HIGH

### H-1. A dead market-data stream produces signals from frozen prices, silently

**Evidence** — `app/scheduler/universal_scanner.py:210-211` (before fix):

```python
asyncio.create_task(self.data_manager.start_websocket())
asyncio.create_task(self.data_manager.start_liquidation_stream())
```

Both results discarded. Two independent failure modes:

1. **Garbage collection.** asyncio holds only a *weak* reference to a running task. With no strong reference anywhere, the task may be collected mid-flight.
2. **Swallowed exceptions.** If either coroutine raises above its own retry loop, the exception goes into an unretrieved task result and nothing logs it.

This is not merely a reliability issue. `app/services/binance_service.py:418` documents that `get_dataframe()` *"Returns the last good cached frame (even if stale)"*. So a dead candle stream leaves the scanner running normally against **frozen prices** — generating entries, stops and targets from stale data, with no error anywhere in the logs. That is a trading-correctness and capital-safety failure.

**Fixed automatically.** Tasks are now held on `self._ws_task` / `self._liquidation_task` (initialised in `__init__` so `stop()` is safe before `start()`), each gets a `done_callback` that logs death loudly and distinguishes cancellation from failure, and `stop()` cancels them before tearing down the data manager. Locked by `TestMarketDataTasksAreSupervised` (4 tests).

### H-2. `COPY . .` baked `.git`, `.env` and the credentials file into every image layer

**Evidence** — no `.dockerignore` existed anywhere in the repo; `Dockerfile:11` is `COPY . .`.

Consequences: every image contained the full git history (so deleting a secret from the working tree would *not* remove it from images built earlier), the `.env` file, and `data/binance_credentials.enc`. Image layers are distributed to registries and recoverable with `docker save`.

**Fixed automatically.** `.dockerignore` created excluding `.env*`, `data/*.enc`, `.git`, `logs/`, `__pycache__/`, `.venv/`, `tests/`. A guard test asserts it does **not** exclude `app/`, `scripts/`, `alembic/`, `requirements.txt` or `alembic.ini` — excluding `scripts/` would remove the ENTRYPOINT and the failure would only surface at deploy time.

### H-3. Production redeploys silently reset the trading control plane

**Evidence** — `docker-compose.prod.yml` mounted only `app_logs:/app/logs`. `data/trading_settings.json` holds `engine_run_state`, auto-trading on/off and `entry_mode`, and is **written at runtime** (mtime moves during operation). With no volume covering `/app/data`, those writes went to the container's ephemeral filesystem.

Every redeploy therefore reverted the control plane to whatever was baked into the image — including **re-enabling Auto Trading after a deliberate stop**, and discarding a deliberate pause. A kill switch that a routine redeploy silently undoes is not a kill switch.

**Fixed automatically.** `./data:/app/data` added to `docker-compose.prod.yml`. This also gives C-1 its correct production posture: credentials are supplied by the operator on the host rather than shipped in the image. Locked by `TestControlPlaneStatePersists`.

---

## 3. MEDIUM

### M-1. Testnet confidence override is still active — **not fixed, deliberately**

`app/scheduler/universal_scanner.py:146` `_apply_testnet_confidence_override()` lowers the AI minimum confidence from 85 to 60 when Testnet credentials are detected. Confirmed live in your log:

> `TESTNET credentials detected - AI minimum confidence threshold temporarily lowered to 60%`

Every signal in your run — SOL 67, AVAX 71, DOT 71, LINK 65 — is **below the 85 threshold** and exists only because of this override. Mainnet and backtest are correctly unaffected.

**I did not revert this.** It is a deliberate change you requested for Testnet validation, and reverting it would stop signal generation mid-validation. It is flagged here because it is scoped to an environment check rather than to a date or a task, so nothing will ever remove it automatically. Remove it when Testnet validation concludes.

### M-2. `DEBUG=true` puts full SQL, including trade parameters, into the log stream

`app/core/database.py:8` `echo=settings.debug`. Your `.env` has `DEBUG=true`, which is why the startup log carries every `INSERT INTO signals ...` with complete bound parameters — entry, stop, take-profit, confidence and the full score breakdown.

Correctly configured for production already: `.env.production.example` sets `DEBUG=false`. **No code change made — this is config, not a defect.** Worth stating because `docker-compose.prod.yml` caps logs at 10 MB × 5 files, and at this verbosity a busy scanner would churn through that rotation quickly enough to lose real diagnostics.

### M-3. Risk-context build holds two pool connections at once

`_build_risk_context()` runs inside the request's session (via `Depends`) and calls `get_peak_equity()`, which opens a second session. With `pool_size=20, max_overflow=10` that halves effective concurrency for those requests.

Not a correctness or deadlock problem — the second is a short read-only SELECT on a different table — and unchanged from the earlier audit. **No change made:** at your traffic level (one WPF client, 30-second monitor poll) it is not reachable, and the fix (threading the session through `get_peak_equity`) touches the risk audit path, which I would not modify without a specific reason.

---

## 4. LOW

### L-1. `app/models/user.py` declares a table that has never existed

Nothing imports it, no migration creates it, `alembic/env.py` does not register it. Because nothing ever imported it, `create_all()` never created it either. Auth uses `settings.admin_password_hash` (`app/api/v1/endpoints/auth.py:30`), not a users table.

**Not fixed — deleting a model is a structural change needing your approval.** The concrete hazard: someone adds it to `env.py`, autogenerate proposes creating a table nothing uses.

### L-2. `data/smc_frequency_report_*.json` committed (4 files, ~72 KB)

Generated analysis artifacts in version control. Excluded from the image via `.dockerignore`; left in git as they are not sensitive.

---

## Checked and found clean

Listed so the absence of a finding is not mistaken for absence of a check.

| Area | Result |
|---|---|
| Blocking I/O inside `async` functions | **none** — AST sweep of every `AsyncFunctionDef` in `app/` for `time.sleep`, `requests.*`, `subprocess.*`, sync file I/O |
| Bare `except:` | **none** |
| `except Exception: pass` in trading paths | reviewed all 10 — `binance_trading_service.py:203` falls back to `resp.text` and still raises `BinanceTradingError`; the rest are `CancelledError`/`WebSocketDisconnect`, which are correct |
| Risk engine enforced at execution | **yes** — `execution_risk.py` raises unless `assessment.approved` |
| Auth on trading endpoints | **enforced** — `Depends(get_current_user)` on `trading.py:41,228` and every `trading_control.py` mutation |
| `.env` in git | **not tracked**, gitignored |
| CORS | no middleware configured — correct for a desktop WPF client; permissive CORS would be the finding, its absence is not |
| Scanner / monitor task supervision | already held on `app.state` with done-callbacks (only the two market-data tasks were unsupervised) |
| SQL injection | ORM-parameterised throughout; the only f-string SQL is `ALTER TYPE ... ADD VALUE '{value}'` in the migration, over a hardcoded tuple |

---

## Files changed

| File | Change |
|---|---|
| `FastAPI Backend/.gitignore` | exclude `data/binance_credentials.enc`, `data/*.enc` |
| `FastAPI Backend/.dockerignore` | **new** — secrets, `.git`, detritus |
| `FastAPI Backend/docker-compose.prod.yml` | mount `./data:/app/data` |
| `FastAPI Backend/app/scheduler/universal_scanner.py` | hold + supervise + cancel market-data tasks |
| `FastAPI Backend/tests/test_production_hardening.py` | **new** — 11 tests |
| git index | `git rm --cached` on the credentials blob |

No trading logic, ICT engine, AI scoring, risk limit or migration was modified.

---

## Verdict

**Not safe for Mainnet until C-1 is closed by you** — rotate keys, purge history, set a real `SECRET_KEY`. Everything else in Critical and High is fixed and test-locked.

Testnet operation is fine as-is.
