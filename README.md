# AI Crypto Signal Pro

Smart-Money-Concepts (SMC) based crypto trading **signal** generator: a
FastAPI backend that scans Binance USD-M futures symbols for BOS/CHoCH
structure breaks, order blocks, fair value gaps, liquidity sweeps, and
supply/demand zones, scores each candidate setup with a hand-weighted (and
optionally calibrated) AI scorer, and surfaces the results through a REST
API, a WebSocket feed, and a WPF desktop app.

**This project does not place trades.** Binance account access (when a
user links one) is read-only - balances, positions, and order history
only. It generates signals and position-size suggestions; acting on them
is always a manual, human decision.

## Disclaimer

This software is provided for educational and informational purposes. It
is not financial advice, and nothing it outputs (signals, confidence
scores, backtest results, suggested position sizes) is a recommendation
to buy or sell anything. Trading cryptocurrency futures carries
substantial risk of loss. Past performance (including backtested
performance - see `docs/VALIDATION.md`) does not guarantee future results.
You are solely responsible for any trading decisions you make. The
authors and contributors accept no liability for financial losses
incurred through use of this software.

**Before using any signal from this system with real money, read
`docs/VALIDATION.md`.** As of this writing, the strategy has not yet
accumulated enough backtested/live sample size to be considered
statistically validated.

## Architecture

- `FastAPI Backend/` - the API, scanner, and signal-generation engine (Python, FastAPI, PostgreSQL, Redis).
- `AI_Crypto_Signal_Pro/` - the WPF desktop client (.NET, MaterialDesignInXaml).

Key backend modules:

| Module | What it does |
|---|---|
| `app/smc/` | Market structure, order blocks, FVG, liquidity, supply/demand zone detection |
| `app/ai/scorer.py` | AIScorer - 9-category graded confidence score per candidate setup |
| `app/ai/calibration.py` | Re-derives AIScorer weights from real win/loss outcomes |
| `app/strategy/signal_generator.py` | Combines SMC detection + AIScorer into a signal (entry/SL/TP/confidence) |
| `app/scheduler/scanner.py` | Live scanning loop - streams candles, calls the generator, saves signals |
| `app/scheduler/signal_monitor.py` | Closes ACTIVE signals against live price (TP/SL resolution) |
| `app/backtest/` | Replays historical candles through the same pipeline for backtesting and bulk calibration |
| `app/services/position_sizing.py` | Real-account-balance-based position size / profit / loss suggestions |
| `app/services/correlation_risk.py` | Flags stacked same-direction exposure between concurrently ACTIVE signals |

## Setup - development

```bash
cd "FastAPI Backend"
cp .env.example .env   # if you don't already have one
docker compose up -d --build
```

API docs (Swagger UI) at `http://localhost:8000/docs` once `DEBUG=true` in
`.env`. Auth is off by default in dev (`REQUIRE_AUTH` unset) so the WPF
app and Swagger both work without a login step.

Run the test suite:

```bash
cd "FastAPI Backend"
pip install -r requirements-dev.txt
pytest -v
```

## Setup - production

```bash
cd "FastAPI Backend"
cp .env.production.example .env.production
python scripts/generate_secret_key.py        # paste into SECRET_KEY
python scripts/generate_password_hash.py     # paste into ADMIN_PASSWORD_HASH
# fill in POSTGRES_USER / POSTGRES_PASSWORD / DATABASE_URL (must match each other) in .env.production

docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
```

`docker-compose.prod.yml` differs from the dev compose file in a few
deliberate ways - see the comments in that file for why:

- No `--reload`, and explicitly `--workers 1` (the scanner/monitor/
  websocket listener are singleton in-process background tasks with no
  cross-worker coordination - more workers would run duplicate scanners).
- Postgres and Redis are not exposed to the host.
- No source bind-mount - runs the image built at deploy time.

With `REQUIRE_AUTH=true` (the production template's default), every API
call needs `Authorization: Bearer <token>` from `POST /api/v1/auth/login`
first. See `app/core/security.py` for what this auth model does and does
not cover (single admin account, not multi-tenant - see that file's
docstring before treating this as ready for untrusted multi-user access).

## Known limitations (read before publishing/deploying further)

- **Strategy edge is not yet statistically validated** - see `docs/VALIDATION.md`.
- **Single admin auth, not multi-tenant** - one account, one set of
  Binance credentials, one global scanner. Real multi-user isolation
  (separate credentials/signals/scanners per account) is a bigger change
  than what's implemented.
- **No automated alerting** if the scanner, signal monitor, or WebSocket
  listener crashes - only logs. Worth adding before unattended production
  use.
- **Backtest results lean on SMC/structure signals more than live ones**
  do, because funding rate, liquidation pressure, and fundamentals have
  no free historical series and are passed neutral during backtests (see
  `app/backtest/engine.py`).
