# Development & Testing Environment — Analysis and Implementation Plan
**Date:** 2026-08-01 · **Status:** ANALYSIS ONLY — nothing modified, awaiting approval

Key finding up front: **most of the enterprise layout already exists and is
correct.** The missing piece is small and precise: no way to run the
existing test suite inside Docker without polluting the production image.
The plan below is accordingly a surgical addition, not a build-out.

---

## 1. Answers to the ten questions (each verified from the repo)

**1. Is the current Docker image production-only?**
Yes — by explicit design, not accident. `Dockerfile` installs only
`requirements.txt`; `.dockerignore` excludes `tests/` from the build
context (production-hardening audit H-2, with a guard test). `pip show
pytest` being empty in the container is the *intended* result. The gap is
not "pytest missing"; it's "no second image variant that has it."

**2/3. Dependency split.**
Already split, already correct in direction:

| File | Today | Verdict |
|---|---|---|
| `requirements.txt` | fastapi, uvicorn, pydantic(+settings), sqlalchemy[asyncio], asyncpg, alembic, redis, apscheduler, python-dotenv, httpx, numpy, pandas, **ta**, websockets, loguru, cryptography, pyjwt, passlib, python-multipart | All runtime — correct, except one flag: `ta==0.11.0` is imported **only** by `app/legacy/` (quarantined retail indicators, grep-verified: zero imports outside `app/legacy`). It stays for now because the quarantine still imports it; it exits `requirements.txt` when L-1-style legacy cleanup is approved, not in this plan. |
| `requirements-dev.txt` | `-r requirements.txt`, pytest==8.3.4, pytest-asyncio==0.24.0 | Correct pattern (dev extends prod). |

**To add to `requirements-dev.txt`:** `pytest-cov` (coverage in CI).
**Deliberately NOT added:** `pytest-mock` (suite uses stdlib
`unittest.mock` — adding a second mocking API invites style drift),
`black`/`mypy` (no formatting/typing regime exists in this codebase today;
adopting one mid-A5-lite is unrelated churn). `ruff` is the one linter
worth adopting — optional, flagged as M5.

**4. Introduce `docker-compose.dev.yml`?**
No — it already exists in substance: `docker-compose.yml` **is** the dev
compose (bind mount `.:/app`, `--reload`, DB port exposed) and
`docker-compose.prod.yml` is production. Renaming to the
`docker-compose.dev.yml` convention is cosmetic churn with real risk (every
runbook/command in this project's history says `docker compose ...` using
the default file). Keep the names; the dev compose gains one line (see §3).

**5. Introduce `Dockerfile.dev`?**
No — a **multi-stage `Dockerfile`** with a `dev` target is strictly
better: one file, one base layer sequence, so dev and prod can never drift
apart on Python version or system deps. `runtime` stage = exactly today's
image; `dev` stage = `FROM runtime` + `pip install -r requirements-dev.txt`.
A separate `Dockerfile.dev` is two files to keep in sync forever.

**6. Production Docker unchanged?**
Yes — invariant of this plan. `docker-compose.prod.yml` explicitly targets
the `runtime` stage; image contents byte-identical to today (verified in
the migration plan's acceptance step by comparing `pip freeze` in the
built prod image before/after).

**7. How will CI run tests?**
CI **already exists and already runs them**: `.github/workflows/backend-tests.yml`
— checkout → Python 3.11 + pip cache keyed on `requirements-dev.txt` →
`pip install -r requirements-dev.txt` → compile check → `pytest -v`. No DB
service is defined and none is needed: the suite is deliberately
DB-independent (static/AST + mocked-service tests). Two improvements:
align CI Python to **3.12** (the image's version — today CI tests on 3.11,
a real, if small, parity gap), and add `--cov=app` with a summary once
pytest-cov lands.

**8. How will local developers run tests?**
Two supported paths, both documented in M6:
(a) **Native** (Windows, existing `.venv`): `pip install -r
requirements-dev.txt` then `pytest` — works today, zero Docker.
(b) **In Docker** (the environment that matches production):
`docker compose exec app pytest -q` — works after M1-M2 because the dev
compose builds the `dev` target. Until then, the zero-change stopgap is a
one-off ephemeral container:
`docker compose run --rm app sh -c "pip install -q -r requirements-dev.txt && pytest -q"`
(installs into a throwaway container; the running `app` container and the
image are untouched).

