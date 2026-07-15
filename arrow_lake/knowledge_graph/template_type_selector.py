"""[#1] TemplateTypeSelector — pick a hyper-extract template by Auto-Type.

hyper-extract offers 8 Auto-Types; this exposes the 6 user-selectable ones
(``graph`` / ``temporal_graph`` / ``hypergraph`` / ``list`` / ``set`` /
``model``), each mapped to a canonical low-risk preset template from the
gallery. Use it when the caller wants a specific structure regardless of
``doc_type`` (e.g. force ``temporal_graph`` for event-heavy text). When
``template_type`` is None the :class:`DocTypeRouter` still drives selection
(existing behavior preserved).

Selection priority inside :meth:`TemplateTypeSelector.select`:

1. Explicit ``template_type`` (caller / config / CLI / API) → that type's
   default template. ``hypergraph`` is opt-in and logs a HIGH-RISK warning.
2. Temporal heuristic — temporal signals in content/doc_type →
   ``temporal_graph`` default (auto, only when no type pinned).
3. ``None`` → defer to ``DocTypeRouter`` (doc_type → template).

Rationale: doc_type routes to a *domain* template (ddd/medical/legal…), but
cannot express a *structure* preference. ``template_type`` adds the structural
axis, orthogonal to doc_type. See ``project_template_type_selector`` (方向 B).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from arrow_lake.knowledge_graph.doc_type_router import TemplateGallery

logger = logging.getLogger(__name__)

# 6 user-selectable Auto-Types. (auto_spatial_graph / auto_spatio_temporal_graph
# omitted — no canonical low-risk preset, niche use.)
TEMPLATE_TYPES: tuple[str, ...] = (
    "graph", "temporal_graph", "hypergraph", "list", "set", "model",
)

# Auto-Type → canonical default preset (a low-risk representative from the
# gallery). Callers requesting only the structure get a sensible template
# without naming a domain.
TYPE_DEFAULTS: dict[str, str] = {
    "graph": "general/concept_graph",
    "temporal_graph": "general/workflow_graph",
    "hypergraph": "tcm/formula_composition",   # HIGH RISK — opt-in only
    "list": "legal/compliance_list",
    "set": "legal/defined_term_set",
    "model": "finance/sentiment_model",
}

# Auto-Types that are known-fragile on sparse/atypical content and so require
# explicit opt-in (hypergraph raises IndexError in
# hyperextract.types.hypergraph.merge_batch_data on empty partial node lists;
# see issue_hypergraph_template_indexerror).
HIGH_RISK_TYPES: frozenset[str] = frozenset({"hypergraph"})

# Signals in content/doc_type suggesting a temporal structure (→ temporal_graph).
#
# CONSERVATIVE on purpose: a false positive routes to a temporal_graph template
# whose ``parse_identifiers`` crashes (TypeError) when no ``time_field`` is set,
# killing the whole kg_build. Earlier the list included generic words like
# 事件 / 流程 / 阶段 / 顺序 / 工序 / 先后 — these appear in nearly every technical
# doc (e.g. DDD: domain *events*, business *processes*) and caused the build to
# crash on the JD DDD corpus. Only unambiguous timeline indicators remain; if a
# caller genuinely wants temporal structure they can pin ``template_type``.
_TEMPORAL_SIGNALS: tuple[str, ...] = (
    "时间线", "时间轴", "时间顺序", "时间序列", "历程", "生平", "履历", "传记",
    "里程碑", "timeline", "chronolog",
)


class TemplateTypeSelector:
    """Resolve a template path from a requested Auto-Type (+ temporal heuristic).

    Construct once (cheap) and call :meth:`select` per extraction. The optional
    ``gallery`` is reserved for future validation that a default still exists in
    the live gallery (currently unused — defaults are stable preset paths).
    """

    def __init__(
        self,
        gallery: "TemplateGallery | None" = None,
        defaults: dict[str, str] | None = None,
    ) -> None:
        self._gallery = gallery
        self._defaults = dict(defaults) if defaults else dict(TYPE_DEFAULTS)

    @staticmethod
    def is_valid(template_type: str | None) -> bool:
        """True iff ``template_type`` is one of the 6 selectable Auto-Types."""
        return template_type in TEMPLATE_TYPES

    @staticmethod
    def is_high_risk(template_type: str | None) -> bool:
        """True iff ``template_type`` is known-fragile (currently hypergraph)."""
        return template_type in HIGH_RISK_TYPES

    def default_for(self, template_type: str) -> str | None:
        """The canonical preset path for an Auto-Type (None if unknown)."""
        return self._defaults.get(template_type)

    def select(
        self,
        *,
        template_type: str | None = None,
        doc_type: str | None = None,
        content: str = "",
    ) -> str | None:
        """Return a template path for the requested Auto-Type, or None to defer.

        - explicit, valid ``template_type`` → its default template (hypergraph
          logs a HIGH-RISK opt-in warning);
        - unknown ``template_type`` → warn + return None (defer to DocTypeRouter);
        - else temporal heuristic on ``content``/``doc_type`` → temporal_graph;
        - else None (caller falls back to ``DocTypeRouter``).
        """
        if template_type:
            if template_type not in self._defaults:
                logger.warning(
                    "unknown template_type %r — ignoring (valid: %s)",
                    template_type, TEMPLATE_TYPES,
                )
                return None
            if template_type in HIGH_RISK_TYPES:
                logger.warning(
                    "template_type %r is HIGH RISK (hypergraph raises IndexError "
                    "on sparse content); proceeding opt-in only", template_type,
                )
            return self._defaults[template_type]

        # Temporal heuristic — only when the caller did not pin a type.
        haystack = f"{content}\n{doc_type or ''}".lower()
        if any(sig in haystack for sig in _TEMPORAL_SIGNALS):
            return self._defaults["temporal_graph"]
        return None
