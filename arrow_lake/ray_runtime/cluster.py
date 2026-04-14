"""Ray runtime execution support (Story 6.2).

Provides distributed execution infrastructure for Metaflow workflows:
- Ray cluster initialization with GPU auto-detection
- Distributed step execution with resource allocation
- Execution context management
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

__all__ = [
    "RayClusterInfo",
    "RayResources",
    "detect_gpu",
    "get_cluster_info",
    "initialize_ray",
    "shutdown_ray",
]


@dataclass(frozen=True)
class RayResources:
    """Resource allocation for Ray cluster execution."""

    num_cpus: int = 2
    num_gpus: int = 0
    memory_mb: int = 4096
    object_store_memory_mb: int = 512


@dataclass(frozen=True)
class RayClusterInfo:
    """Information about the current Ray cluster."""

    available: bool
    num_cpus: int = 0
    num_gpus: int = 0
    memory_bytes: int = 0
    address: str = ""


def detect_gpu() -> int:
    """Detect available GPUs via Ray.

    Returns:
        Number of available GPUs, or 0 if Ray is not running.
    """
    try:
        import ray

        if not ray.is_initialized():
            return 0
        cluster_resources = ray.cluster_resources()
        return cluster_resources.get("GPU", 0)
    except ImportError:
        return 0
    except Exception as exc:
        logger.warning("gpu_detection_failed", error=str(exc))
        return 0


def get_cluster_info() -> RayClusterInfo:
    """Get information about the current Ray cluster.

    Returns:
        RayClusterInfo with cluster details.
    """
    try:
        import ray

        if not ray.is_initialized():
            return RayClusterInfo(available=False)

        resources = ray.cluster_resources()
        try:
            address = ray.get_runtime_context().get_address()
        except Exception:
            address = ""
        return RayClusterInfo(
            available=True,
            num_cpus=int(resources.get("CPU", 0)),
            num_gpus=int(resources.get("GPU", 0)),
            memory_bytes=int(resources.get("memory", 0)),
            address=address,
        )
    except (ImportError, Exception):
        return RayClusterInfo(available=False)


def initialize_ray(
    *,
    address: str | None = None,
    num_cpus: int | None = None,
    num_gpus: int | None = None,
    object_store_memory: int | None = None,
    include_dashboard: bool = False,
) -> bool:
    """Initialize Ray cluster for distributed execution.

    Args:
        address: Ray cluster address to connect to. None = local cluster.
        num_cpus: Number of CPUs for local cluster.
        num_gpus: Number of GPUs for local cluster.
        object_store_memory: Object store memory in bytes.
        include_dashboard: Whether to launch Ray dashboard.

    Returns:
        True if initialization succeeded, False otherwise.
    """
    try:
        import ray

        if ray.is_initialized():
            logger.info("ray_already_initialized")
            return True

        init_kwargs: dict[str, Any] = {
            "include_dashboard": include_dashboard,
        }
        if address is not None:
            init_kwargs["address"] = address
        if num_cpus is not None:
            init_kwargs["num_cpus"] = num_cpus
        if num_gpus is not None:
            init_kwargs["num_gpus"] = num_gpus
        if object_store_memory is not None:
            init_kwargs["object_store_memory"] = object_store_memory

        ray.init(**init_kwargs)

        info = get_cluster_info()
        logger.info(
            "ray_initialized",
            address=info.address,
            cpus=info.num_cpus,
            gpus=info.num_gpus,
        )
        return True
    except ImportError:
        logger.warning("ray_not_installed", message="Ray not available")
        return False
    except Exception as exc:
        logger.error("ray_init_failed", error=str(exc))
        return False


def shutdown_ray() -> None:
    """Shutdown Ray cluster gracefully."""
    try:
        import ray

        if ray.is_initialized():
            ray.shutdown()
            logger.info("ray_shutdown")
    except ImportError:
        pass
