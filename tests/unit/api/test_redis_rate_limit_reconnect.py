"""P0-4: RedisRateLimiter lazy reconnect + 422 input scrub tests."""

from __future__ import annotations

from arrow_lake.api._redis_rate_limit import RedisRateLimiter


class _FakePipeline:
    def __init__(self, fail: bool) -> None:
        self._fail = fail
        self._count = 0

    def incr(self, key: str) -> None:
        if self._fail:
            raise ConnectionError("redis down")
        self._count += 1

    def expire(self, key: str, ttl: int, nx: bool = False) -> None:
        pass

    def execute(self):
        return [self._count, None]


class _FakeRedis:
    """Toggle-able fake: fails while .down is True, recovers after."""

    def __init__(self) -> None:
        self.down = True

    def ping(self) -> None:
        if self.down:
            raise ConnectionError("redis down")

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(fail=self.down)

    def ttl(self, key: str) -> int:
        return 30


def _limiter_with_fake() -> tuple[RedisRateLimiter, _FakeRedis]:
    rl = RedisRateLimiter.__new__(RedisRateLimiter)
    rl._prefix = "t:"
    rl._login_bucket = "t:login"
    rl._login_fail_limit = 10
    rl._login_lockout_seconds = 900
    fake = _FakeRedis()
    rl._redis = fake
    rl._connected = False
    return rl, fake


class TestLazyReconnect:
    def test_recovers_after_redis_outage(self) -> None:
        rl, fake = _limiter_with_fake()
        fake.down = True
        # Outage: hit fails once and falls back.
        assert rl.hit("1.2.3.4", "/x", limit=10, window=60) is None
        assert rl.is_connected is False
        # Redis comes back: next call lazily reconnects instead of staying
        # permanently degraded to the 4x-diluted in-memory counter.
        fake.down = False
        result = rl.hit("1.2.3.4", "/x", limit=10, window=60)
        assert result is not None and result[0] is True
        assert rl.is_connected is True

    def test_never_raises(self) -> None:
        rl, fake = _limiter_with_fake()
        fake.down = True
        assert rl.hit("ip", "p", limit=1, window=60) is None
        assert rl.check_login("u", "ip") is None
        rl.record_login_failure("u", "ip")  # must not raise
        rl.reset_login("u", "ip")  # must not raise


class TestValidation422InputScrub:
    def test_input_key_never_reflected(self) -> None:
        from arrow_lake.api.errors import _safe_validation_errors

        class _Exc:
            def errors(self):
                return [
                    {
                        "type": "string_too_short",
                        "loc": ("body", "password"),
                        "msg": "String should have at least 8 characters",
                        "input": "hunter2",
                    },
                    {
                        "type": "missing",
                        "loc": ("body", "username"),
                        "msg": "Field required",
                        "input": b"\x00binary",
                    },
                ]

        safe = _safe_validation_errors(_Exc())
        assert all("input" not in e for e in safe)
        assert safe[0]["msg"].startswith("String should have at least")
        assert list(safe[1]["loc"]) == ["body", "username"]
