"""hyper-extract backed entity extractor (v1.7.0 §4.1).

Adapter that implements the same ``async extract() -> ExtractionResult``
contract as :class:`EntityExtractor`, so :class:`KGBuilder` is unaware of the
backend swap (driven by ``HugeGraphConfig.extractor_backend``).

Pipeline: doc_type → template (DocTypeRouter) → ``Template.create().parse()``
→ AutoGraph ``nodes``/``edges`` → :class:`ExtractionResult`.

Implementation notes (§12.6):
- §12.6.①  ``ChatOpenAI(api_key=...)`` does not propagate to the underlying
  openai client in langchain-openai 1.3.x → set ``OPENAI_API_KEY`` env var.
- §12.6.②  Preset templates emit English ``type`` (Organization/Model/...) →
  normalize to HugeGraph vertex labels (person/organization/...).
- §11.3     Reuse legacy stopword filter + relation endpoint validity check.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from typing import Any

from arrow_lake.knowledge_graph.doc_type_router import (
    DocTypeClassifier,
    DocTypeRouter,
)
from arrow_lake.knowledge_graph.extractor import (
    _GENERIC_ENTITY_STOPWORDS,
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
)

logger = logging.getLogger(__name__)

# §12.6.② type 归一化：预置模板英文 type → HugeGraph vertex label
_TYPE_NORMALIZE: dict[str, str] = {
    "person": "person",
    "organization": "organization",
    "organisation": "organization",
    "company": "organization",
    "location": "location",
    "place": "location",
    "concept": "concept",
    "model": "concept",
    "technology": "concept",
    "product": "concept",
    "tool": "concept",
    "architecture": "concept",
    "framework": "concept",
    "algorithm": "concept",
    "method": "concept",
    "event": "event",
    "incident": "event",
}


def _normalize_type(raw: Any) -> str:
    """Map a hyper-extract node type to a HugeGraph vertex label."""
    key = str(raw or "concept").strip().lower()
    return _TYPE_NORMALIZE.get(key, "concept")


class HyperExtractExtractor:
    """Entity extractor backed by hyper-extract (AutoGraph).

    Implements the same ``extract()`` contract as :class:`EntityExtractor`.
    """

    def __init__(
        self,
        llm_config: Any,
        *,
        doc_type_router: DocTypeRouter,
        language: str = "zh",
        model: str | None = None,
        doc_type_classifier: DocTypeClassifier | None = None,
    ) -> None:
        self._llm_config = llm_config
        self._router = doc_type_router
        self._language = language
        self._model = model
        self._classifier = doc_type_classifier
        self._template_cache: dict[str, Any] = {}
        self._doc_type_cache: dict[str, str | None] = {}  # content digest → inferred doc_type
        # §12.6.① langchain-openai 1.3.x does not propagate ChatOpenAI(api_key=...)
        # to the underlying openai client, so OPENAI_API_KEY must be in the env.
        # Only set when absent (setdefault semantics) and log when an existing
        # DIFFERENT key wins, so a second extractor with another key is not silent.
        _existing = os.environ.get("OPENAI_API_KEY")
        _want = llm_config.api_key or "dummy"
        if _existing is None:
            os.environ["OPENAI_API_KEY"] = _want
        elif _existing != _want:
            logger.debug("OPENAI_API_KEY already set; not overridden by this extractor")

    def _get_template(self, template_path: str) -> Any:
        """Return a cached hyper-extract KA instance for ``template_path``."""
        if template_path not in self._template_cache:
            from hyperextract import Template
            from langchain_openai import ChatOpenAI

            chat = ChatOpenAI(
                model=self._model or self._llm_config.model,
                api_key=self._llm_config.api_key or "dummy",
                base_url=self._llm_config.api_base or None,
                temperature=0,
                max_tokens=2048,
            )
            self._template_cache[template_path] = Template.create(
                template_path,
                self._language,
                llm_client=chat,
                embedder=None,
            )
        return self._template_cache[template_path]

    async def _infer_doc_type(self, text: str) -> str | None:
        """Classify doc_type from ``text`` (cached by a stable content digest).

        Best-effort dedup keyed by SHA1 of the full text so repeated chunks /
        re-runs reuse the result (unlike ``hash()`` which is salted per process).
        NOTE: under concurrent ``extract_batch`` calls, two chunks with the same
        digest can both miss the cache and both call the LLM (benign — same
        result); this is NOT a hard dedup guarantee across in-flight coroutines.
        """
        key = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:16]
        if key not in self._doc_type_cache:
            self._doc_type_cache[key] = await self._classifier.classify(text)
        return self._doc_type_cache[key]

    async def extract(
        self, text: str, *, chunk_id: str = "", doc_type: str | None = None
    ) -> ExtractionResult:
        """Extract entities/relations from ``text`` via hyper-extract.

        Gracefully degrades to an empty result on any failure (matching
        legacy ``EntityExtractor`` behavior). ``chunk_id`` is accepted for
        contract symmetry and used only for logging.
        """
        stripped = text.strip()
        if not stripped:
            return ExtractionResult(entities=(), relations=(), raw_text=text)

        # P3: infer doc_type from content when the caller did not supply one.
        if doc_type is None and self._classifier is not None:
            doc_type = await self._infer_doc_type(stripped)

        template_path = self._router.resolve(doc_type)
        try:
            ka = self._get_template(template_path)
            # hyper-extract ``parse`` is sync with internal concurrency; wrap
            # to avoid blocking the event loop.
            result = await asyncio.to_thread(ka.parse, stripped)
        except Exception as exc:
            logger.warning(
                "hyper-extract parse failed for chunk %s (template=%s): %s",
                chunk_id,
                template_path,
                exc,
            )
            return ExtractionResult(entities=(), relations=(), raw_text=text)

        nodes = getattr(result, "nodes", None) or []
        edges = getattr(result, "edges", None) or []

        # nodes → entities (+ §11.3 stopword filter)
        entities: list[ExtractedEntity] = []
        name_set: set[str] = set()
        for n in nodes:
            name = getattr(n, "name", None)
            name = str(name).strip() if name else ""
            if not name or name in _GENERIC_ENTITY_STOPWORDS:
                continue
            entity_type = _normalize_type(getattr(n, "type", None))
            entities.append(ExtractedEntity(name=name, entity_type=entity_type))
            name_set.add(name)

        # edges → relations (+ §11.3 endpoint validity)
        relations: list[ExtractedRelation] = []
        for e in edges:
            source = str(getattr(e, "source", "") or "").strip()
            target = str(getattr(e, "target", "") or "").strip()
            if not source or not target:
                continue
            if source not in name_set or target not in name_set:
                continue
            rel_type = str(getattr(e, "type", "") or "related_to") or "related_to"
            relations.append(
                ExtractedRelation(source=source, target=target, relation_type=rel_type)
            )

        return ExtractionResult(
            entities=tuple(entities),
            relations=tuple(relations),
            raw_text=text,
        )

    async def extract_batch(
        self, chunks: list[tuple[str, str]], doc_type: str | None = None
    ) -> list[ExtractionResult]:
        """Extract from multiple chunks concurrently (contract parity).

        Concurrency is delegated to hyper-extract's internal ``max_workers``
        (inside ``parse``); here we only fan out the awaitable per chunk.
        """
        if not chunks:
            return []
        return await asyncio.gather(
            *(self.extract(text, chunk_id=cid, doc_type=doc_type) for cid, text in chunks)
        )
