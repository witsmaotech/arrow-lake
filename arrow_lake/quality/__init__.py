"""Arrow Lake quality module.

Provides pluggable data quality filtering, dead-letter persistence,
and schema validation.
"""

from arrow_lake.quality.base import QualityFilter, QualityFilterRegistry
from arrow_lake.quality.models import FilterResult, QualityReport
from arrow_lake.quality.scoring import compute_quality_scores

__all__ = [
    "FilterResult",
    "QualityFilter",
    "QualityFilterRegistry",
    "QualityReport",
    "compute_quality_scores",
]

try:
    import arrow_lake.quality.nemo_curator  # noqa: F401

    __all__.append("NeMoCuratorFilter")
except ImportError:
    pass
