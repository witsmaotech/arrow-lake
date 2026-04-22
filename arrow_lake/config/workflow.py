"""Workflow orchestration configuration — workflow, Argo, autoscale."""

from __future__ import annotations

from pydantic import BaseModel, field_validator


class WorkflowConfig(BaseModel):
    """Workflow orchestration configuration (Epic 6).

    Attributes:
        max_retry_attempts: Maximum retry attempts per step.
        min_backoff_seconds: Minimum backoff between retries (exponential).
        max_backoff_seconds: Maximum backoff between retries.
        checkpoint_enabled: Enable Lance version checkpointing before steps.
        ray_execution_enabled: Enable Ray cluster execution (--with ray).
        auto_tag_runs: Auto-generate tags from run metadata.
        artifact_retention_days: Days to retain Argo workflow artifacts.
    """

    max_retry_attempts: int = 3
    min_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 60.0
    checkpoint_enabled: bool = True
    ray_execution_enabled: bool = False
    auto_tag_runs: bool = True
    artifact_retention_days: int = 30
    schedule_cron: str | None = None

    @field_validator("max_retry_attempts")
    @classmethod
    def validate_max_retry_attempts(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"max_retry_attempts must be >= 0, got {v}")
        return v

    @field_validator("min_backoff_seconds", "max_backoff_seconds")
    @classmethod
    def validate_backoff(cls, v: float) -> float:
        if v < 0:
            raise ValueError(f"backoff_seconds must be >= 0, got {v}")
        return v


class ArgoConfig(BaseModel):
    """Argo Workflows configuration (Story 7.3).

    Attributes:
        namespace: Kubernetes namespace for Argo workflows.
        service_account: Service account for workflow pods.
        workflow_timeout: Workflow execution timeout in seconds.
        image: Container image for workflow pods.
        image_pull_policy: Image pull policy.
        artifact_storage: Storage backend for Argo artifacts (s3:// or minio://).
    """

    namespace: str = "default"
    service_account: str = "arrow-lake"
    workflow_timeout: int = 3600
    image: str = "arrow-lake:latest"
    image_pull_policy: str = "IfNotPresent"
    artifact_storage: str = ""

    @field_validator("workflow_timeout")
    @classmethod
    def validate_timeout(cls, v: int) -> int:
        if v < 60:
            raise ValueError(f"workflow_timeout must be >= 60 seconds, got {v}")
        return v


class AutoscaleConfig(BaseModel):
    """GPU autoscaling configuration (Story 7.5).

    Attributes:
        enabled: Whether GPU autoscaling is active.
        min_workers: Minimum GPU workers (0 = scale to zero).
        max_workers: Maximum GPU workers.
        scale_up_timeout_seconds: Max wait time for scale-up.
        idle_timeout_seconds: Seconds of inactivity before scale-down.
        spot_preference: Prefer spot instances (0.0=on-demand, 1.0=spot-only).
        gpu_increment: Fractional GPU increment (0.5 = half-GPU steps).
    """

    enabled: bool = False
    min_workers: int = 0
    max_workers: int = 8
    scale_up_timeout_seconds: int = 300
    idle_timeout_seconds: int = 600
    spot_preference: float = 0.8
    gpu_increment: float = 0.5

    @field_validator("max_workers")
    @classmethod
    def validate_max_workers(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"max_workers must be >= 1, got {v}")
        return v

    @field_validator("scale_up_timeout_seconds", "idle_timeout_seconds")
    @classmethod
    def validate_timeout(cls, v: int) -> int:
        if v < 60:
            raise ValueError(f"timeout must be >= 60 seconds, got {v}")
        return v

    @field_validator("spot_preference")
    @classmethod
    def validate_spot_preference(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"spot_preference must be 0.0-1.0, got {v}")
        return v

    @field_validator("gpu_increment")
    @classmethod
    def validate_gpu_increment(cls, v: float) -> float:
        if v not in (0.5, 1.0):
            raise ValueError(f"gpu_increment must be 0.5 or 1.0, got {v}")
        return v
