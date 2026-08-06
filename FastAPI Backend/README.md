# AI Crypto Signal — Backend

Production-grade FastAPI engine that scans Binance USDT-perpetuals and emits
high-probability **ICT / Smart-Money** trading signals, tracks them to
resolution, and streams everything to clients over REST + WebSocket.

---

## Strategy — Multi-Timeframe ICT

Signals are generated **top-down**, the way ICT is actually traded:

| Step | What happens | Module |
|------|--------------|--------|
| 1. **HTF bias** | 4h + 1h decide direction (structure + EMA 50/200, with a neutral deadband). No bias → no trade. | `strategy/bias.py` |
| 2. **Kill zone** | Only the London / NY sessions are high-expectancy windows (optional hard filter). | `smc/sessions.py` |
| 3. **Liquidity sweep** | Require a stop-raid of the opposite side — equal highs/lows, **PDH/PDL**, Asian range. | `smc/liquidity.py`, `smc/key_levels.py` |
| 4. **Displacement (CISD)** | Require an impulsive move that leaves a **Fair Value Gap** — the institutional footprint. | `smc/displacement.py`, `smc/fvg.py` |
| 5. **Entry** | Enter on the **OTE** (0.62–0.79 retracement) or a mitigated **order block**. | `smc/ote.py`, `smc/order_blocks.py` |
| 6. **Confluence score** | A weighted score (v2.0.0) across all factors; only setups ≥ `MIN_CONFIDENCE` publish. | `ai/scorer.py` |
| 7. **Risk model** | Structure-based stop, **liquidity-drawn** targets, enforced `MIN_RISK_REWARD`. | `strategy/signal_generator.py` |

Live **market context** feeds the score with real data (no more hard-coded
values): BTC higher-timeframe bias, Binance funding rate, and ATR-percentile
volatility (`market/context.py`).

### Signal lifecycle

Every active signal is monitored against live price and resolved
automatically (`scheduler/signal_tracker.py`):

```
ACTIVE ──SL──▶ STOPPED
ACTIVE ──TP1─▶ (stop → breakeven) ──▶ TP1_HIT / TP2_HIT / TP3_HIT
```

Progress is stored in `max_tp_hit`; `status` only flips on a terminal close,
so **win-rate and all statistics are accurate**.

---

## Architecture

```
Binance WS/REST ─▶ BinanceDataManager ─▶ CryptoScanner ─▶ SignalGenerator ─▶ DB
                                   │                              │
                                   ▼                              ▼
                          SignalTracker  ───────────────▶  Redis pub/sub ─▶ WS clients
```

* **FastAPI** app (`app/main.py`) — REST API + `/ws/signals` WebSocket.
* **Scanner** — streams candles, runs the generator on each entry-TF close.
* **Tracker** — resolves active signals to TP/SL.
* **PostgreSQL** (async SQLAlchemy 2.0) + **Redis** (pub/sub for live updates).

---

## Quick start (Docker)

```bash
cp .env.example .env          # adjust as needed
docker compose up --build     # dev: API + scanner + tracker, hot-reload
```

API: <http://localhost:8000> · Health: `/api/v1/health` · Docs (when `DEBUG=true`): `/docs`

`docker compose up` merges `docker-compose.override.yml` (dev). For production
use the base file only:

```bash
docker compose -f docker-compose.yml up --build -d
```

## Local (without Docker)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
# start Postgres + Redis (e.g. docker compose up db redis)
uvicorn app.main:app --reload
pytest -q
```

---

## Configuration

All settings come from environment variables (see `.env.example`). Highlights:

| Var | Default | Purpose |
|-----|---------|---------|
| `HTF_TIMEFRAMES` | `4h,1h` | Bias timeframes |
| `LTF_TIMEFRAME` | `15m` | Entry timeframe |
| `MIN_CONFIDENCE` | `65` | Min score to publish |
| `MIN_RISK_REWARD` | `1.5` | Min RR to publish |
| `ENFORCE_KILLZONES` | `false` | Reject setups outside London/NY |
| `API_KEY` | *(empty)* | If set, require `X-API-Key` header (auth off by default) |
| `CORS_ORIGINS` | `*` | Allowed origins |
| `RUN_SCANNER` | `true` | `false` = API-only (scale it), run engine via `python -m app.worker` |
| `AUTO_CREATE_TABLES` | `true` | Set `false` in prod and use Alembic |

### Scaling

Run the API stateless and the engine once:

```bash
# API service(s)
RUN_SCANNER=false uvicorn app.main:app --workers 4
# single engine process
python -m app.worker
```

(Uncomment the `worker` service in `docker-compose.yml`.)

---

## Database migrations (Alembic)

```bash
export AUTO_CREATE_TABLES=false
alembic upgrade head                       # apply schema
alembic revision --autogenerate -m "msg"   # after model changes
```

---

## API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/health` | DB + Redis health |
| GET | `/api/v1/signals` | Paginated, filterable signals |
| GET | `/api/v1/signals/latest` | Most recent signal |
| GET | `/api/v1/signals/{id}` | Signal by id |
| GET | `/api/v1/stats` | Aggregate performance (win-rate, avg RR, …) |
| WS  | `/ws/signals` | Live `new_signal` + `signal_update` events |

Signal responses now include `session`, `htf_bias`, `bias_strength` and
`max_tp_hit` (additive — existing clients are unaffected).

---

## Testing

```bash
pytest -q        # 17 tests: sessions, OTE, key levels, bias, tracker, pipeline
```

---

## Desktop client

The `AI_Crypto_Signal_Pro` WPF app consumes this API at
`http://localhost:8000/api/v1/`. It works with auth **off** (default); to
require auth, set `API_KEY` and send it as an `X-API-Key` header.

> **Disclaimer:** For research/education. Crypto trading carries substantial
> risk; nothing here is financial advice.
