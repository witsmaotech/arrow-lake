"""Ontology (SHACL) gate configuration — v1.11.0 MS1 F1.3.

Sibling section of ``quality`` (the ingest gate); this one governs the
KG-build-finish ontology gate. Red lines: SHACL never enters the query
hot path — the only call site is the kg_build finisher plus the ontology
admin API.
"""

from __future__ import annotations

from pydantic import BaseModel, field_validator

from arrow_lake.config._enums import OntologyGateMode


class OntologyConfig(BaseModel):
    """Ontology gate + versioning configuration.

    Attributes:
        gate_mode: ``off`` / ``shadow`` (default) / ``enforce`` — see
            :class:`OntologyGateMode`. Shadow counts and reports without
            failing builds; the enforce flip is a post-baseline decision.
        validation_timeout_seconds: Upper bound for one pyshacl run at
            build finish. On timeout the gate fails CLOSED (counted as a
            reject) per the plan's risk mitigation — a stuck validation
            must never silently pass a build.
        max_violations_reported: Cap on violation rows carried in the
            task detail / error message (counts stay exact; only the
            sample is truncated).
    """

    gate_mode: OntologyGateMode = OntologyGateMode.SHADOW
    validation_timeout_seconds: float = 60.0
    max_violations_reported: int = 20

    @field_validator("validation_timeout_seconds")
    @classmethod
    def validate_timeout(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"validation_timeout_seconds must be > 0, got {v}")
        return v

    @field_validator("max_violations_reported")
    @classmethod
    def validate_max_violations(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"max_violations_reported must be >= 1, got {v}")
        return v
