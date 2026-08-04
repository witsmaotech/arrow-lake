"""API and security configuration — audit, API, auth, rate limit, OpenTelemetry, lineage."""

from __future__ import annotations

from pydantic import BaseModel, field_validator, model_validator

from arrow_lake.config._enums import AuthMode


class LineageConfig(BaseModel):
    """Data lineage configuration (Sprint 9, Story 8.3).

    Attributes:
        enabled: Whether lineage tracking is active.
        store_dataset: Name of the lineage events dataset.
        auto_record: Automatically record lineage on dataset operations.
    """

    enabled: bool = False
    store_dataset: str = "sys_lineage_events"
    auto_record: bool = True


class AuditConfig(BaseModel):
    """Event sourcing audit configuration (Sprint 9, Story 8.4).

    Attributes:
        enabled: Whether audit trail is active.
        hmac_secret_key: Secret key for HMAC. Empty disables HMAC.
        audit_dataset: Name of the audit trail dataset.
        auto_record_workflow: Auto-record workflow events.
    """

    enabled: bool = False
    hmac_secret_key: str = ""
    audit_dataset: str = "sys_audit_trail"
    auto_record_workflow: bool = True


class ApiConfig(BaseModel):
    """REST API configuration.

    Attributes:
        enabled: Whether the REST API server is active.
        host: Bind address.
        port: Listen port.
        api_key: API key for authentication. Empty disables auth.
        api_key_header: HTTP header name for API key.
        cors_origins: Allowed CORS origins.
        arrow_ipc_threshold_bytes: Results larger than this use Arrow IPC encoding.
        request_timeout_seconds: Maximum request processing time.
        max_request_size_bytes: Maximum HTTP request body size.
        security_headers_enabled: Enable HTTP security response headers.
        content_security_policy: CSP value. Empty = header not set.
        frame_options: X-Frame-Options value (DENY, SAMEORIGIN, or empty to disable).
        tls_enabled: Whether TLS is enabled. Actual TLS termination should be
            handled by uvicorn CLI flags or a reverse proxy (nginx/Caddy).
        ssl_keyfile: Path to TLS private key file.
        ssl_certfile: Path to TLS certificate file.
        api_key_default_role: Default role assigned to API key authenticated users.
    """

    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 8000
    api_key: str = ""
    api_key_header: str = "X-API-Key"
    cors_origins: list[str] = []
    arrow_ipc_threshold_bytes: int = 10240  # 10 KB
    request_timeout_seconds: float = 300.0

    @field_validator("request_timeout_seconds")
    @classmethod
    def validate_request_timeout(cls, v: float) -> float:
        if v < 1.0:
            raise ValueError(f"request_timeout_seconds must be >= 1.0, got {v}")
        return v
    max_request_size_bytes: int = 100 * 1024 * 1024  # 100 MB
    auto_generate_request_id: bool = True
    docs_enabled: bool = True
    api_key_rotation_days: int = 90
    security_headers_enabled: bool = True
    content_security_policy: str = ""
    frame_options: str = "DENY"
    tls_enabled: bool = False
    ssl_keyfile: str = ""
    ssl_certfile: str = ""
    api_key_default_role: str = "VIEWER"


class AuthConfig(BaseModel):
    """认证配置 (M4).

    Attributes:
        auth_mode: 认证模式.
        jwt_secret_key: JWT 签名密钥.
        jwt_algorithm: JWT 签名算法.
        jwt_access_token_minutes: Access token 有效期 (分钟).
        jwt_refresh_token_days: Refresh token 有效期 (天).
        jwt_issuer: JWT issuer 声明.
        jwt_public_key: PEM-encoded public key (RS256/ES256).
        jwt_private_key: PEM-encoded private key (RS256/ES256).
        jwt_bootstrap_token: One-time bootstrap token for initial JWT
            acquisition when auth_mode is "jwt".
        allow_unauthenticated_access: When True and no auth_service is
            configured, role checks are skipped (dev/test mode).
            Production deployments should keep this False (default).
    """

    auth_mode: AuthMode = AuthMode.API_KEY
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_access_token_minutes: int = 30
    jwt_refresh_token_days: int = 7
    jwt_issuer: str = "arrow-lake"
    jwt_public_key: str = ""
    jwt_private_key: str = ""
    jwt_bootstrap_token: str = ""
    allow_unauthenticated_access: bool = False

    @field_validator("jwt_secret_key")
    @classmethod
    def validate_jwt_secret(cls, v: str) -> str:
        if len(v) > 0 and len(v) < 32:
            raise ValueError(
                f"jwt_secret_key must be >= 32 characters for security, got {len(v)}"
            )
        return v

    @field_validator("jwt_access_token_minutes")
    @classmethod
    def validate_access_minutes(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"jwt_access_token_minutes must be >= 1, got {v}")
        return v

    @field_validator("jwt_refresh_token_days")
    @classmethod
    def validate_refresh_days(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"jwt_refresh_token_days must be >= 1, got {v}")
        return v

    @model_validator(mode="after")
    def validate_jwt_config(self) -> AuthConfig:
        """Ensure JWT mode has either secret_key (HS256) or key pair (RS256/ES256)."""
        if self.auth_mode == AuthMode.JWT:
            has_secret = bool(self.jwt_secret_key)
            has_key_pair = bool(self.jwt_public_key) and bool(self.jwt_private_key)
            if not has_secret and not has_key_pair:
                raise ValueError(
                    "JWT auth_mode requires either jwt_secret_key (HS256) "
                    "or both jwt_public_key and jwt_private_key (RS256/ES256)"
                )
        return self


class RateLimitConfig(BaseModel):
    """速率限制配置 (M5).

    Attributes:
        enabled: 是否启用速率限制.
        default_requests_per_minute: 默认每分钟请求数.
        default_burst: 默认突发请求数.
        override_per_endpoint: 每端点自定义限制 {"path": rpm}.
        exempt_paths: 免除限制的路径前缀列表.
    """

    enabled: bool = True
    default_requests_per_minute: int = 60
    default_burst: int = 10
    override_per_endpoint: dict[str, int] = {}
    exempt_paths: list[str] = ["/health", "/metrics", "/docs", "/openapi.json", "/redoc", "/console"]
    trusted_proxies: set[str] = set()


class OpenTelemetryConfig(BaseModel):
    """OpenTelemetry 分布式追踪配置 (M4).

    Attributes:
        enabled: Whether OTel tracing is active.
        service_name: Service name for traces.
        otel_endpoint: OTLP exporter endpoint URL.
        trace_sample_rate: Fraction of traces to sample (0.0-1.0).
    """

    enabled: bool = False
    service_name: str = "arrow-lake"
    otel_endpoint: str = "http://localhost:4317"
    trace_sample_rate: float = 1.0

    @field_validator("trace_sample_rate")
    @classmethod
    def validate_sample_rate(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"trace_sample_rate must be 0.0-1.0, got {v}")
        return v