**9. How should pytest discover tests?**
As it already does — `pytest.ini` is present and correct: `testpaths =
tests`, `pythonpath = .`, `asyncio_mode = auto`. Convention: `tests/` is a
real package (`__init__.py`), flat, `test_<module>.py` naming mirroring
`app/` modules. No change.

**10. Does the project already follow a testing convention?**
Yes: 47 test files, one flat package, name-mirroring, DB-less by design,
plus a distinct integration layer that is *not* pytest — the Testnet
validation harness (`scripts/validate_testnet_execution.py`), which
correctly stays outside CI (it talks to a real exchange). The convention
is coherent; nothing to restructure.

---

## 2. Dependency architecture (target)

```
requirements.txt          runtime only — what the production image installs
  └─ (unchanged this plan; `ta` flagged for exit with legacy cleanup)
requirements-dev.txt      -r requirements.txt + test/lint tooling
  ├─ pytest==8.3.4, pytest-asyncio==0.24.0     (existing)
  ├─ pytest-cov==<pin>                         (add — M3)
  └─ ruff==<pin>                               (optional — M5)
```
Rule: the prod image never reads `requirements-dev.txt`; CI and the dev
image never install anything *not* pinned in one of these two files.

## 3. Docker architecture (target)

```
Dockerfile (multi-stage)
  ├─ stage "runtime"  == today's image, byte-identical (entrypoint, CMD)
  └─ stage "dev"      FROM runtime → install requirements-dev.txt

docker-compose.yml        (dev)   build: { target: dev }  + existing bind mount/--reload
docker-compose.prod.yml   (prod)  build: { target: runtime }   ← contents unchanged otherwise
```
`docker compose exec app pytest` then works in dev, and cannot work in
prod — which is the correct asymmetry.

## 4. Testing architecture (unchanged, made explicit)

| Layer | Runs | Where |
|---|---|---|
| Unit + static/AST invariant suite (47 files) | every commit | CI + dev container + native venv |
| Compile gate | before pytest | CI (exists) |
| Testnet execution harness | manually, gated | operator's machine only — never CI |
| Fresh-DB migration build + `alembic check` | per DB-touching phase | operator's machine (needs real Postgres) |

## 5. CI architecture (delta only)

Keep `backend-tests.yml` as-is except: `python-version: "3.12"` (parity
with the image), `pytest -v --cov=app --cov-report=term-missing` after
pytest-cov lands. Optional later: a `ruff check app tests` step, and a
second workflow job that builds the prod image and asserts pytest is NOT
importable in it (locks question 1's answer permanently).

## 6. Directory layout (already conformant — no moves)

```
FastAPI Backend/
  app/                  production code
  tests/                pytest suite (flat package, test_*.py)
  scripts/              operational tooling incl. Testnet harness (not pytest)
  alembic/              migrations
  pytest.ini            discovery config
  requirements.txt      runtime deps
  requirements-dev.txt  dev deps
  Dockerfile            (becomes multi-stage; only file that changes shape)
  docker-compose.yml / docker-compose.prod.yml
.github/workflows/backend-tests.yml
```

## 7. Migration plan (small, ordered, each step independently revertible)

| Step | Change | Risk | Acceptance |
|---|---|---|---|
| M1 | `Dockerfile` → multi-stage (`runtime` + `dev`); `runtime` content byte-identical | low | prod image `pip freeze` identical before/after; `docker compose -f docker-compose.prod.yml build` green |
| M2 | `docker-compose.yml` (dev): `build: { target: dev }`; `docker-compose.prod.yml`: `target: runtime` explicit | low | `docker compose exec app pytest -q` runs; prod compose builds runtime stage |
| M3 | `requirements-dev.txt` += `pytest-cov` (pinned) | none | suite green with `--cov=app` |
| M4 | CI: Python 3.11→3.12; add coverage flags | low | Actions run green |
| M5 (optional, own approval) | ruff + CI lint step | low | `ruff check` clean or baselined |
| M6 | `TESTING.md`: the two developer paths + harness/CI boundary + guard-test note (`tests/test_production_hardening.py` asserts `.dockerignore` keeps `tests/` out of the build context — the dev stage gets tests via the bind mount, not the image, so no conflict) | none | doc review |

Total: ~4 files touched (Dockerfile, two compose files, requirements-dev,
CI yaml, plus one new doc). Rollback for every step is a one-line revert.

**Immediate unblocking note:** the A5-lite checkpoint does not need to wait
for M1-M2 — the ephemeral-container command in §1-Q8(b) runs the suite
today with zero changes and zero image pollution.

---

Awaiting your approval on M1-M4 (+M5 optional) before touching anything.
