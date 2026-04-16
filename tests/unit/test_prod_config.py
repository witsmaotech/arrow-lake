"""Tests for Story 6.12 — Production deployment configuration."""

from __future__ import annotations

import json

import yaml


class TestDockerCompose:
    """Validate docker-compose.yml structure."""

    def test_file_exists(self) -> None:
        from pathlib import Path

        path = Path("deploy/docker-compose.yml")
        assert path.exists(), "docker-compose.yml not found"

    def test_valid_yaml(self) -> None:
        from pathlib import Path

        path = Path("deploy/docker-compose.yml")
        with path.open() as f:
            compose = yaml.safe_load(f)

        assert "services" in compose
        services = compose["services"]
        assert "minio" in services
        assert "ray-head" in services
        assert "ray-worker" in services

    def test_api_service_exists(self) -> None:
        from pathlib import Path

        path = Path("deploy/docker-compose.yml")
        with path.open() as f:
            compose = yaml.safe_load(f)

        services = compose["services"]
        assert "api" in services

    def test_healthcheck_configured(self) -> None:
        from pathlib import Path

        path = Path("deploy/docker-compose.yml")
        with path.open() as f:
            compose = yaml.safe_load(f)

        services = compose["services"]
        minio = services["minio"]
        assert "healthcheck" in minio
        assert "test" in minio["healthcheck"]

    def test_environment_variables(self) -> None:
        from pathlib import Path

        path = Path("deploy/docker-compose.yml")
        with path.open() as f:
            compose = yaml.safe_load(f)

        ray_head = compose["services"]["ray-head"]
        env = ray_head.get("environment", {})
        assert "ARROW_LAKE__STORAGE__BACKEND" in env
        assert "ARROW_LAKE__OBSERVABILITY__METRICS_ENABLED" in env


class TestEnvExample:
    """Validate .env.example has production-relevant variables."""

    def test_file_exists(self) -> None:
        from pathlib import Path

        path = Path(".env.example")
        assert path.exists(), ".env.example not found"

    def test_contains_storage_config(self) -> None:
        from pathlib import Path

        content = Path(".env.example").read_text()
        assert "ARROW_LAKE__STORAGE__BACKEND" in content
        assert "ARROW_LAKE__STORAGE__S3_ENDPOINT" in content

    def test_contains_observability_config(self) -> None:
        from pathlib import Path

        content = Path(".env.example").read_text()
        assert "ARROW_LAKE__OBSERVABILITY__METRICS_ENABLED" in content
        assert "ARROW_LAKE__OBSERVABILITY__LOG_LEVEL" in content


class TestHealthEndpoint:
    """Test the WSGI health check app."""

    def test_health_returns_json(self) -> None:
        from arrow_lake.server import app

        environ = {"PATH_INFO": "/health"}
        response_started: dict[str, str] = {}

        def start_response(status: str, headers: list[tuple[str, str]]) -> None:
            response_started["status"] = status
            response_started["headers"] = headers

        result = app(environ, start_response)
        body = json.loads(result[0].decode())
        assert body["status"] in ("ok", "degraded")
        assert "storage" in body
        assert "catalog" in body
        assert response_started["status"].startswith("200") or response_started[
            "status"
        ].startswith("503")

    def test_metrics_endpoint(self) -> None:
        from arrow_lake.server import app

        environ = {"PATH_INFO": "/metrics"}

        def start_response(status: str, headers: list[tuple[str, str]]) -> None:
            pass

        result = app(environ, start_response)
        assert len(result) == 1
        body = result[0] if isinstance(result[0], str) else result[0].decode()
        assert "arrow_lake" in body or body.startswith("#")

    def test_404_for_unknown_path(self) -> None:
        from arrow_lake.server import app

        environ = {"PATH_INFO": "/unknown"}

        def start_response(status: str, headers: list[tuple[str, str]]) -> None:
            pass

        result = app(environ, start_response)
        assert b"Not Found" in result[0]
