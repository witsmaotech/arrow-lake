"""Tests for Story 7.3 — Argo Workflows Basic Deployment."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import yaml
from arrow_lake.config import ArgoConfig
from arrow_lake.exceptions import ArgoError, ErrorCode
from arrow_lake.workflow.argo import ArgoWorkflowBridge, _validate_workflow_structure


def _make_valid_workflow(**overrides: object) -> dict:
    """Create a minimal valid Argo Workflow dict."""
    workflow: dict = {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Workflow",
        "metadata": {"name": "test-workflow"},
        "spec": {
            "entrypoint": "main",
            "templates": [
                {
                    "name": "main",
                    "container": {
                        "image": "arrow-lake:latest",
                        "command": ["python", "flow.py"],
                        "env": [],
                    },
                }
            ],
        },
    }
    workflow.update(overrides)
    return workflow


class TestArgoWorkflowBridgeInit:
    """Test ArgoWorkflowBridge initialization."""

    def test_default_config(self) -> None:
        bridge = ArgoWorkflowBridge()
        assert bridge.config.namespace == "default"
        assert bridge.config.service_account == "arrow-lake"
        assert bridge.config.workflow_timeout == 3600

    def test_custom_config(self) -> None:
        cfg = ArgoConfig(namespace="production", workflow_timeout=7200)
        bridge = ArgoWorkflowBridge(config=cfg)
        assert bridge.config.namespace == "production"
        assert bridge.config.workflow_timeout == 7200

    def test_config_property(self) -> None:
        bridge = ArgoWorkflowBridge()
        assert isinstance(bridge.config, ArgoConfig)


class TestValidateWorkflow:
    """Test workflow YAML validation."""

    def test_valid_workflow_passes(self) -> None:
        wf = _make_valid_workflow()
        assert _validate_workflow_structure(wf) is True

    def test_valid_cronworkflow_passes(self) -> None:
        wf = _make_valid_workflow(kind="CronWorkflow")
        assert _validate_workflow_structure(wf) is True

    def test_missing_api_version_raises(self) -> None:
        wf = _make_valid_workflow()
        del wf["apiVersion"]
        with pytest.raises(ArgoError, match="Missing required"):
            _validate_workflow_structure(wf)

    def test_missing_kind_raises(self) -> None:
        wf = _make_valid_workflow()
        del wf["kind"]
        with pytest.raises(ArgoError, match="Missing required"):
            _validate_workflow_structure(wf)

    def test_invalid_kind_raises(self) -> None:
        wf = _make_valid_workflow(kind="Pod")
        with pytest.raises(ArgoError, match="Invalid kind"):
            _validate_workflow_structure(wf)

    def test_missing_spec_raises(self) -> None:
        wf = _make_valid_workflow()
        del wf["spec"]
        with pytest.raises(ArgoError, match="Missing required"):
            _validate_workflow_structure(wf)

    def test_spec_missing_templates_raises(self) -> None:
        wf = _make_valid_workflow()
        wf["spec"] = {"entrypoint": "main"}
        with pytest.raises(ArgoError, match="templates"):
            _validate_workflow_structure(wf)

    def test_empty_yaml_raises(self) -> None:
        with pytest.raises(ArgoError):
            ArgoWorkflowBridge().validate_workflow("")

    def test_invalid_yaml_raises(self) -> None:
        with pytest.raises(ArgoError, match="Invalid YAML"):
            ArgoWorkflowBridge().validate_workflow(": not yaml: [")

    def test_non_mapping_yaml_raises(self) -> None:
        with pytest.raises(ArgoError, match="must be a mapping"):
            ArgoWorkflowBridge().validate_workflow("just a string")

    def test_validate_via_bridge(self) -> None:
        wf = _make_valid_workflow()
        yaml_str = yaml.dump(wf)
        assert ArgoWorkflowBridge().validate_workflow(yaml_str) is True


class TestGenerateWorkflow:
    """Test workflow generation (mocked)."""

    def test_generates_valid_yaml_for_flow(self) -> None:
        wf = _make_valid_workflow()
        with patch("arrow_lake.workflow.argo.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=yaml.dump(wf), stderr="")
            bridge = ArgoWorkflowBridge()
            result = bridge.generate_workflow(MagicMock(__module__="flows.test_flow"))
            parsed = yaml.safe_load(result)
            assert parsed["kind"] == "Workflow"

    def test_injects_arrow_lake_env_vars(self) -> None:
        wf = _make_valid_workflow()
        with patch("arrow_lake.workflow.argo.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=yaml.dump(wf), stderr="")
            bridge = ArgoWorkflowBridge()
            result = bridge.generate_workflow(MagicMock(__module__="flows.test_flow"))
            parsed = yaml.safe_load(result)
            env = parsed["spec"]["templates"][0]["container"]["env"]
            env_names = {e["name"] for e in env}
            assert "ARROW_LAKE__STORAGE__S3_ENDPOINT" in env_names
            assert "ARROW_LAKE__OBSERVABILITY__METRICS_ENABLED" in env_names

    def test_generates_with_custom_config(self) -> None:
        wf = _make_valid_workflow()
        with patch("arrow_lake.workflow.argo.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=yaml.dump(wf), stderr="")
            cfg = ArgoConfig(namespace="staging")
            bridge = ArgoWorkflowBridge()
            bridge.generate_workflow(MagicMock(__module__="flows.test"), config=cfg)
            call_args = mock_run.call_args[0][0]
            assert "--namespace=staging" not in call_args  # generate, not deploy

    def test_generation_failure_raises_argo_error(self) -> None:
        with patch("arrow_lake.workflow.argo.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Flow not found")
            bridge = ArgoWorkflowBridge()
            with pytest.raises(ArgoError, match=ErrorCode.ARGO_GENERATION_FAILED):
                bridge.generate_workflow(MagicMock(__module__="flows.test"))

    def test_cli_not_found_raises_argo_error(self) -> None:
        with patch(
            "arrow_lake.workflow.argo.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            bridge = ArgoWorkflowBridge()
            with pytest.raises(ArgoError, match="not found"):
                bridge.generate_workflow(MagicMock(__module__="flows.test"))

    def test_timeout_raises_argo_error(self) -> None:
        import subprocess

        with patch(
            "arrow_lake.workflow.argo.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="test", timeout=3600),
        ):
            bridge = ArgoWorkflowBridge()
            with pytest.raises(ArgoError, match="timed out"):
                bridge.generate_workflow(MagicMock(__module__="flows.test"))


class TestDeployWorkflow:
    """Test workflow deployment (mocked)."""

    def test_deploy_returns_workflow_name(self) -> None:
        with patch("arrow_lake.workflow.argo.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="deployed", stderr="")
            bridge = ArgoWorkflowBridge()

            class FakeFlow: ...

            FakeFlow.__name__ = "TestFlow"
            result = bridge.deploy_workflow(FakeFlow)
            assert result == "TestFlow"

    def test_deploy_with_namespace(self) -> None:
        with patch("arrow_lake.workflow.argo.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="deployed", stderr="")
            cfg = ArgoConfig(namespace="production", service_account="arrow-lake-prod")
            bridge = ArgoWorkflowBridge()
            bridge.deploy_workflow(MagicMock(__name__="TestFlow"), config=cfg)
            call_args = mock_run.call_args[0][0]
            assert "--namespace=production" in call_args
            assert "--service-account=arrow-lake-prod" in call_args

    def test_deploy_failure_raises_argo_error(self) -> None:
        with patch("arrow_lake.workflow.argo.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Permission denied")
            bridge = ArgoWorkflowBridge()
            with pytest.raises(ArgoError, match=ErrorCode.ARGO_DEPLOY_FAILED):
                bridge.deploy_workflow(MagicMock(__name__="TestFlow"))


class TestInjectEnvironment:
    """Test Arrow Lake environment variable injection."""

    def test_adds_storage_env_vars(self) -> None:
        wf = _make_valid_workflow()
        bridge = ArgoWorkflowBridge()
        result = bridge._inject_arrow_lake_env(wf)
        env = result["spec"]["templates"][0]["container"]["env"]
        env_names = {e["name"] for e in env}
        assert "ARROW_LAKE__STORAGE__S3_ENDPOINT" in env_names
        assert "ARROW_LAKE__STORAGE__S3_BUCKET" in env_names

    def test_adds_observability_env_vars(self) -> None:
        wf = _make_valid_workflow()
        bridge = ArgoWorkflowBridge()
        result = bridge._inject_arrow_lake_env(wf)
        env = result["spec"]["templates"][0]["container"]["env"]
        env_names = {e["name"] for e in env}
        assert "ARROW_LAKE__OBSERVABILITY__METRICS_ENABLED" in env_names

    def test_preserves_existing_env_vars(self) -> None:
        wf = _make_valid_workflow()
        wf["spec"]["templates"][0]["container"]["env"] = [{"name": "MY_VAR", "value": "keep_me"}]
        bridge = ArgoWorkflowBridge()
        result = bridge._inject_arrow_lake_env(wf)
        env = result["spec"]["templates"][0]["container"]["env"]
        env_names = {e["name"] for e in env}
        assert "MY_VAR" in env_names
        assert "keep_me" in [e["value"] for e in env if e["name"] == "MY_VAR"]

    def test_does_not_duplicate_env_vars(self) -> None:
        wf = _make_valid_workflow()
        wf["spec"]["templates"][0]["container"]["env"] = [
            {"name": "ARROW_LAKE__STORAGE__S3_ENDPOINT", "value": "original"}
        ]
        bridge = ArgoWorkflowBridge()
        result = bridge._inject_arrow_lake_env(wf)
        env = result["spec"]["templates"][0]["container"]["env"]
        endpoint_envs = [e for e in env if e["name"] == "ARROW_LAKE__STORAGE__S3_ENDPOINT"]
        assert len(endpoint_envs) == 1
        assert endpoint_envs[0]["value"] == "original"

    def test_multiple_containers_all_updated(self) -> None:
        wf = _make_valid_workflow()
        wf["spec"]["templates"].append(
            {
                "name": "sidecar",
                "container": {"image": "other", "env": []},
            }
        )
        bridge = ArgoWorkflowBridge()
        result = bridge._inject_arrow_lake_env(wf)
        for template in result["spec"]["templates"]:
            env_names = {e["name"] for e in template.get("container", {}).get("env", [])}
            assert "ARROW_LAKE__STORAGE__S3_ENDPOINT" in env_names
