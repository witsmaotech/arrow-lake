"""System database (libSQL / Turso) configuration for control-plane persistence.

Introduced in v1.9.0: a unified relational store for the *control plane* —
RBAC, identity, personal tokens, catalog registry, task history, lineage
index, RAG sessions, governance history. The data plane (Lance / DuckDB /
HugeGraph / MinIO) is intentionally NOT touched (see
``docs/v1.9.0-turso-system-db-plan.md``).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SystemDBConfig(BaseModel):
    """Settings for the control-plane database.

    When ``enabled`` is ``False``, control-plane structs keep their original
    pre-v1.9 in-memory / ephemeral-file behavior (graceful degradation), so a
    deployment can opt in incrementally.

    The ``url`` selects the deployment mode:

    * ``file:local.db`` — embedded (dev, no server, no token)
    * ``http://system-db:8080`` — self-hosted libSQL server (prod, 4 workers)
    * ``:memory:`` — ephemeral (unit tests)
    """

    enabled: bool = False
    url: str = "file:local.db"
    auth_token: str = ""

    connect_timeout_seconds: float = Field(default=5.0, ge=0.1)
    health_probe_on_startup: bool = True

    # fail_close (RBAC/identity: refuse requests when store down) |
    # fail_soft (catalog/tasks/rag: log + degrade)
    fail_mode: str = "fail_close"

    # v1.9.0 availability: when sqld is unreachable at runtime, serve the
    # last-cached RBAC decision instead of denying (bounded staleness). The
    # role-permission matrix is warmed at startup, so role-based checks keep
    # working through an outage; only ACL *mutations* fail. Set False for
    # strict fail-close (deny-all on store error).
    serve_stale_on_error: bool = True

    # Empty = package default (arrow_lake/system_db/migrations).
    migrations_dir: str = ""

    # Short-TTL per-worker ACL cache (multi-worker eventual consistency,
    # 5s window acceptable for control-plane data).
    acl_cache_ttl_seconds: float = Field(default=5.0, ge=0.0)
