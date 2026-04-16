"""Arrow Lake flow definitions."""

import logging

from arrow_lake.workflow.base import FlowRegistry

logger = logging.getLogger(__name__)

_registration_attempted = False


def _register_flows() -> None:
    """Lazily register flows to avoid import-time Metaflow dependency errors.

    Some flows (e.g. ScheduledQualityFlow) import Metaflow plugins at
    module level. This function catches ImportError and only registers
    flows that can be imported successfully.
    """
    global _registration_attempted
    if _registration_attempted:
        return

    _registration_attempted = True

    _flow_map: dict[str, str] = {
        "quality_pipeline": "flows.quality_pipeline_flow.QualityPipelineFlow",
        "maya_e2e": "flows.maya_e2e_flow.MayaE2EFlow",
        "scheduled_quality": "flows.scheduled_quality_flow.ScheduledQualityFlow",
    }
    for name, import_path in _flow_map.items():
        try:
            parts = import_path.rsplit(".", 1)
            module_path, cls_name = parts[0], parts[1]
            module = __import__(module_path, fromlist=[cls_name])
            flow_cls = getattr(module, cls_name)
            FlowRegistry.register(name, flow_cls)
        except (ImportError, AttributeError):
            logger.warning("Flow '%s' could not be registered (%s)", name, import_path)


__all__ = ["MayaE2EFlow", "QualityPipelineFlow", "ScheduledQualityFlow"]
