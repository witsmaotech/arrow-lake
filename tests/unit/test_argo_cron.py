"""Tests for Story 7.4 — CronWorkflow Scheduling and Artifact Retention."""

from __future__ import annotations

from unittest.mock import MagicMock

import yaml
from arrow_lake.workflow.argo import ArgoWorkflowBridge


def _make_workflow_dict() -> dict:
    return {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Workflow",
        "metadata": {"name": "test-flow"},
        "spec": {
            "entrypoint": "main",
            "templates": [
                {
                    "name": "main",
                    "container": {
                        "image": "arrow-lake:latest",
                        "env": [],
                    },
                }
            ],
        },
    }


def _mock_generate(bridge: ArgoWorkflowBridge, workflow_dict: dict) -> None:
    """Patch generate_workflow to return the given dict as YAML."""
    yaml_str = yaml.dump(workflow_dict, default_flow_style=False)

    def fake_generate(flow_cls, config=None) -> str:
        return yaml_str

    bridge.generate_workflow = MagicMock(side_effect=fake_generate)


class TestGenerateCronWorkflow:
    """Test CronWorkflow generation."""

    def test_produces_cronworkflow_kind(self) -> None:
        wf = _make_workflow_dict()
        bridge = ArgoWorkflowBridge()
        _mock_generate(bridge, wf)
        result = bridge.generate_cron_workflow(
            MagicMock(__module__="flows.test"), cron_expression="0 2 * * *"
        )
        parsed = yaml.safe_load(result)
        assert parsed["kind"] == "CronWorkflow"

    def test_has_schedule_field(self) -> None:
        wf = _make_workflow_dict()
        bridge = ArgoWorkflowBridge()
        _mock_generate(bridge, wf)
        result = bridge.generate_cron_workflow(
            MagicMock(__module__="flows.test"), cron_expression="0 2 * * 1-5"
        )
        parsed = yaml.safe_load(result)
        assert parsed["spec"]["schedule"] == "0 2 * * 1-5"

    def test_schedule_matches_input(self) -> None:
        wf = _make_workflow_dict()
        bridge = ArgoWorkflowBridge()
        _mock_generate(bridge, wf)
        expr = "*/30 * * * *"
        result = bridge.generate_cron_workflow(
            MagicMock(__module__="flows.test"), cron_expression=expr
        )
        parsed = yaml.safe_load(result)
        assert parsed["spec"]["schedule"] == expr

    def test_ttl_strategy_with_retention_days(self) -> None:
        wf = _make_workflow_dict()
        bridge = ArgoWorkflowBridge()
        bridge._artifact_retention_days = 30
        _mock_generate(bridge, wf)
        result = bridge.generate_cron_workflow(
            MagicMock(__module__="flows.test"), cron_expression="0 * * * *"
        )
        parsed = yaml.safe_load(result)
        assert "ttlStrategy" in parsed["spec"]
        assert parsed["spec"]["ttlStrategy"]["secondsAfterCompletion"] == 30 * 86400

    def test_ttl_strategy_default_30_days(self) -> None:
        wf = _make_workflow_dict()
        bridge = ArgoWorkflowBridge()
        _mock_generate(bridge, wf)
        result = bridge.generate_cron_workflow(
            MagicMock(__module__="flows.test"), cron_expression="0 * * * *"
        )
        parsed = yaml.safe_load(result)
        assert parsed["spec"]["ttlStrategy"]["secondsAfterCompletion"] == 2592000


class TestSecretInjection:
    """Test K8s Secret reference injection."""

    def test_injects_s3_access_key_secret_ref(self) -> None:
        wf = _make_workflow_dict()
        bridge = ArgoWorkflowBridge()
        _mock_generate(bridge, wf)
        result = bridge.generate_cron_workflow(
            MagicMock(__module__="flows.test"), cron_expression="0 * * * *"
        )
        parsed = yaml.safe_load(result)
        env = parsed["spec"]["templates"][0]["container"]["env"]
        secret_envs = [e for e in env if e["name"] == "ARROW_LAKE__STORAGE__S3_ACCESS_KEY"]
        assert len(secret_envs) == 1
        assert secret_envs[0]["valueFrom"]["secretKeyRef"]["name"] == "arrow-lake-secrets"
        assert secret_envs[0]["valueFrom"]["secretKeyRef"]["key"] == "s3-access-key"

    def test_injects_s3_secret_key_secret_ref(self) -> None:
        wf = _make_workflow_dict()
        bridge = ArgoWorkflowBridge()
        _mock_generate(bridge, wf)
        result = bridge.generate_cron_workflow(
            MagicMock(__module__="flows.test"), cron_expression="0 * * * *"
        )
        parsed = yaml.safe_load(result)
        env = parsed["spec"]["templates"][0]["container"]["env"]
        secret_envs = [e for e in env if e["name"] == "ARROW_LAKE__STORAGE__S3_SECRET_KEY"]
        assert len(secret_envs) == 1
        assert secret_envs[0]["valueFrom"]["secretKeyRef"]["key"] == "s3-secret-key"

    def test_preserves_existing_env_vars(self) -> None:
        wf = _make_workflow_dict()
        wf["spec"]["templates"][0]["container"]["env"] = [{"name": "EXISTING_VAR", "value": "keep"}]
        bridge = ArgoWorkflowBridge()
        _mock_generate(bridge, wf)
        result = bridge.generate_cron_workflow(
            MagicMock(__module__="flows.test"), cron_expression="0 * * * *"
        )
        parsed = yaml.safe_load(result)
        env_names = {e["name"] for e in parsed["spec"]["templates"][0]["container"]["env"]}
        assert "EXISTING_VAR" in env_names

    def test_secret_ref_has_correct_name_and_key(self) -> None:
        wf = _make_workflow_dict()
        bridge = ArgoWorkflowBridge()
        _mock_generate(bridge, wf)
        result = bridge.generate_cron_workflow(
            MagicMock(__module__="flows.test"), cron_expression="0 * * * *"
        )
        parsed = yaml.safe_load(result)
        env = parsed["spec"]["templates"][0]["container"]["env"]
        secret_refs = [
            e for e in env if "valueFrom" in e and "secretKeyRef" in e.get("valueFrom", {})
        ]
        for ref in secret_refs:
            assert ref["valueFrom"]["secretKeyRef"]["name"] == "arrow-lake-secrets"
            assert "key" in ref["valueFrom"]["secretKeyRef"]


class TestArtifactRetention:
    """Test artifact TTL retention configuration."""

    def test_retention_converts_days_to_seconds(self) -> None:
        wf = _make_workflow_dict()
        bridge = ArgoWorkflowBridge()
        bridge._artifact_retention_days = 7
        _mock_generate(bridge, wf)
        result = bridge.generate_cron_workflow(
            MagicMock(__module__="flows.test"), cron_expression="0 * * * *"
        )
        parsed = yaml.safe_load(result)
        assert parsed["spec"]["ttlStrategy"]["secondsAfterCompletion"] == 604800

    def test_zero_retention_removes_ttl(self) -> None:
        wf = _make_workflow_dict()
        bridge = ArgoWorkflowBridge()
        bridge._artifact_retention_days = 0
        _mock_generate(bridge, wf)
        result = bridge.generate_cron_workflow(
            MagicMock(__module__="flows.test"), cron_expression="0 * * * *"
        )
        parsed = yaml.safe_load(result)
        assert "ttlStrategy" not in parsed["spec"]
