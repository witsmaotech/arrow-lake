"""Tests for Story 7.14 — CI/CD workflow YAML validation."""

from __future__ import annotations

from pathlib import Path

import yaml


def _load_workflow(name: str) -> dict:
    path = Path(f".github/workflows/{name}")
    with path.open() as f:
        return yaml.safe_load(f)


class TestCIWorkflow:
    """Validate .github/workflows/ci.yml."""

    def test_file_exists(self) -> None:
        assert Path(".github/workflows/ci.yml").exists()

    def test_ci_has_required_jobs(self) -> None:
        ci = _load_workflow("ci.yml")
        assert "jobs" in ci
        jobs = list(ci["jobs"].keys())
        # v1.11.5-W1-2: three parallel jobs (lint / tests / build-and-smoke)
        assert "lint" in jobs
        assert "tests" in jobs
        assert "build-and-smoke" in jobs

    def test_ci_has_required_steps(self) -> None:
        ci = _load_workflow("ci.yml")
        lint_names = [s.get("name", "") for s in ci["jobs"]["lint"]["steps"]]
        test_names = [s.get("name", "") for s in ci["jobs"]["tests"]["steps"]]
        build_names = [s.get("name", "") for s in ci["jobs"]["build-and-smoke"]["steps"]]

        assert any("ruff" in n.lower() for n in lint_names), "Missing ruff lint step"
        assert any("test" in n.lower() for n in test_names), "Missing test suite step"
        assert any("smoke" in n.lower() for n in build_names), "Missing smoke step"

    def test_ci_runs_on_push_and_pr(self) -> None:
        ci = _load_workflow("ci.yml")
        on_config = ci.get("on", ci) if "on" in ci else ci.get(True, {})
        # YAML 'on' maps to Python True
        on_config = ci.get(True, ci.get("on", {}))
        assert "push" in on_config or "pull_request" in on_config

    def test_ci_uses_uv(self) -> None:
        ci = _load_workflow("ci.yml")
        # uv appears in run: commands (uv sync / uv run pytest / uv run ruff)
        for job in ci["jobs"].values():
            for s in job.get("steps", []):
                if "uv " in str(s.get("run", "")):
                    return
        raise AssertionError("No step invokes uv")


class TestNightlyGPUWorkflow:
    """Validate .github/workflows/nightly-gpu.yml."""

    def test_file_exists(self) -> None:
        assert Path(".github/workflows/nightly-gpu.yml").exists()

    def test_has_cron_trigger(self) -> None:
        wf = _load_workflow("nightly-gpu.yml")
        on_config = wf.get(True, wf.get("on", {}))
        assert "schedule" in on_config
        assert len(on_config["schedule"]) >= 1

    def test_has_workflow_dispatch(self) -> None:
        wf = _load_workflow("nightly-gpu.yml")
        on_config = wf.get(True, wf.get("on", {}))
        assert "workflow_dispatch" in on_config

    def test_has_gpu_tests_job(self) -> None:
        wf = _load_workflow("nightly-gpu.yml")
        assert "jobs" in wf
        assert "gpu-tests" in wf["jobs"]


class TestReleaseWorkflow:
    """Validate .github/workflows/release.yml."""

    def test_file_exists(self) -> None:
        assert Path(".github/workflows/release.yml").exists()

    def test_triggers_on_version_tags(self) -> None:
        wf = _load_workflow("release.yml")
        on_config = wf.get(True, wf.get("on", {}))
        assert "push" in on_config
        assert "tags" in on_config["push"]
        assert any("v*" in t for t in on_config["push"]["tags"])

    def test_has_publish_step(self) -> None:
        wf = _load_workflow("release.yml")
        jobs = list(wf["jobs"].keys())
        assert any(
            "publish" in j.lower() or "release" in j.lower() or "build" in j.lower() for j in jobs
        )

    def test_has_test_step_before_publish(self) -> None:
        wf = _load_workflow("release.yml")
        job_name = next(iter(wf["jobs"].keys()))
        steps = wf["jobs"][job_name]["steps"]
        step_names = [s.get("name", "") for s in steps]
        test_idx = next((i for i, n in enumerate(step_names) if "test" in n.lower()), None)
        publish_idx = next((i for i, n in enumerate(step_names) if "publish" in n.lower()), None)
        assert test_idx is not None, "Missing test step"
        assert publish_idx is not None, "Missing publish step"
        assert test_idx < publish_idx, "Tests should run before publish"
