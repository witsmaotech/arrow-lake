"""Document type → hyper-extract template routing (v1.7.0 §4.2, hardened v1.7.x).

Production-grade routing with three layers (first hit wins):

1. **Explicit override** — ``HugeGraphConfig.he_doc_type_templates`` (operator
   control; highest priority).
2. **Metadata-driven match** — :class:`TemplateGallery` indexes every hyper-extract
   preset by ``tags`` / ``category`` / ``name`` / ``description`` (zh+en); the
   normalized doc_type is matched against template metadata so new templates are
   picked up automatically without editing config.
3. **Default fallback** — ``HugeGraphConfig.he_default_template``.

``doc_type`` is normalized first (lowercase, strip, alias collapse) so ``"Paper"``,
``"research_paper"``, ``"论文"`` all route identically. Unknown doc_types fall
through to the default rather than failing — but the resolved template and match
source are exposed for observability.

Backward compatible: the legacy ``DocTypeRouter(templates, default)`` signature
still works; the gallery is built lazily and degrades to no-op if hyper-extract
is not installed.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import lru_cache

logger = logging.getLogger(__name__)


# --- doc_type normalization -------------------------------------------------
#
# Canonical doc_type → accepted aliases (case-insensitive). Canonical keys are
# aligned with the gallery categories (finance/legal/medicine/industry/tcm/
# general) plus the general document types (paper/report/manual/biography), so
# the three taxonomies (aliases / classifier labels / gallery categories) share
# one source of truth — see KNOWN_DOC_TYPES + validate_taxonomy().
DOC_TYPE_ALIASES: dict[str, tuple[str, ...]] = {
    "paper": ("article", "research-paper", "research_paper", "researchpaper", "论文", "学术论文", "academic", "technical", "tech", "技术文档", "技术"),
    "report": ("报告", "白皮书", "whitepaper", "white-paper"),
    "manual": ("guide", "手册", "教程", "tutorial", "howto"),
    "biography": ("人物", "传记", "bio"),
    "finance": ("财报", "earnings", "financial"),  # gallery category: finance
    "legal": ("合同", "协议", "agreement", "contract", "判例", "案例", "case", "legal_case"),
    "medicine": ("医疗", "病历", "clinical", "medical"),  # gallery category: medicine
    "industry": ("设备", "topology", "equipment", "故障", "failure", "事故", "incident"),
    "tcm": ("中医", "中药", "herb"),
    "general": (),  # fallback — no aliases
}

# Precomputed flat reverse-lookup: every alias (lowercased) → canonical key.
# Built once at import; ``normalize_doc_type`` is O(1) with no per-call set build.
_DOC_TYPE_LOOKUP: dict[str, str] = {}
for _canon, _aliases in DOC_TYPE_ALIASES.items():
    _DOC_TYPE_LOOKUP[_canon] = _canon
    for _a in _aliases:
        _DOC_TYPE_LOOKUP[_a.lower()] = _canon


@lru_cache(maxsize=1024)
def normalize_doc_type(raw: str | None) -> str | None:
    """Normalize a raw doc_type to its canonical key, or ``None`` if empty.

    Lowercases, strips, and collapses aliases (e.g. ``"research_paper"`` →
    ``"paper"``, ``"论文"`` → ``"paper"``). Returns ``None`` for empty/blank
    input so callers can fall through to the default.
    """
    if raw is None:
        return None
    key = raw.strip().lower()
    if not key:
        return None
    return _DOC_TYPE_LOOKUP.get(key, key)  # unknown but non-empty — preserved


# --- template gallery -------------------------------------------------------


@dataclass(frozen=True)
class TemplateInfo:
    """One hyper-extract preset template with its metadata."""

    path: str  # e.g. "general/concept_graph"
    category: str  # e.g. "general"
    name: str  # e.g. "concept_graph"
    type: str  # e.g. "graph"
    tags: tuple[str, ...]
    description: str  # flattened zh+en text for keyword matching


# Splits on any run of non-alphanumeric OR underscore (template names are
# underscore-joined, e.g. "workflow_graph" → {"workflow", "graph"}).
_TOKEN_SPLIT = re.compile(r"[\W_]+")


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_SPLIT.split(text) if t}


def _contains_word(key: str, haystack: str) -> bool:
    """True if ``key`` appears as a whole word in ``haystack``.

    ASCII keys use a word-boundary regex (avoids ``"general"`` matching
    ``"generally"``); CJK keys fall back to substring (CJK has no word
    boundaries, and aliases route most CJK inputs via the override layer).
    """
    if not key:
        return False
    if key.isascii():
        return re.search(rf"(?<![A-Za-z0-9]){re.escape(key)}(?![A-Za-z0-9])", haystack) is not None
    return key in haystack


# Preferred template `type` ranking when multiple templates share a category
# (lower wins) — graph is the most general-purpose, so prefer it within a group.
_CATEGORY_TYPE_RANK: dict[str, int] = {"graph": 0, "model": 1, "hypergraph": 2}


@dataclass
class TemplateGallery:
    """Index of hyper-extract preset templates, queryable by metadata."""

    templates: list[TemplateInfo] = field(default_factory=list)

    @classmethod
    def build(cls) -> TemplateGallery:
        """Scan hyper-extract presets and index every template's metadata.

        Returns an empty gallery (not an error) if hyper-extract is missing or
        its preset directory is absent — callers degrade to the default template.
        """
        infos: list[TemplateInfo] = []
        try:
            import hyperextract  # type: ignore[import-untyped]
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            logger.debug("hyperextract/yaml not installed — TemplateGallery is empty")
            return cls()

        presets = os.path.join(
            os.path.dirname(hyperextract.__file__), "templates", "presets"
        )
        if not os.path.isdir(presets):
            return cls()

        for category in sorted(os.listdir(presets)):
            cdir = os.path.join(presets, category)
            if not os.path.isdir(cdir):
                continue
            for fname in sorted(os.listdir(cdir)):
                if not fname.endswith(".yaml"):
                    continue
                name = fname[:-5]
                # Skip base_* presets — they are AutoType base classes, not
                # directly extractable (Template.create raises "Template not
                # found"). Including them would let e.g. doc_type="general"
                # (base_graph's tag) misroute to an unusable template.
                if name.startswith("base_"):
                    continue
                try:
                    with open(os.path.join(cdir, fname), encoding="utf-8") as fh:
                        data = yaml.safe_load(fh)
                except (OSError, ValueError, yaml.YAMLError):
                    continue  # unreadable / malformed preset — skip
                if not isinstance(data, dict):
                    continue
                tags = tuple(str(t).lower() for t in data.get("tags", []) if t)
                desc = _flatten_description(data.get("description", "")).lower()
                infos.append(
                    TemplateInfo(
                        path=f"{category}/{name}",
                        category=category.lower(),
                        name=name.lower(),
                        type=str(data.get("type", "")).lower(),
                        tags=tags,
                        description=desc,
                    )
                )
        return cls(templates=infos)

    def match(self, doc_type: str) -> TemplateInfo | None:
        """Best-effort metadata match for a (already-normalized) doc_type.

        Priority: exact tag → category → name token → description word. Levels
        3-4 use whole-word matching (not raw substring) to avoid false positives
        like ``"general"`` matching ``"generally"``. Returns ``None`` if nothing
        matches (caller falls back to default).
        """
        if not self.templates or not doc_type:
            return None
        key = doc_type.lower()

        # 1. exact tag hit (e.g. doc_type="concept" matches concept_graph's tag)
        for t in self.templates:
            if key in t.tags:
                return t

        # 2. category hit (e.g. doc_type="finance" → a finance template)
        cat_hits = [t for t in self.templates if t.category == key]
        if cat_hits:
            return min(cat_hits, key=lambda t: _CATEGORY_TYPE_RANK.get(t.type, 99))

        # 3. name token (e.g. doc_type="workflow" → workflow_graph)
        for t in self.templates:
            if key in _tokens(t.name):
                return t

        # 4. description keyword (substring; e.g. doc_type="paper" matches
        #    concept_graph's "academic papers" / "学术论文"). Substring is safe
        #    here because base_* presets are excluded and common-word doc_types
        #    like "general"/"concept" already match at the tag level above.
        for t in self.templates:
            if key in t.description:
                return t

        return None


def _flatten_description(desc: object) -> str:
    """Flatten a template description (str / dict / list) to one string."""
    if isinstance(desc, str):
        return desc
    if isinstance(desc, dict):
        return " ".join(str(v) for v in desc.values())
    if isinstance(desc, list):
        return " ".join(str(v) for v in desc)
    return str(desc or "")


@lru_cache(maxsize=1)
def _shared_gallery() -> TemplateGallery:
    """Module-level shared gallery (built once, cached)."""
    return TemplateGallery.build()


def reset_gallery_cache() -> None:
    """Clear the shared gallery cache (dev/test helper for preset hot-reload)."""
    _shared_gallery.cache_clear()


# --- router -----------------------------------------------------------------


class DocTypeRouter:
    """Resolve ``doc_type`` to a hyper-extract template path.

    Args:
        doc_type_templates: Explicit overrides ``doc_type -> template_path``
            (from ``HugeGraphConfig.he_doc_type_templates``). Highest priority.
        default_template: Fallback template path.
        gallery: Optional :class:`TemplateGallery` for metadata-driven matching.
            If ``None``, the shared module-level gallery is used (lazily built).
    """

    def __init__(
        self,
        doc_type_templates: dict[str, str],
        default_template: str,
        *,
        gallery: TemplateGallery | None = None,
    ) -> None:
        # Normalize override keys to canonical form so an operator can key by
        # either canonical or alias (e.g. {"research_paper": X} and {"paper": X}
        # are equivalent — aliases collapse before lookup).
        self._overrides: dict[str, str] = {}
        for k, v in doc_type_templates.items():
            nt = normalize_doc_type(k) or (k or "").strip().lower()
            self._overrides[nt] = v
        self._default = default_template
        self._gallery = gallery if gallery is not None else _shared_gallery()

    def resolve(self, doc_type: str | None) -> str:
        """Return the template path for ``doc_type`` (normalized first)."""
        path, _source = self.resolve_with_source(doc_type)
        return path

    def resolve_with_source(self, doc_type: str | None) -> tuple[str, str]:
        """Like :meth:`resolve` but also returns the match source for observability.

        Source is one of: ``"override"``, ``"gallery"``, ``"default"``.
        """
        nt = normalize_doc_type(doc_type)
        if nt:
            if nt in self._overrides:
                return self._overrides[nt], "override"
            hit = self._gallery.match(nt)
            if hit is not None:
                return hit.path, "gallery"
        return self._default, "default"


# --- P3: content-based doc_type inference -----------------------------------
#
# When the caller does not supply a doc_type (or supplies an unknown one), infer
# it from the document content via a single LLM call. The canonical doc_types
# below are the classification label set; the router then resolves the inferred
# doc_type through the normal 3-layer path (override → gallery → default).

# Canonical doc_type → short description used as the LLM classification label set.
# Keys are aligned with DOC_TYPE_ALIASES canonicals (and thus gallery categories).
DOC_TYPE_DESCRIPTIONS: dict[str, str] = {
    "paper": "academic / technical paper, research article (concepts, taxonomy)",
    "report": "structured report, whitepaper, analysis document",
    "manual": "how-to guide, tutorial, workflow, SOP",
    "biography": "biography, life events, timeline of a person/entity",
    "finance": "earnings, finance, corporate events, ownership, sentiment",
    "legal": "contract, legal case, compliance, regulation, defined terms",
    "medicine": "clinical, treatment, drug interaction, anatomy, hospital",
    "tcm": "traditional chinese medicine: herbs, formulas, meridians, syndromes",
    "industry": "industrial: equipment topology, operation flow, safety, failure",
    "general": "general-purpose (fallback)",
}

# Single source of truth for "known" canonical doc_types — the union of the
# alias taxonomy and the classifier label set. Used by :func:`validate_taxonomy`
# to catch drift between the three taxonomies (aliases / descriptions / gallery).
KNOWN_DOC_TYPES: frozenset[str] = frozenset(DOC_TYPE_ALIASES) | frozenset(DOC_TYPE_DESCRIPTIONS)


def validate_taxonomy(gallery: TemplateGallery | None = None) -> list[str]:
    """Return a list of taxonomy-drift warnings (empty if consistent).

    Checks that every classifier label (:data:`DOC_TYPE_DESCRIPTIONS`) is a
    known alias canonical key, and that every gallery category is a known
    doc_type. Intended to be called from a CI test so drift fails the build.
    """
    warnings: list[str] = []
    gallery = gallery if gallery is not None else _shared_gallery()
    for label in DOC_TYPE_DESCRIPTIONS:
        if label not in DOC_TYPE_ALIASES:
            warnings.append(
                f"classifier label {label!r} has no alias entry in DOC_TYPE_ALIASES"
            )
    cats = {t.category for t in gallery.templates}
    for cat in sorted(cats):
        if cat not in KNOWN_DOC_TYPES:
            warnings.append(
                f"gallery category {cat!r} not in KNOWN_DOC_TYPES (will only match via explicit override)"
            )
    return warnings


class DocTypeClassifier:
    """Infer a canonical doc_type from document content via a single LLM call.

    Uses :data:`DOC_TYPE_DESCRIPTIONS` as the label set. The LLM call is injected
    via ``llm_complete`` (an ``async (system, user) -> str`` callable) so the
    classifier is unit-testable without a real LLM. Use
    :meth:`from_llm_config` to build one from an Arrow Lake LLM config.

    The result is a canonical doc_type (already normalized) suitable for
    :meth:`DocTypeRouter.resolve`, or ``None`` if inference fails / text is empty.
    """

    LABELS = tuple(DOC_TYPE_DESCRIPTIONS)

    def __init__(
        self,
        llm_complete: Callable[[str, str], Awaitable[str]],
        *,
        max_text_chars: int = 1500,
    ) -> None:
        self._llm_complete = llm_complete
        self._max_text_chars = max_text_chars

    @classmethod
    def from_llm_config(cls, llm_config: object, **provider_kwargs: object) -> DocTypeClassifier:
        """Build a classifier backed by the Arrow Lake LLM provider.

        ``llm_config`` is an LLMConfig-like with ``model`` / ``api_key`` /
        ``api_base``. The provider is imported lazily so this module stays
        importable without the rag stack.
        """
        from arrow_lake.rag.provider import create_llm_provider  # local import

        provider = create_llm_provider(llm_config, **provider_kwargs)

        async def _complete(system: str, user: str) -> str:
            from arrow_lake.rag.provider import LLMMessage  # local import

            # 修(2026-07-07)：provider 只有 .generate()（BaseLLMProvider 无 .complete()）；
            # LLMMessage 是 @dataclass(role, content)，不是 system=/user= kwargs。
            resp = await provider.generate([
                LLMMessage(role="system", content=system),
                LLMMessage(role="user", content=user),
            ])
            return resp.content

        return cls(_complete)

    async def classify(self, text: str) -> str | None:
        """Infer a canonical doc_type from ``text``.

        Returns one of :attr:`LABELS`, or ``None`` on empty text / unparseable
        / hallucinated label. Never raises — inference is best-effort and
        callers fall back to the default template on ``None``.
        """
        snippet = (text or "").strip()
        if not snippet:
            return None
        snippet = snippet[: self._max_text_chars]

        options = "\n".join(f"- {k}: {v}" for k, v in DOC_TYPE_DESCRIPTIONS.items())
        system = (
            "You classify documents by type. Reply with ONLY the label word "
            "(one of the options), no punctuation, no explanation."
        )
        user = (
            f"Pick the ONE best-fitting label for this document.\n\n"
            f"Options:\n{options}\n\n"
            f"Document:\n{snippet}"
        )
        try:
            raw = (await self._llm_complete(system, user)).strip().lower()
        except (TimeoutError, RuntimeError, OSError, ValueError, AttributeError, TypeError) as exc:
            # Best-effort catch: network/LLM/parse errors → None. AttributeError/
            # TypeError 防止 provider 接口不匹配等静默 bug 炸调用方。Never masks
            # KeyboardInterrupt / asyncio.CancelledError (BaseException).
            logger.warning("doc_type classification LLM call failed: %s", exc)
            return None

        if not raw:
            return None
        # Robust to thinking-model reasoning / extra explanation: scan the FULL
        # response for a known label as a whole word. Specific labels first,
        # "general" last (common English word — avoid false match on reasoning).
        ordered = [k for k in DOC_TYPE_DESCRIPTIONS if k != "general"] + ["general"]
        for label in ordered:
            if _contains_word(label, raw):
                return label
        # Token-level alias normalization (handles Chinese labels like 论文/财报).
        for tok in re.split(r"[\W_]+", raw):
            nt = normalize_doc_type(tok)
            if nt in DOC_TYPE_DESCRIPTIONS:
                return nt
        logger.debug("doc_type classifier returned unknown label (raw=%r)", raw)
        return None

    def classify_sync(self, text: str) -> str | None:
        """Sync adapter for :meth:`classify` — for the sync ingest path.

        Handles both no-running-loop (plain ``asyncio.run``) and running-loop
        (FastAPI async context) cases: the latter runs the coro in a worker
        thread with its own loop to avoid "loop already running" errors.
        Best-effort: never raises (degrades to None on any failure).
        """
        import asyncio
        import concurrent.futures

        async def _safe() -> str | None:
            try:
                return await self.classify(text)
            except Exception as exc:  # noqa: BLE001 — best-effort, mirror classify()
                logger.warning("doc_type classification failed: %s", exc)
                return None

        try:
            asyncio.get_running_loop()
            running = True
        except RuntimeError:
            running = False

        if not running:
            return asyncio.run(_safe())
        # Running loop present (e.g. inside an async API handler) — run in a
        # worker thread with a fresh loop.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(asyncio.run, _safe()).result()
