"""Arrow Lake quality module.

Provides pluggable data quality filtering, dead-letter persistence,
schema validation, and content deduplication.
"""

from arrow_lake.quality.base import QualityFilter, QualityFilterRegistry
from arrow_lake.quality.models import FilterResult, QualityReport
from arrow_lake.quality.scoring import compute_quality_scores

__all__ = [
    "ContentDeduplicator",
    "FilterResult",
    "QualityFilter",
    "QualityFilterRegistry",
    "QualityReport",
    "compute_quality_scores",
]

try:
    import arrow_lake.quality.nemo_curator

    __all__.append("NeMoCuratorFilter")
except ImportError:
    pass

try:
    import arrow_lake.quality.dedup  # noqa: F401

    __all__.append("ContentDeduplicator")
except ImportError:
    pass
