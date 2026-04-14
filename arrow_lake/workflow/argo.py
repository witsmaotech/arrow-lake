"""Argo Workflows bridge for Arrow Lake (Story 7.3, 7.4).

Wraps Metaflow's Argo plugin to provide Arrow Lake-specific workflow
generation, validation, and deployment.

Usage::

    from arrow_lake.workflow.argo import ArgoWorkflowBridge

    bridge = ArgoWorkflowBridge(config=argo_config)
    yaml_str = bridge.generate_workflow(QualityPipelineFlow)
    is_valid = bridge.validate_workflow(yaml_str)
    name = bridge.deploy_workflow(QualityPipelineFlow)
"""

from __future__ import annotations

import subprocess
from typing import Any

import structlog
import yaml

from arrow_lake.config import ArgoConfig
from arrow_lake.exceptions import ArgoError, ErrorCode

logger = structlog.get_logger(__name__)

__all__ = ["ArgoWorkflowBridge"]


def _validate_workflow_structure(workflow: dict[str, Any]) -> bool:
    """Validate Argo Workflow YAML structure.

    Args:
        workflow: Parsed YAML dict.

    Returns:
        True if valid.

    Raises:
        ArgoError: If validation fails.
    """
    required_keys = {"apiVersion", "kind", "metadata", "spec"}
    missing = required_keys - set(workflow.keys())
    if missing:
        raise ArgoError(
            error_code=ErrorCode.ARGO_VALIDATION_FAILED,
            message=f"Missing required keys: {sorted(missing)}",
        )
    if workflow["kind"] not in ("Workflow", "CronWorkflow"):
        raise ArgoError(
            error_code=ErrorCode.ARGO_VALIDATION_FAILED,
            message=f"Invalid kind: {workflow['kind']!r}, expected Workflow or CronWorkflow",
        )
    spec = workflow.get("spec", {})
    if not isinstance(spec, dict) or "templates" not in spec:
        raise ArgoError(
            error_code=ErrorCode.ARGO_VALIDATION_FAILED,
            message="Workflow spec must contain 'templates'",
        )
    return True


