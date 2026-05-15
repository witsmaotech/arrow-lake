"""Tests for Story 7.11 — Docker Network Isolation and Security."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_DEFAULT_PORT_RE = re.compile(r":-(\d+)}")


def _load_compose(path: str = "deploy/docker-compose.yml") -> dict:
    p = Path(path)
    if not p.exists():
        pytest.skip(f"{path} not found")
    with p.open() as f:
        return yaml.safe_load(f)


def _extract_host_port(port_spec: str) -> int:
    """Extract default host port from Docker Compose port spec.

    Handles formats: "${VAR:-8000}:8000", "8000:8000", 8000
    """
    m = _DEFAULT_PORT_RE.search(port_spec)
    if m:
        return int(m.group(1))
    host_part = str(port_spec).split(":")[0]
    return int(host_part)


def _load_prometheus(path: str = "deploy/monitoring/prometheus/prometheus.yml") -> dict:
    p = Path(path)
    if not p.exists():
        pytest.skip(f"{path} not found")
    with p.open() as f:
        return yaml.safe_load(f)


class TestDockerComposeNetworkIsolation:
    """Verify docker-compose.yml has network isolation."""

    def test_has_dedicated_network_definition(self) -> None:
        compose = _load_compose()
        assert "networks" in compose, "No 'networks' section found"
        assert "arrow-lake-net" in compose["networks"]

    def test_all_services_use_internal_network(self) -> None:
        compose = _load_compose()
        services = compose.get("services", {})
        for name, svc in services.items():
            if svc.get("network_mode") == "host":
                continue
            nets = svc.get("networks", [])
            if isinstance(nets, str):
                nets = [nets]
            assert "arrow-lake-net" in nets, (
                f"Service '{name}' is not on arrow-lake-net network"
            )

    def test_ray_gcs_port_not_exported(self) -> None:
        compose = _load_compose()
        for svc in compose.get("services", {}).values():
            for port_spec in svc.get("ports", []):
                port_str = str(port_spec) if not isinstance(port_spec, dict) else str(port_spec.get("published", ""))
                assert _extract_host_port(port_str) != 6379, "Ray GCS port 6379 should not be exposed"


class TestPortExposureRestrictions:
    """Verify only allowed ports are exposed."""

    @staticmethod
    def _extract_default_ports(compose: dict) -> set[int]:
        ports: set[int] = set()
        for svc in compose.get("services", {}).values():
            for port_spec in svc.get("ports", []):
                port_str = str(port_spec) if not isinstance(port_spec, dict) else str(port_spec.get("published", ""))
                ports.add(_extract_host_port(port_str))
        return ports

    def test_allowed_ports_present(self) -> None:
        compose = _load_compose()
        exposed_ports = self._extract_default_ports(compose)
        assert 8000 in exposed_ports, "Metrics port 8000 should be exposed"
        assert 8265 in exposed_ports, "Ray Dashboard port 8265 should be exposed"
        assert 9000 in exposed_ports, "MinIO API port 9000 should be exposed"

    def test_no_unexpected_ports(self) -> None:
        allowed = {8000, 8265, 9000, 9001, 6378, 6380, 8888}
        compose = _load_compose()
        exposed_ports = self._extract_default_ports(compose)
        unexpected = exposed_ports - allowed
        assert not unexpected, f"Unexpected ports exposed: {unexpected}"


class TestPrometheusScrapeConfig:
    """Verify Prometheus scrapes from internal network only."""

    def test_targets_use_docker_service_names(self) -> None:
        prom = _load_prometheus()
        targets: list[str] = []
        for job in prom.get("scrape_configs", []):
            for sc in job.get("static_configs", []):
                targets.extend(sc.get("targets", []))
        for target in targets:
            assert "host.docker.internal" not in target, (
                f"Target '{target}' uses host.docker.internal (internal network only)"
            )

    def test_server_job_uses_arrow_lake_api_service(self) -> None:
        prom = _load_prometheus()
        server_job = None
        for job in prom.get("scrape_configs", []):
            if job.get("job_name") == "arrow-lake-server":
                server_job = job
                break
        assert server_job is not None, "arrow-lake-server job not found"
        targets = server_job["static_configs"][0]["targets"]
        assert any("arrow-lake-api" in t for t in targets), (
            "Server scrape target should use arrow-lake-api service name"
        )


class TestTLSCertScript:
    """Verify TLS cert generation script exists and is valid."""

    def test_cert_script_exists(self) -> None:
        p = Path("deploy/scripts/gen-certs.sh")
        assert p.exists(), "deploy/scripts/gen-certs.sh not found"

    def test_cert_script_is_executable(self) -> None:
        p = Path("deploy/scripts/gen-certs.sh")
        assert p.stat().st_mode & 0o111, "gen-certs.sh should be executable"

    def test_cert_script_mentions_openssl(self) -> None:
        p = Path("deploy/scripts/gen-certs.sh")
        content = p.read_text()
        assert "openssl" in content, "gen-certs.sh should use openssl"
        assert "server.key" in content, "Should output server.key"
        assert "server.crt" in content, "Should output server.crt"

    def test_cert_script_mentions_san(self) -> None:
        p = Path("deploy/scripts/gen-certs.sh")
        content = p.read_text()
        assert "subjectAltName" in content, "Should include SAN for localhost/127.0.0.1"
