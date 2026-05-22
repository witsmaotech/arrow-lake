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