class ArgoWorkflowBridge:
    """Bridge between Arrow Lake flows and Argo Workflows.

    Wraps Metaflow's ``argo-workflows create`` CLI command to generate
    workflow YAML, with Arrow Lake configuration injection.

    Attributes:
        config: Argo deployment configuration.
    """

    def __init__(self, config: ArgoConfig | None = None) -> None:
        self._config = config or ArgoConfig()

    @property
    def config(self) -> ArgoConfig:
        return self._config

    def generate_workflow(
        self,
        flow_cls: type[Any],
        config: ArgoConfig | None = None,
    ) -> str:
        """Generate Argo Workflow YAML for a Metaflow flow.

        Executes ``python flow.py --with argo-workflows create`` in dry-run
        mode to capture the generated YAML.

        Args:
            flow_cls: Metaflow FlowSpec class.
            config: Override ArgoConfig for this generation.

        Returns:
            YAML string of the Argo Workflow.

        Raises:
            ArgoError: If workflow generation fails.
        """
        cfg = config or self._config
        module_name = flow_cls.__module__

        cmd = self._build_metaflow_command(module_name)
        cmd.extend(["create", "--dry-run"])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=cfg.workflow_timeout,
                check=False,
            )
            if result.returncode != 0:
                raise ArgoError(
                    error_code=ErrorCode.ARGO_GENERATION_FAILED,
                    message=f"Metaflow CLI failed: {result.stderr[:500]}",
                )
            yaml_str = result.stdout
            # Inject Arrow Lake env vars
            workflow_dict = yaml.safe_load(yaml_str)
            workflow_dict = self._inject_arrow_lake_env(workflow_dict)
            return yaml.dump(workflow_dict, default_flow_style=False)
        except FileNotFoundError:
            raise ArgoError(
                error_code=ErrorCode.ARGO_GENERATION_FAILED,
                message="Metaflow CLI not found — ensure metaflow is installed",
            ) from None
        except subprocess.TimeoutExpired:
            raise ArgoError(
                error_code=ErrorCode.ARGO_GENERATION_FAILED,
                message=f"Workflow generation timed out after {cfg.workflow_timeout}s",
            ) from None
        except yaml.YAMLError as exc:
            raise ArgoError(
                error_code=ErrorCode.ARGO_GENERATION_FAILED,
                message=f"Failed to parse generated YAML: {exc}",
            ) from None

    def validate_workflow(self, yaml_str: str) -> bool:
        """Validate Argo Workflow YAML structure.

        Args:
            yaml_str: YAML string to validate.

        Returns:
            True if valid.

        Raises:
            ArgoError: If validation fails.
        """
        try:
            workflow = yaml.safe_load(yaml_str)
        except yaml.YAMLError as exc:
            raise ArgoError(
                error_code=ErrorCode.ARGO_VALIDATION_FAILED,
                message=f"Invalid YAML: {exc}",
            ) from None
        if not isinstance(workflow, dict):
            raise ArgoError(
                error_code=ErrorCode.ARGO_VALIDATION_FAILED,
                message="Workflow YAML must be a mapping",
            )
        return _validate_workflow_structure(workflow)

    def deploy_workflow(
        self,
        flow_cls: type[Any],
        config: ArgoConfig | None = None,
    ) -> str:
        """Deploy a workflow to Argo Workflows.

        Executes ``python flow.py argo-workflows create`` with the
        configured namespace and service account.

        Args:
            flow_cls: Metaflow FlowSpec class.
            config: Override ArgoConfig.

        Returns:
            Workflow template name.

        Raises:
            ArgoError: If deployment fails.
        """
        cfg = config or self._config
        module_name = flow_cls.__module__
        cmd = self._build_metaflow_command(module_name)
        cmd.extend(
            [
                "create",
                f"--namespace={cfg.namespace}",
                f"--service-account={cfg.service_account}",
            ]
        )

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=cfg.workflow_timeout,
                check=False,
            )
            if result.returncode != 0:
                raise ArgoError(
                    error_code=ErrorCode.ARGO_DEPLOY_FAILED,
                    message=f"Deploy failed: {result.stderr[:500]}",
                )
            return flow_cls.__name__
        except FileNotFoundError:
            raise ArgoError(
                error_code=ErrorCode.ARGO_DEPLOY_FAILED,
                message="Metaflow CLI not found",
            ) from None
        except subprocess.TimeoutExpired:
            raise ArgoError(
                error_code=ErrorCode.ARGO_DEPLOY_FAILED,
                message=f"Deploy timed out after {cfg.workflow_timeout}s",
            ) from None

    def generate_cron_workflow(
        self,
        flow_cls: type[Any],
        cron_expression: str,
        config: ArgoConfig | None = None,
    ) -> str:
        """Generate Argo CronWorkflow YAML.

        Takes a base workflow and converts it to a CronWorkflow with
        schedule and TTL-based artifact retention.

        Args:
            flow_cls: Metaflow FlowSpec class.
            cron_expression: Cron schedule expression (e.g. "0 2 * * *").
            config: Override ArgoConfig.

        Returns:
            YAML string for CronWorkflow.

        Raises:
            ArgoError: If generation fails.
        """
        workflow_dict = yaml.safe_load(self.generate_workflow(flow_cls, config))

        workflow_dict["kind"] = "CronWorkflow"
        spec = workflow_dict.get("spec", {})

        # Set schedule
        spec["schedule"] = cron_expression

        # TTL-based artifact retention
        retention_days = getattr(self, "_artifact_retention_days", 30)
        if retention_days > 0:
            spec["ttlStrategy"] = {
                "secondsAfterCompletion": retention_days * 86400,
            }

        # Inject secrets
        self._inject_secrets(workflow_dict)

        return yaml.dump(workflow_dict, default_flow_style=False)

    def _inject_arrow_lake_env(self, workflow_dict: dict[str, Any]) -> dict[str, Any]:
        """Inject Arrow Lake environment variables into workflow containers.

        Adds ARROW_LAKE__* env vars to all workflow container specs.
        """
        arrow_lake_env = [
            {"name": "ARROW_LAKE__STORAGE__S3_ENDPOINT", "value": "http://minio:9000"},
            {"name": "ARROW_LAKE__STORAGE__S3_BUCKET", "value": "arrow-lake"},
            {"name": "ARROW_LAKE__OBSERVABILITY__METRICS_ENABLED", "value": "true"},
        ]

        for template in workflow_dict.get("spec", {}).get("templates", []):
            container = template.get("container", {})
            if container:
                existing = list(container.get("env", []))
                existing_names = {e.get("name") for e in existing}
                for env_var in arrow_lake_env:
                    if env_var["name"] not in existing_names:
                        existing.append(env_var)
                container["env"] = existing
        return workflow_dict

    def _inject_secrets(self, workflow_dict: dict[str, Any]) -> dict[str, Any]:
        """Inject K8s Secret references for sensitive values."""
        secret_env = [
            {
                "name": "ARROW_LAKE__STORAGE__S3_ACCESS_KEY",
                "valueFrom": {
                    "secretKeyRef": {
                        "name": "arrow-lake-secrets",
                        "key": "s3-access-key",
                    }
                },
            },
            {
                "name": "ARROW_LAKE__STORAGE__S3_SECRET_KEY",
                "valueFrom": {
                    "secretKeyRef": {
                        "name": "arrow-lake-secrets",
                        "key": "s3-secret-key",
                    }
                },
            },
        ]

        for template in workflow_dict.get("spec", {}).get("templates", []):
            container = template.get("container", {})
            if container:
                existing = list(container.get("env", []))
                existing_names = {e.get("name") for e in existing}
                for env_var in secret_env:
                    if env_var["name"] not in existing_names:
                        existing.append(env_var)
                container["env"] = existing
        return workflow_dict

    def _build_metaflow_command(self, flow_module: str) -> list[str]:
        """Build the Metaflow CLI command for Argo workflow generation."""
        return [
            "python",
            "-m",
            flow_module,
            "--with",
            "argo-workflows",
        ]
