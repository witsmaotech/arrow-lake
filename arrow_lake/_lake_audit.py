"""Audit mixin — audit trail recording, verification, querying, and export."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from arrow_lake.workflow.audit import AuditEntry, AuditTrail


class _LakeAuditMixin:
    """Provides audit trail recording, HMAC verification, querying, and export."""

    def _get_audit_trail(self) -> AuditTrail:
        """Lazy-init and cache the AuditTrail component."""
        from arrow_lake.workflow.audit import AuditTrail

        return self._get_component(
            "audit",
            lambda: AuditTrail(
                self._get_storage(),
                audit_dataset=self._config.audit.audit_dataset,
                hmac_secret_key=self._config.audit.hmac_secret_key,
            ),
        )

    def audit_record(
        self,
        event_type: str,
        dataset_name: str = "",
        actor: str = "system",
        lance_version: int | None = None,
        metaflow_run_id: str = "",
        metaflow_tags: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> str:
        """Record an audit entry (Story 8.4).

        Args:
            event_type: Type of event.
            dataset_name: Affected dataset name.
            actor: Who triggered the event.
            lance_version: Lance version at time of event.
            metaflow_run_id: Associated Metaflow run ID.
            metaflow_tags: Associated Metaflow tags.
            payload: Additional event data.

        Returns:
            The generated audit_id.
        """
        return self._get_audit_trail().record(
            event_type=event_type,
            dataset_name=dataset_name,
            actor=actor,
            lance_version=lance_version,
            metaflow_run_id=metaflow_run_id,
            metaflow_tags=metaflow_tags,
            payload=payload,
        )

    def audit_verify(self, audit_id: str) -> bool:
        """Verify HMAC integrity of an audit entry (Story 8.4).

        Args:
            audit_id: Audit entry ID to verify.

        Returns:
            True if intact, False if tampered or not found.
        """
        return self._get_audit_trail().verify(audit_id)

    def audit_query(
        self,
        dataset_name: str | None = None,
        start: str | None = None,
        end: str | None = None,
        event_type: str | None = None,
    ) -> list[AuditEntry]:
        """Query audit entries with optional filters (Story 8.4).

        Args:
            dataset_name: Filter by dataset name.
            start: ISO timestamp lower bound.
            end: ISO timestamp upper bound.
            event_type: Filter by event type.

        Returns:
            List of AuditEntry.
        """
        return self._get_audit_trail().query(
            dataset_name=dataset_name,
            start=start,
            end=end,
            event_type=event_type,
        )

    def audit_export(self, dataset_name: str) -> dict[str, Any]:
        """Export audit entries for a dataset (Story 8.4).

        Args:
            dataset_name: Dataset name to export.

        Returns:
            Dict with export metadata and entries.
        """
        return self._get_audit_trail().export(dataset_name)

    def audit_analyze(self) -> list[dict[str, Any]]:
        """Run anomaly detection on the audit trail.

        Returns:
            List of anomaly dicts sorted by severity.
        """
        from arrow_lake.workflow.audit_analyzer import AnomalyRecord

        entries = self._get_audit_trail().query()
        from arrow_lake.workflow.audit_analyzer import AuditAnalyzer

        analyzer = AuditAnalyzer(entries)
        results: list[dict[str, Any]] = []
        for r in analyzer.analyze():
            if isinstance(r, AnomalyRecord):
                results.append(asdict(r))
            elif hasattr(r, "__dict__"):
                results.append(r.__dict__)
            else:
                results.append(asdict(r))
        return results
