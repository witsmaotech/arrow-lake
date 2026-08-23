"""v1.10.7 WP3 (review H5/H6): auth hot-path blocking IO must leave the
event loop.

Login (PBKDF2 verify, libSQL read), JWT verification middleware (redis
blacklist EXISTS + libSQL token_valid_after), and the rate-limit Redis
pipeline used to run inline in async context — one slow storage call froze
the whole worker, and serialized PBKDF2 amplified credential-stuffing DoS.

Behavioral: concurrent logins with the real 200k-iteration PBKDF2 must
overlap (executor-parallel), not serialize on the loop.
Wiring: the hot call sites must dispatch via run_sync (source-scan, same
approach as the WP1 ACL audit so refactors can't silently drop coverage).
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from arrow_lake.api.auth_models import LoginRequest
from arrow_lake.api.passwords import hash_password, verify_password
from arrow_lake.api.routers.auth import login_with_password

_HASH = hash_password("correct-password")


class _FakeIdentityStore:
    def get_user_with_credentials(self, username: str) -> dict:
        return {
            "id": 1,
            "username": username,
            "role": "VIEWER",
            "is_active": True,
            "password_hash": _HASH,
        }

    def bump_token_valid_after(self, _uid: int) -> None:  # pragma: no cover
        pass


class _FakeAuthService:
    """Minimal stub: encode whatever payload it is handed."""

    def create_access_token(self, **kwargs):  # noqa: ANN003
        return SimpleNamespace(permissions=kwargs.get("permissions", []), **{
            k: v for k, v in kwargs.items() if k != "permissions"
        })

    def create_refresh_token(self, **kwargs):  # noqa: ANN003
        return "refresh"

    def _encode(self, payload) -> str:  # noqa: ANN001
        return "access"


def _fake_request() -> SimpleNamespace:
    app = SimpleNamespace(
        state=SimpleNamespace(
            identity_store=_FakeIdentityStore(),
            # _client_ip reads rate_limit.trusted_proxies via get_app_config
            config=SimpleNamespace(rate_limit=SimpleNamespace(trusted_proxies=set())),
        )
    )
    return SimpleNamespace(app=app, headers={}, client=SimpleNamespace(host="127.0.0.1"))


@pytest.fixture(autouse=True)
def _stub_auth_service(monkeypatch):
    monkeypatch.setattr(
        "arrow_lake.api.routers.auth._get_auth_service", lambda _r: _FakeAuthService()
    )


@pytest.fixture(autouse=True)
def _clear_login_failures():
    from arrow_lake.api.routers import auth as auth_mod

    auth_mod._LOGIN_FAILURES.clear()
    yield
    auth_mod._LOGIN_FAILURES.clear()


def test_verify_password_semantics_unchanged():
    assert verify_password("correct-password", _HASH) is True
    assert verify_password("wrong-password", _HASH) is False
    assert verify_password("x", None) is False


async def _login(creds: LoginRequest):
    return await login_with_password(_fake_request(), creds)


def test_concurrent_logins_overlap_not_serialize():
    """20 concurrent logins with real PBKDF2 (200k iters, ~tens of ms CPU
    each). Serialized on the event loop this is N×t; executor-parallel it
    must finish in well under half that."""

    async def scenario() -> float:
        # per-login cost reference (executor not needed for the measurement)
        t0 = time.perf_counter()
        assert verify_password("correct-password", _HASH) is True
        per = time.perf_counter() - t0

        reqs = [LoginRequest(username="u", password="correct-password") for _ in range(20)]
        t0 = time.perf_counter()
        results = await asyncio.gather(*(_login(r) for r in reqs), return_exceptions=True)
        total = time.perf_counter() - t0

        assert all(not isinstance(r, Exception) for r in results), results
        serialized_estimate = per * 20
        # Allow generous CI margin but demand real overlap (>2x speedup).
        assert total < serialized_estimate * 0.5, (
            f"serial={serialized_estimate:.2f}s actual={total:.2f}s per={per * 1000:.0f}ms"
        )
        return total

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Wiring audit: hot call sites must stay off the event loop
# ---------------------------------------------------------------------------

def test_jwt_middleware_verifies_off_loop():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[3] / "arrow_lake/api/jwt_auth.py").read_text()
    i = src.index("auth_service.verify_token")
    assert "run_sync" in src[max(0, i - 200):i + 80], "verify_token not executor-dispatched"


def test_api_key_middleware_validates_off_loop():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[3] / "arrow_lake/api/auth.py").read_text()
    i = src.index("identity_store.validate_token")
    assert "run_sync" in src[max(0, i - 200):i + 80], "validate_token not executor-dispatched"


def test_rate_limit_redis_hit_off_loop():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[3] / "arrow_lake/api/rate_limit.py").read_text()
    i = src.index("rl.hit, client_ip, path,")
    assert "run_sync" in src[max(0, i - 200):i + 120], "rl.hit not executor-dispatched"
