"""
Smart AI owner-gate auth: token crypto, the in-memory login rate limiter, and
the HTTP contract (every protected route 401s unauthenticated, wrong password
rejected, rate limit triggers, expired/refresh tokens handled). No live calls.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import smartai_auth
from app.core.config import get_settings
from app.core.rate_limit import LoginRateLimiter
from app.core.security import create_access_token as create_admin_token

PASSWORD = "correct horse battery staple"
_SENTINEL_HASH = "bcrypt$owner-sentinel"
PROTECTED = [
    "/api/v1/smartai/status",
    "/api/v1/smartai/strategies",
    "/api/v1/smartai/signals?strategy_id=ict_levels",
    "/api/v1/smartai/performance?strategy_id=ict_levels",
]


@pytest.fixture
def owner_hash_set(monkeypatch):
    """Configure a known OWNER_PASSWORD_HASH on the cached settings and stub the
    passlib primitive so these tests exercise our auth LOGIC, not passlib's
    bcrypt backend (which is broken in this environment by a passlib/bcrypt
    version-detection bug). Restores the original hash afterwards."""
    settings = get_settings()
    original = settings.owner_password_hash
    settings.owner_password_hash = _SENTINEL_HASH
    monkeypatch.setattr(
        smartai_auth._pwd_context,
        "verify",
        lambda plain, hashed: plain == PASSWORD and hashed == _SENTINEL_HASH,
    )
    try:
        yield
    finally:
        settings.owner_password_hash = original


@pytest.fixture
def client():
    from app.api.v1.endpoints import smartai

    # Fresh limiter per test so attempts don't leak between tests.
    smartai.login_limiter = LoginRateLimiter(max_attempts=5, window_seconds=900, lockout_seconds=900)
    app = FastAPI()
    app.include_router(smartai.auth_router, prefix="/api/v1")
    app.include_router(smartai.router, prefix="/api/v1")
    return TestClient(app)


# ======================================================================
# Token crypto
# ======================================================================
class TestTokens:
    def test_verify_owner_password(self, owner_hash_set):
        assert smartai_auth.verify_owner_password(PASSWORD) is True
        assert smartai_auth.verify_owner_password("wrong") is False

    def test_blank_hash_fails_closed(self):
        settings = get_settings()
        original = settings.owner_password_hash
        settings.owner_password_hash = ""
        try:
            assert smartai_auth.verify_owner_password("anything") is False
        finally:
            settings.owner_password_hash = original

    def test_access_and_refresh_types_are_distinct(self):
        access = smartai_auth.create_access_token()
        refresh = smartai_auth.create_refresh_token()
        assert smartai_auth.decode_token(access, "access") == "owner"
        assert smartai_auth.decode_token(refresh, "refresh") == "owner"
        # An access token cannot be used to refresh, nor a refresh token to authorise.
        assert smartai_auth.decode_token(access, "refresh") is None
        assert smartai_auth.decode_token(refresh, "access") is None

    def test_admin_token_rejected_here(self):
        # A general admin token (wrong scope) must not open the Smart AI gate.
        assert smartai_auth.decode_token(create_admin_token("admin"), "access") is None

    def test_expired_token_rejected(self):
        settings = get_settings()
        original = settings.smartai_token_expire_minutes
        settings.smartai_token_expire_minutes = -1  # already expired on issue
        try:
            token = smartai_auth.create_access_token()
        finally:
            settings.smartai_token_expire_minutes = original
        assert smartai_auth.decode_token(token, "access") is None

    def test_garbage_token_rejected(self):
        assert smartai_auth.decode_token("not.a.jwt", "access") is None


# ======================================================================
# Rate limiter (unit, controllable clock)
# ======================================================================
class TestLoginRateLimiter:
    def _limiter(self, t):
        return LoginRateLimiter(max_attempts=5, window_seconds=900, lockout_seconds=900, clock=lambda: t[0])

    def test_locks_after_max_attempts(self):
        t = [0.0]
        lim = self._limiter(t)
        for _ in range(5):
            assert lim.is_locked("ip")[0] is False
            lim.record_failure("ip")
        assert lim.is_locked("ip")[0] is True

    def test_lock_expires_after_cooldown(self):
        t = [0.0]
        lim = self._limiter(t)
        for _ in range(5):
            lim.record_failure("ip")
        assert lim.is_locked("ip")[0] is True
        t[0] = 901.0  # past the 900s lockout
        assert lim.is_locked("ip")[0] is False

    def test_success_resets(self):
        t = [0.0]
        lim = self._limiter(t)
        for _ in range(4):
            lim.record_failure("ip")
        lim.record_success("ip")
        for _ in range(4):
            lim.record_failure("ip")
        assert lim.is_locked("ip")[0] is False  # counter was cleared

    def test_old_failures_fall_out_of_window(self):
        t = [0.0]
        lim = self._limiter(t)
        for _ in range(4):
            lim.record_failure("ip")
        t[0] = 901.0  # the 4 earlier failures are now outside the 900s window
        lim.record_failure("ip")
        assert lim.is_locked("ip")[0] is False


# ======================================================================
# HTTP contract
# ======================================================================
class TestHttp:
    def test_every_protected_route_401_without_token(self, client):
        for path in PROTECTED:
            resp = client.get(path)
            assert resp.status_code == 401, f"{path} should be 401 unauthenticated, got {resp.status_code}"

    def test_login_wrong_password_401(self, client, owner_hash_set):
        resp = client.post("/api/v1/smartai/auth/login", json={"password": "nope"})
        assert resp.status_code == 401

    def test_login_success_then_access(self, client, owner_hash_set):
        resp = client.post("/api/v1/smartai/auth/login", json={"password": PASSWORD})
        assert resp.status_code == 200
        token = resp.json()["access_token"]
        ok = client.get("/api/v1/smartai/status", headers={"Authorization": f"Bearer {token}"})
        assert ok.status_code == 200
        assert ok.json()["testnet"] is True

    def test_rate_limit_triggers_after_5_failures(self, client, owner_hash_set):
        for _ in range(5):
            assert client.post("/api/v1/smartai/auth/login", json={"password": "x"}).status_code == 401
        blocked = client.post("/api/v1/smartai/auth/login", json={"password": "x"})
        assert blocked.status_code == 429
        assert "Retry-After" in blocked.headers

    def test_refresh_flow(self, client, owner_hash_set):
        tokens = client.post("/api/v1/smartai/auth/login", json={"password": PASSWORD}).json()
        refreshed = client.post(
            "/api/v1/smartai/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert refreshed.status_code == 200
        new_access = refreshed.json()["access_token"]
        assert client.get(
            "/api/v1/smartai/status", headers={"Authorization": f"Bearer {new_access}"}
        ).status_code == 200

    def test_refresh_with_access_token_rejected(self, client, owner_hash_set):
        tokens = client.post("/api/v1/smartai/auth/login", json={"password": PASSWORD}).json()
        bad = client.post(
            "/api/v1/smartai/auth/refresh", json={"refresh_token": tokens["access_token"]}
        )
        assert bad.status_code == 401
