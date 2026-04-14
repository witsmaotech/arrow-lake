"""Arrow Lake Ray runtime module (Epic 6).

Provides distributed execution infrastructure for Metaflow workflows.
"""

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
    "PrefetchConfig",
    "ProcessingResult",
    "RayClusterInfo",
    "RayResources",
    "RemoteDataLoader",
    "create_torch_dataloader",
    "detect_gpu",
    "foreach",
    "get_cluster_info",
    "initialize_ray",
    "shutdown_ray",
]
