"""Tests for audit anomaly detection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from arrow_lake.workflow.audit_analyzer import AuditAnalyzer


def _make_entries(
    n: int = 100,
    actors: list[str] | None = None,
    hours_ago: float = 0.1,
    off_hours: bool = False,
) -> list[dict]:
    now = datetime.now(UTC)
    entries = []
    for i in range(n):
        if off_hours:
            ts = (now - timedelta(hours=hours_ago)).replace(hour=3, minute=0).isoformat()
        else:
            ts = (now - timedelta(hours=hours_ago + i * 0.001)).isoformat()
        entries.append({
            "audit_id": f"a{i}",
            "timestamp": ts,
            "event_type": "create",
            "dataset_name": "ds1",
            "actor": actors[i % len(actors)] if actors else "system",
            "payload": {},
        })
    return entries


class TestFrequencyAnomalies:
    def test_no_anomaly_with_normal_traffic(self) -> None:
        entries = _make_entries(n=50)
        analyzer = AuditAnalyzer(entries)
        result = analyzer.detect_frequency_anomalies()
        assert result == []

    def test_insufficient_data(self) -> None:
        entries = _make_entries(n=2)
        analyzer = AuditAnalyzer(entries)
        result = analyzer.detect_frequency_anomalies()
        assert result == []

    def test_spike_detected_with_dense_cluster(self) -> None:
        now = datetime.now(UTC)
        entries = []
        for h in range(1, 49):
            ts = now - timedelta(hours=h)
            entries.append({
                "audit_id": f"b{h}",
                "timestamp": ts.isoformat(),
                "event_type": "delete",
                "dataset_name": "ds1",
                "actor": "system",
            })
        dense_time = now - timedelta(seconds=1)
        for i in range(200):
            entries.append({
                "audit_id": f"s{i}",
                "timestamp": (dense_time + timedelta(microseconds=i * 100)).isoformat(),
                "event_type": "delete",
                "dataset_name": "ds1",
                "actor": "system",
            })
        analyzer = AuditAnalyzer(entries)
        result = analyzer.detect_frequency_anomalies(window_hours=168)
        assert len(result) >= 1
        assert result[0].anomaly_type == "frequency_spike"


class TestActorAnomalies:
    def test_no_anomaly_single_actor(self) -> None:
        entries = _make_entries(n=50, actors=["user1"])
        analyzer = AuditAnalyzer(entries)
        result = analyzer.detect_actor_anomalies()
        assert result == []

    def test_insufficient_data(self) -> None:
        entries = _make_entries(n=5)
        analyzer = AuditAnalyzer(entries)
        result = analyzer.detect_actor_anomalies()
        assert result == []

    def test_detects_imbalanced_actors(self) -> None:
        entries = []
        now = datetime.now(UTC)
        for i in range(500):
            entries.append({
                "audit_id": f"c{i}",
                "timestamp": (now - timedelta(hours=0.1 + i * 0.0001)).isoformat(),
                "event_type": "create",
                "dataset_name": "ds1",
                "actor": "busy_user",
            })
        for actor in [f"user{j}" for j in range(10)]:
            for i in range(3):
                entries.append({
                    "audit_id": f"d_{actor}_{i}",
                    "timestamp": (now - timedelta(hours=0.1 + i * 0.1)).isoformat(),
                    "event_type": "create",
                    "dataset_name": "ds1",
                    "actor": actor,
                })
        analyzer = AuditAnalyzer(entries)
        result = analyzer.detect_actor_anomalies()
        busy = [r for r in result if "busy_user" in r.description]
        assert len(busy) >= 1


class TestOffHours:
    def test_no_off_hours(self) -> None:
        entries = _make_entries(n=5)
        analyzer = AuditAnalyzer(entries)
        result = analyzer.detect_off_hours_activity()
        assert result == []

    def test_off_hours_detected(self) -> None:
        entries = _make_entries(n=15, off_hours=True)
        analyzer = AuditAnalyzer(entries)
        result = analyzer.detect_off_hours_activity(min_events=5)
        assert len(result) == 1
        assert result[0].anomaly_type == "off_hours"

    def test_below_min_threshold(self) -> None:
        entries = _make_entries(n=3, off_hours=True)
        analyzer = AuditAnalyzer(entries)
        result = analyzer.detect_off_hours_activity(min_events=5)
        assert result == []


class TestAnalyze:
    def test_analyze_runs_all(self) -> None:
        entries = _make_entries(n=15, off_hours=True)
        analyzer = AuditAnalyzer(entries)
        result = analyzer.analyze()
        assert isinstance(result, list)

    def test_empty_entries(self) -> None:
        analyzer = AuditAnalyzer([])
        result = analyzer.analyze()
        assert result == []
