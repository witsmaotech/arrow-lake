"""Audit anomaly detection — frequency spikes, actor anomalies, off-hours activity."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class AnomalyRecord:
    """A single detected anomaly in the audit trail."""

    anomaly_type: str
    severity: str
    description: str
    affected_events: int
    detected_at: str


class AuditAnalyzer:
    """Statistical anomaly detection over audit trail entries."""

    def __init__(self, entries: list[dict[str, Any]]) -> None:
        self._entries = entries

    def _now_iso(self) -> str:
        return datetime.now(UTC).isoformat()

    def detect_frequency_anomalies(
        self,
        *,
        window_hours: int = 24,
        zscore_threshold: float = 3.0,
    ) -> list[AnomalyRecord]:
        """Detect event frequency spikes using z-score on hourly bin counts."""
        now = datetime.now(UTC)
        cutoff = now.timestamp() - window_hours * 3600

        recent = [
            e for e in self._entries
            if e.get("timestamp", "") and _ts_to_epoch(e["timestamp"]) >= cutoff
        ]

        if len(recent) < 5:
            return []

        grouped: dict[str, list[dict]] = {}
        for e in recent:
            key = f"{e.get('event_type', 'unknown')}:{e.get('dataset_name', '')}"
            grouped.setdefault(key, []).append(e)

        anomalies: list[AnomalyRecord] = []
        for key, events in grouped.items():
            timestamps = [_ts_to_epoch(e["timestamp"]) for e in events if e.get("timestamp")]
            if not timestamps:
                continue

            min_ts = min(timestamps)
            num_bins = max(2, int((max(timestamps) - min_ts) / 3600) + 1)
            bin_size = max(1.0, (max(timestamps) - min_ts) / num_bins)

            bins: dict[int, int] = {}
            for ts in timestamps:
                b = int((ts - min_ts) / bin_size)
                bins[b] = bins.get(b, 0) + 1

            counts = list(bins.values())
            if len(counts) < 2:
                continue

            mean_c = statistics.mean(counts)
            std_c = statistics.stdev(counts)
            if std_c == 0:
                continue

            max_bin = max(bins, key=bins.get)
            z = (bins[max_bin] - mean_c) / std_c
            if z >= zscore_threshold:
                anomalies.append(AnomalyRecord(
                    anomaly_type="frequency_spike",
                    severity="high" if z >= 5 else "medium",
                    description=f"Frequency spike for {key}: z={z:.1f}",
                    affected_events=bins[max_bin],
                    detected_at=self._now_iso(),
                ))

        return anomalies

    def detect_actor_anomalies(
        self,
        *,
        window_hours: int = 168,
    ) -> list[AnomalyRecord]:
        """Detect actors with unusual activity volume compared to baseline."""
        now = datetime.now(UTC)
        cutoff = now.timestamp() - window_hours * 3600

        recent = [
            e for e in self._entries
            if e.get("timestamp", "") and _ts_to_epoch(e["timestamp"]) >= cutoff
        ]

        if len(recent) < 10:
            return []

        actor_counts: dict[str, int] = {}
        for e in recent:
            actor = e.get("actor", "unknown")
            actor_counts[actor] = actor_counts.get(actor, 0) + 1

        if len(actor_counts) < 2:
            return []

        counts = list(actor_counts.values())
        mean_c = statistics.mean(counts)
        std_c = statistics.stdev(counts) if len(counts) > 1 else 0

        anomalies: list[AnomalyRecord] = []
        if std_c > 0:
            for actor, count in actor_counts.items():
                z = (count - mean_c) / std_c
                if z >= 3.0:
                    anomalies.append(AnomalyRecord(
                        anomaly_type="actor_anomaly",
                        severity="high" if z >= 5 else "medium",
                        description=f"Actor '{actor}' has {count} events (z={z:.1f})",
                        affected_events=count,
                        detected_at=self._now_iso(),
                    ))

        return anomalies

    def detect_off_hours_activity(
        self,
        *,
        start_hour: int = 0,
        end_hour: int = 6,
        min_events: int = 10,
    ) -> list[AnomalyRecord]:
        """Detect significant activity outside expected hours."""
        off_hours: list[dict] = []
        for e in self._entries:
            ts = e.get("timestamp", "")
            if not ts:
                continue
            epoch = _ts_to_epoch(ts)
            if epoch is None:
                continue
            hour = datetime.fromtimestamp(epoch, tz=UTC).hour
            if start_hour <= hour < end_hour:
                off_hours.append(e)

        if len(off_hours) < min_events:
            return []

        return [
            AnomalyRecord(
                anomaly_type="off_hours",
                severity="medium" if len(off_hours) < 50 else "high",
                description=(
                    f"{len(off_hours)} events between {start_hour}:00-{end_hour}:00 UTC"
                ),
                affected_events=len(off_hours),
                detected_at=self._now_iso(),
            )
        ]

    def analyze(self) -> list[AnomalyRecord]:
        """Run all anomaly detectors and return combined results."""
        results: list[AnomalyRecord] = []
        results.extend(self.detect_frequency_anomalies())
        results.extend(self.detect_actor_anomalies())
        results.extend(self.detect_off_hours_activity())
        return sorted(results, key=lambda r: r.severity, reverse=True)


def _ts_to_epoch(ts: str) -> float | None:
    """Convert an ISO timestamp string to epoch seconds."""
    try:
        dt = datetime.fromisoformat(ts)
        return dt.timestamp()
    except (ValueError, TypeError, OverflowError):
        return None
