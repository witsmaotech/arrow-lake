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
