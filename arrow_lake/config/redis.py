"""Redis configuration for distributed session coordination."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RedisConfig(BaseModel):
    """Redis connection settings for distributed semaphore and JWT blacklist.

    When ``enabled`` is ``False``, the system falls back to in-process
    ``threading.Semaphore`` and in-memory data structures.
    """

    enabled: bool = False
    url: str = "redis://localhost:6379/0"
    password: str = ""
    ssl: bool = False
    ssl_cert_reqs: str = "required"
    semaphore_key_prefix: str = "arrow_lake:semaphore:"
    semaphore_ttl_seconds: int = Field(default=300, ge=1)
    redis_pool_size: int = Field(default=10, ge=1)
    instance_registry_key: str = "arrow_lake:instances"
    instance_heartbeat_ttl_seconds: int = Field(default=30, ge=5)
    task_key_prefix: str = "arrow_lake:task:"
    task_ttl_seconds: int = Field(default=7200, ge=60)
    # v1.9.2 批5: rate_limit + login lockout (多 worker 分布式共享)
    rate_limit_key_prefix: str = "arrow_lake:rl:"
    rate_limit_login_bucket: str = "login"
