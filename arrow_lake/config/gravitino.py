"""Gravitino metadata federation configuration."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class GravitinoAuthType(StrEnum):
    """Gravitino authentication type."""

    SIMPLE = "simple"
    OAUTH = "oauth"
    KERBEROS = "kerberos"


class GravitinoSyncDirection(StrEnum):
    """Metadata sync direction."""

    OUTBOUND = "outbound"
    INBOUND = "inbound"
    BIDIRECTIONAL = "bidirectional"


class GravitinoConfig(BaseModel):
    """Gravitino connection and sync settings.

    All fields use ``ARROW_LAKE__GRAVITINO__`` env prefix via pydantic-settings.
    """

    enabled: bool = Field(default=False, description="Enable Gravitino integration")
    uri: str = Field(default="http://gravitino:8090", description="Gravitino REST URI")
    metalake: str = Field(default="arrow_lake", description="Metalake name")

    # Lance REST Catalog
    lance_rest_uri: str = Field(
        default="http://arrow-lake-lance-rest:9101/lance",
        description="Lance REST Catalog URI (internal)",
    )
    lance_rest_enabled: bool = Field(
        default=True, description="Enable Lance REST Catalog"
    )

    # Auth
    auth_type: GravitinoAuthType = Field(
        default=GravitinoAuthType.SIMPLE, description="Authentication type"
    )

    # Sync
    sync_direction: GravitinoSyncDirection = Field(
        default=GravitinoSyncDirection.BIDIRECTIONAL,
        description="Metadata sync direction",
    )
    sync_interval_seconds: int = Field(
        default=30, ge=5, le=300, description="Sync interval in seconds"
    )

    # ── v1.4.2: Policy Enforcement ──
    retention_enforce_interval_seconds: int = Field(
        default=3600, ge=300, description="Retention policy enforcement interval"
    )
    masking_policy_cache_ttl_seconds: int = Field(
        default=60, ge=10, description="Masking policy cache TTL"
    )

    # ── v1.4.2: Tag-driven ACL ──
    tag_acl_sync_interval_seconds: int = Field(
        default=300, ge=30, description="Tag-to-ACL sync interval"
    )
    tag_access_rules: dict[str, dict[str, list[str]]] = Field(
        default_factory=lambda: {
            "pii": {"visible_to": ["admin"]},
            "sensitive": {"visible_to": ["admin", "editor"]},
            "financial": {"visible_to": ["admin"]},
        },
        description="Tag-to-role access rules: tag → {visible_to: [roles]}",
    )

    # ── v1.4.2: Stats-driven optimization ──
    stats_cache_ttl_seconds: int = Field(
        default=300, ge=30, description="Statistics cache TTL"
    )
    stats_auto_route_threshold: int = Field(
        default=1_000_000, ge=10_000,
        description="Row count threshold for auto-routing queries to DuckDB OLAP",
    )

    # ── v1.4.2: Model registry ──
    model_resolver_cache_ttl_seconds: int = Field(
        default=600, ge=60, description="Model path cache TTL"
    )

    # ── v1.4.2: Lineage ──
    lineage_sync_to_gravitino: bool = Field(
        default=True, description="Sync lineage events to Gravitino table properties"
    )
    lineage_sync_from_gravitino: bool = Field(
        default=False, description="Pull lineage from Gravitino into local store"
    )

    # ── v1.4.2: Federated query ──
    federated_query_max_rows: int = Field(
        default=100_000, ge=1_000, description="Max rows for cross-catalog federated queries"
    )
