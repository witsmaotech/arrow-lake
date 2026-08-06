"""Redis-backed task state store for multi-worker deployments.

When Redis is available, all task state is stored in Redis hashes so any
uvicorn worker can read/write task progress.  When Redis is unavailable,
the caller falls back to the in-memory ``TaskManager._tasks`` dict.

Key layout::

    arrow_lake:task:{task_id}  →  Redis HASH with task fields
    arrow_lake:task:index      →  Redis SET  of all active task IDs
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from typing import Any

try:
    import redis as _redis_module
except ImportError:  # pragma: no cover
    _redis_module = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class RedisTaskStore:
    """Redis-backed store for ``BackgroundTask`` state.

    Each task is stored as a Redis HASH under ``{prefix}{task_id}``.
    An index SET ``{prefix}index`` tracks all active task IDs for listing.
    """

    def __init__(
        self,
        *,
        key_prefix: str = "arrow_lake:task:",
        ttl_seconds: int = 7200,
        redis_url: str = "redis://localhost:6379/0",
        password: str = "",
        ssl: bool = False,
        pool_size: int = 10,
    ) -> None:
        self._prefix = key_prefix
        self._ttl = ttl_seconds
        self._index_key = f"{key_prefix}index"
        self._redis: Any = None
        self._connected = False

        try:
            if _redis_module is None:
                raise ImportError("redis package not installed")

            kwargs: dict[str, Any] = {
                "max_connections": pool_size,
                "socket_timeout": 5,
                "socket_connect_timeout": 5,
                "decode_responses": True,
            }
            if password:
                kwargs["password"] = password
            if ssl:
                kwargs["ssl"] = True
                kwargs["ssl_cert_reqs"] = "required"

            self._redis = _redis_module.Redis.from_url(redis_url, **kwargs)
            self._redis.ping()
            self._connected = True
            logger.info("RedisTaskStore connected: %s", redis_url)
        except Exception as exc:
            self._connected = False
            self._redis = None
            logger.warning("RedisTaskStore: Redis unavailable (%s)", exc)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _ensure_connected(self) -> bool:
        """Return True if Redis is usable, (re)connecting lazily on demand.

        A transient error sets ``_connected=False`` (see :meth:`_handle_error`).
        The NEXT call re-pings and recovers, so a worker is never permanently
        blinded by a single Redis hiccup — the original bug where one error
        disabled cross-worker task visibility for the worker's whole lifetime.
        """
        if self._connected and self._redis is not None:
            return True
        if self._redis is None:
            return False
        try:
            self._redis.ping()
            self._connected = True
            logger.info("RedisTaskStore reconnected")
            return True
        except Exception as exc:  # noqa: BLE001 — unreachable/transient
            self._connected = False
            logger.debug("RedisTaskStore reconnect failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_task(self, task_data: dict[str, Any]) -> bool:
        """Store a new task.  Returns True on success."""
        if not self._ensure_connected():
            tid = task_data.get("task_id", "")
            logger.warning("RedisTaskStore create_task dropped (redis down): %s", tid)
            return False
        task_id = task_data.get("task_id", "")
        if not task_id:
            return False
        key = f"{self._prefix}{task_id}"
        try:
            mapping = {
                k: json.dumps(v) if isinstance(v, (dict, list)) else str(v)
                for k, v in task_data.items()
            }
            pipe = self._redis.pipeline()
            pipe.hset(key, mapping=mapping)
            pipe.expire(key, self._ttl)
            pipe.sadd(self._index_key, task_id)
            pipe.expire(self._index_key, self._ttl)
            pipe.execute()
            return True
        except Exception as exc:
            logger.debug("RedisTaskStore.create_task failed: %s", exc)
            self._handle_error()
            return False

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Return task data as dict, or None."""
        if not self._ensure_connected():
            return None
        key = f"{self._prefix}{task_id}"
        try:
            raw = self._redis.hgetall(key)
            if not raw:
                return None
            return self._deserialize_task(raw)
        except Exception as exc:
            logger.debug("RedisTaskStore.get_task failed: %s", exc)
            self._handle_error()
            return None

    def update_task(self, task_id: str, updates: dict[str, Any]) -> bool:
        """Update specific fields of a task."""
        if not self._ensure_connected():
            # update_task is hot (kg_build progress poller calls ~every 2s);
            # the per-task create_task warning already flags the outage, so
            # stay quiet here to avoid log spam during a transient outage.
            return False
        key = f"{self._prefix}{task_id}"
        try:
            mapping = {
                k: json.dumps(v) if isinstance(v, (dict, list)) else str(v)
                for k, v in updates.items()
            }
            pipe = self._redis.pipeline()
            pipe.hset(key, mapping=mapping)
            pipe.expire(key, self._ttl)
            pipe.execute()
            return True
        except Exception as exc:
            logger.debug("RedisTaskStore.update_task failed: %s", exc)
            self._handle_error()
            return False

    def list_task_ids(self) -> list[str]:
        """Return all active task IDs."""
        if not self._ensure_connected():
            return []
        try:
            ids = self._redis.smembers(self._index_key)
            return [i for i in ids if i]
        except Exception as exc:
            logger.debug("RedisTaskStore.list_task_ids failed: %s", exc)
            return []

    def delete_task(self, task_id: str) -> bool:
        """Remove a task from Redis."""
        if not self._ensure_connected():
            return False
        key = f"{self._prefix}{task_id}"
        try:
            pipe = self._redis.pipeline()
            pipe.delete(key)
            pipe.srem(self._index_key, task_id)
            pipe.execute()
            return True
        except Exception as exc:
            logger.debug("RedisTaskStore.delete_task failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def evict_expired(self) -> int:
        """Remove completed/failed tasks older than TTL.

        Returns count of evicted tasks.
        """
        if not self._ensure_connected():
            return 0
        evicted = 0
        try:
            ids = self._redis.smembers(self._index_key)
            now = time.time()
            for tid in ids:
                raw = self._redis.hgetall(f"{self._prefix}{tid}")
                if not raw:
                    self._redis.srem(self._index_key, tid)
                    continue
                status = raw.get("status", "")
                completed_at = raw.get("completed_at", "")
                if status in ("completed", "failed") and completed_at:
                    try:
                        from datetime import datetime, timezone
                        dt = datetime.fromisoformat(completed_at)
                        age = now - dt.timestamp()
                        if age > self._ttl:
                            self._redis.delete(f"{self._prefix}{tid}")
                            self._redis.srem(self._index_key, tid)
                            evicted += 1
                    except (ValueError, OSError):
                        pass
        except Exception as exc:
            logger.debug("RedisTaskStore.evict_expired failed: %s", exc)
        return evicted

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _deserialize_task(raw: dict[str, str]) -> dict[str, Any]:
        """Parse Redis hash values into proper Python types."""
        result: dict[str, Any] = {}
        for k, v in raw.items():
            if k in ("progress",):
                result[k] = float(v)
            elif k in ("detail", "result"):
                try:
                    result[k] = json.loads(v) if v else None
                except (json.JSONDecodeError, TypeError):
                    result[k] = None
            else:
                result[k] = v
        return result

    def _handle_error(self) -> None:
        """Mark disconnected and attempt reconnection on next call."""
        self._connected = False

    def shutdown(self) -> None:
        """Close the Redis connection."""
        if self._redis is not None:
            with contextlib.suppress(Exception):
                self._redis.close()
            self._connected = False


def create_task_store(redis_config: Any) -> RedisTaskStore | None:
    """Factory: return RedisTaskStore if Redis is enabled, else None."""
    if getattr(redis_config, "enabled", False):
        store = RedisTaskStore(
            key_prefix=redis_config.task_key_prefix,
            ttl_seconds=redis_config.task_ttl_seconds,
            redis_url=redis_config.url,
            password=redis_config.password,
            ssl=redis_config.ssl,
            pool_size=redis_config.redis_pool_size,
        )
        if store.is_connected:
            return store
        # Redis enabled but not reachable — warn and fall back
        logger.warning("Redis enabled but connection failed, using in-memory task store")
    return None
