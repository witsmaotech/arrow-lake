"""Arrow Lake Ray runtime module (Epic 6, Sprint 7).

Provides distributed execution infrastructure for Metaflow workflows
and GPU autoscaling for production deployments.
"""

from arrow_lake.ray_runtime.autoscaler import GPUAutoscaler, ScalingEvent
from arrow_lake.ray_runtime.cluster import (
    RayClusterInfo,
    RayResources,
    detect_gpu,
    get_cluster_info,
    initialize_ray,
    shutdown_ray,
)
from arrow_lake.ray_runtime.data_loader import (
    PrefetchConfig,
    RemoteDataLoader,
    create_torch_dataloader,
)
from arrow_lake.ray_runtime.distributed import AutoScaleConfig, ProcessingResult, foreach

__all__ = [
    "AutoScaleConfig",
    "GPUAutoscaler",
    "PrefetchConfig",
    "ProcessingResult",
    "RayClusterInfo",
    "RayResources",
    "RemoteDataLoader",
    "ScalingEvent",
    "create_torch_dataloader",
    "detect_gpu",
    "foreach",
    "get_cluster_info",
    "initialize_ray",
    "shutdown_ray",
]
