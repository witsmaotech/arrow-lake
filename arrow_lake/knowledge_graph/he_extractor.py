"""hyper-extract backed entity extractor (v1.7.0 §4.1, v1.8.8 per-dataset KA).

Implements the same ``async extract() -> ExtractionResult`` contract as
:class:`EntityExtractor`, so :class:`KGBuilder` is unaware of the backend swap
(driven by ``HugeGraphConfig.extractor_backend``).

Two granularities (``HugeGraphConfig.he_kg_granularity``):

- ``"chunk"``   — per-chunk fresh ``Template.create().parse()`` (legacy path,
                  kept for fallback/parity). ``extract`` / ``extract_batch``.
- ``"dataset"`` — per-dataset ONE KA, chunks fed via ``feed_text`` so the
                  AutoGraph LLM.BALANCED merger fuses cross-chunk entities,
                  then ``build_index`` + ``dump`` to ``<base>/<ds>/ka/``.
                  ``build_dataset_ka`` → :class:`DatasetKA`.

Pipeline (per-chunk): doc_type → template (DocTypeRouter) → ``parse()``
→ AutoGraph ``nodes``/``edges`` → :class:`ExtractionResult`.
Pipeline (per-dataset): template → ``feed_text`` loop → ``build_index`` →
``dump`` → merged :class:`ExtractionResult` + name→[chunk_id] provenance.

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
from dataclasses import dataclass
from pathlib import Path
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


@dataclass(frozen=True)
class DatasetKA:
    """Result of a per-dataset KA build (feed_text + build_index + dump).

    Attributes:
        ka: the hyper-extract AutoGraph instance with merged nodes/edges and a
            built FAISS index, state dumped to ``ka_dir``.
        ka_dir: directory holding ``data.json`` / ``metadata.json`` / ``index/``.
        entity_chunks: ``name → [chunk_id]`` provenance rebuilt by name-diff
            across ``feed_text`` calls — a chunk "owns" an entity if the name is
            present in ``ka.nodes`` after feeding that chunk. Used by
            :class:`KGBuilder` to build ``references(chunk→entity)`` edges.
        result: the merged :class:`ExtractionResult` for KG insertion.
    """

    ka: Any
    ka_dir: Path
    entity_chunks: dict[str, list[str]]
    result: ExtractionResult


class HyperExtractExtractor:
    """Entity extractor backed by hyper-extract (AutoGraph).

    Implements the same ``extract()`` contract as :class:`EntityExtractor`, and
    additionally exposes ``build_dataset_ka`` for per-dataset granularity.
    """

    def __init__(
        self,
        llm_config: Any,
        *,
        doc_type_router: DocTypeRouter,
        language: str = "zh",
        model: str | None = None,
        doc_type_classifier: DocTypeClassifier | None = None,
        embedder: Any = None,
        kg_granularity: str = "dataset",
    ) -> None:
        self._llm_config = llm_config
        self._router = doc_type_router
        self._language = language
        self._model = model
        self._classifier = doc_type_classifier
        # v1.8.8: langchain Embeddings (_LakeEmbedderAdapter) for build_index.
        # None = parse-only (old behaviour); required for dataset granularity.
        self._embedder = embedder
        self._kg_granularity = kg_granularity
        self._template_cache: dict[str, Any] = {}
        self._doc_type_cache: dict[str, str | None] = {}  # content digest → inferred doc_type
        # langchain ChatOpenAI client (thread-safe, reused across parses). A fresh
        # hyper-extract KA is created per parse to avoid AutoGraph accumulating
        # mutable state (data/indexes) across many chunks → corrupted/empty output.
        self._chat_client: Any = None
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

    def _get_chat_client(self) -> Any:
        """Return a cached langchain ChatOpenAI client (thread-safe; reused
        across parses). A fresh hyper-extract KA is created per parse via
        :meth:`_create_ka` to avoid the AutoGraph accumulating mutable state
        (``data``/indexes) across many chunks, which corrupts output after N calls.
        """
        if self._chat_client is None:
            api_base = self._llm_config.api_base or ""
            model = self._model or self._llm_config.model or ""
            # 阿里百炼(aliyuncs MaaS):走 hyper-extract 官方 create_client,而非直连
            # langchain ChatOpenAI。原因:① 直连 ChatOpenAI 在 hyperextract 包 import 后
            # api_key 传递异常(openai.OpenAIError: Missing credentials,import 副作用);
            # ② create_client 正确处理 bailian provider 配置 + json_schema 模型选型
            # (qwen-turbo/plus 等支持 json_schema,避开 qwen-max / qwen3.7-max 系
            # json_object-only 的 "messages must contain json" 400,见 hyper-extract
            # providers 文档 Issue #24);③ qwen-turbo 非思考 → feed_text 快(实测 7.7s/
            # chunk vs thinking 模型 8min 无 checkpoint)。embedder 仍用项目的
            # _LakeEmbedderAdapter(忽略 create_client 自带的 emb)。
            if "aliyuncs" in api_base:
                from hyperextract import create_client

                llm, _emb = create_client(
                    f"bailian:{model}@{api_base}",
                    api_key=self._llm_config.api_key or "dummy",
                )
                self._chat_client = llm
            else:
                from langchain_openai import ChatOpenAI

                self._chat_client = ChatOpenAI(
                    model=model,
                    api_key=self._llm_config.api_key or "dummy",
                    base_url=self._llm_config.api_base or None,
                    temperature=0,
                    # §12.6 修正：hyper-extract 走结构化输出(.parse() + response_format)，
                    # 不能用 chat_template_kwargs 关 thinking（.parse() 拒绝未知 kwarg）。
                    # thinking 模型需把 max_tokens 撑大让 thinking+结构化输出都装下，否则返空。
                    max_tokens=8192,
                )
        return self._chat_client

    def _create_ka(self, template_path: str) -> Any:
        """Create a fresh hyper-extract KA with this extractor's llm + embedder.

        AutoGraph's default node/edge merger is ``MergeStrategy.LLM.BALANCED``
        (``hyperextract/types/graph.py:164-167``), so ``feed_text`` activates
        cross-chunk LLM field-merging out of the box — no explicit merger kwarg
        needed. ``embedder`` (a langchain ``Embeddings`` via
        ``_LakeEmbedderAdapter``) enables ``build_index``; ``None`` keeps the
        old parse-only behaviour (per-chunk granularity without index).
        """
        from hyperextract import Template

        return Template.create(
            template_path,
            self._language,
            llm_client=self._get_chat_client(),
            embedder=self._embedder,
        )

    def _parse_fresh(self, template_path: str, text: str) -> Any:
        """Create a ONE-SHOT hyper-extract KA and parse ``text``.

        Each call builds a fresh KA (reusing the cached langchain client) so no
        mutable AutoGraph state persists between chunks (per-chunk granularity).
        """
        return self._create_ka(template_path).parse(text)

    @staticmethod
    def _ka_to_extraction_result(ka: Any, raw_text: str = "") -> ExtractionResult:
        """Convert a hyper-extract KA (``nodes``/``edges``) to an ExtractionResult.

        Applies the same §11.3 stopword filter + endpoint validity as legacy
        ``EntityExtractor``, so the per-chunk (``parse``) and per-dataset
        (``feed_text``) paths share one conversion.
        """
        nodes = getattr(ka, "nodes", None) or []
        edges = getattr(ka, "edges", None) or []

        # nodes → entities (+ §11.3 stopword filter)
        # entity_type keeps the LLM-extracted RAW type (Chinese 概念/属性/架构
        # 组件/...). Do NOT call _normalize_type here — it collapsed every type
        # to "concept" (dict has only English keys) and erased the 81-way type
        # info. route_entity_type does typed-vertex routing on this raw value.
        entities: list[ExtractedEntity] = []
        name_set: set[str] = set()
        for n in nodes:
            name = getattr(n, "name", None)
            name = str(name).strip() if name else ""
            if not name or name in _GENERIC_ENTITY_STOPWORDS:
                continue
            entity_type = str(getattr(n, "type", "") or "").strip() or "未知"
            definition = str(getattr(n, "definition", "") or "").strip()
            props = (("definition", definition),) if definition else ()
            entities.append(ExtractedEntity(
                name=name, entity_type=entity_type, properties=props))
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
            description = str(getattr(e, "description", "") or "").strip()
            props = (("description", description),) if description else ()
            relations.append(
                ExtractedRelation(source=source, target=target,
                                  relation_type=rel_type, properties=props)
            )

        return ExtractionResult(
            entities=tuple(entities),
            relations=tuple(relations),
            raw_text=raw_text,
        )

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
        """Extract entities/relations from ``text`` via hyper-extract (per-chunk).

        Gracefully degrades to an empty result on any failure (matching
        legacy ``EntityExtractor`` behavior). ``chunk_id`` is accepted for
        contract symmetry and used only for logging.
        """
        stripped = text.strip()
        if not stripped:
            return ExtractionResult(entities=(), relations=(), raw_text=text)

        # P3: infer doc_type from content when the caller did not supply one.
        # Skip inference for very short chunks (e.g. TOC page-number lines like
        # ".... | 1") — the LLM misclassifies these to unrelated domain templates
        # (observed: tcm/formula_composition), whose hypergraph parse then raises
        # IndexError. Real content chunks are >= 80 chars; shorter ones fall
        # through to the default template (and the fallback below still catches
        # any parse failure).
        if doc_type is None and self._classifier is not None and len(stripped) >= 80:
            doc_type = await self._infer_doc_type(stripped)

        template_path = self._router.resolve(doc_type)
        try:
            # Fresh KA per parse (no shared mutable state across chunks).
            result = await asyncio.to_thread(self._parse_fresh, template_path, stripped)
        except Exception as exc:
            # Fallback: a doc_type-routed template can fail on sparse/atypical
            # content (e.g. hypergraph templates raise IndexError in
            # hyperextract.types.hypergraph.merge_batch_data when a partial
            # nodes list is empty). Retry with the default template before
            # yielding empty — otherwise one misrouted chunk zeroes the KG.
            default_path = self._router.default_template()
            if template_path != default_path:
                logger.warning(
                    "hyper-extract parse failed for chunk %s (template=%s): %s "
                    "— retrying with default %s",
                    chunk_id, template_path, exc, default_path,
                )
                try:
                    result = await asyncio.to_thread(
                        self._parse_fresh, default_path, stripped
                    )
                except Exception as exc2:
                    logger.warning(
                        "hyper-extract default parse also failed for chunk %s: %s",
                        chunk_id, exc2,
                    )
                    return ExtractionResult(entities=(), relations=(), raw_text=text)
            else:
                logger.warning(
                    "hyper-extract parse failed for chunk %s (template=%s): %s",
                    chunk_id, template_path, exc,
                )
                return ExtractionResult(entities=(), relations=(), raw_text=text)

        return self._ka_to_extraction_result(result, text)

    async def build_dataset_ka(
        self,
        template_path: str,
        chunks: list[tuple[str, str]],
        ka_dir: Path,
        *,
        checkpoint_every: int = 20,
    ) -> DatasetKA:
        """Build ONE per-dataset KA: ``feed_text`` all chunks → ``build_index`` → ``dump``.

        Activates hyper-extract's cross-chunk LLM.BALANCED merge that the
        per-chunk fresh-KA path bypasses, then dumps the merged KA to
        ``ka_dir`` (``data.json`` + ``metadata.json`` + FAISS ``index/``) so it
        can be reloaded later via ``he search/talk`` / MCP.

        Provenance (``entity_chunks``) is rebuilt by name-diff around each
        ``feed_text`` call: a chunk owns every entity name present in
        ``ka.nodes`` after feeding it. This matches ``node_key_extractor``'s
        exact-name bucketing (no alias fusion — same semantics as per-chunk,
        no regression).

        Error handling: a single chunk feed failure is SKIPPED (counted), never
        retried with another template — the per-dataset KA must keep ONE
        template or its schema tears. The lone exception is a FIRST-chunk
        failure with an empty KA, which means the template itself is unusable:
        rebuild the KA with the default template and continue.

        Args:
            template_path: hyper-extract template path (caller-resolved doc_type).
            chunks: list of ``(chunk_id, text)`` to feed, in order.
            ka_dir: directory to dump into (created if needed).
            checkpoint_every: dump every N chunks (crash protection, §9
                large-corpus mode). 0 disables.

        Returns:
            :class:`DatasetKA` with the merged KA, dump dir, name→[chunk_id]
            provenance, and the merged ExtractionResult.
        """
        ka = self._create_ka(template_path)
        ka_dir = Path(ka_dir)
        ka_dir.mkdir(parents=True, exist_ok=True)
        entity_chunks: dict[str, list[str]] = {}
        failures = 0

        for i, (cid, text) in enumerate(chunks):
            stripped = (text or "").strip()
            if not stripped:
                continue
            before = {getattr(n, "name", "") for n in (getattr(ka, "nodes", None) or [])}
            try:
                await asyncio.to_thread(ka.feed_text, stripped)
            except Exception as exc:
                failures += 1
                logger.warning(
                    "build_dataset_ka feed_text failed chunk %s (%d/%d), skipped: %s",
                    cid, i + 1, len(chunks), str(exc)[:160],
                )
                # First-chunk failure on an empty KA ⇒ template unusable:
                # rebuild with default and keep going (one-time, not per-chunk).
                if i == 0 and not (getattr(ka, "nodes", None) or []):
                    default_path = self._router.default_template()
                    if template_path != default_path:
                        logger.warning(
                            "first-chunk failure on template %s; rebuilding KA with default %s",
                            template_path, default_path,
                        )
                        template_path = default_path
                        ka = self._create_ka(default_path)
                continue
            after = {getattr(n, "name", "") for n in (getattr(ka, "nodes", None) or [])}
            # Record only NEW names this chunk introduced (first-appearance
            # provenance). LLM.BALANCED never removes a name, so each name is
            # attributed to exactly the chunk where it first appeared — keeps
            # references(chunk→entity) edges precise, not inflated.
            for name in (after - before):
                if name:
                    entity_chunks.setdefault(name, []).append(cid)
            # checkpoint (§9 large-corpus crash protection)
            if checkpoint_every and (i + 1) % checkpoint_every == 0:
                try:
                    await asyncio.to_thread(ka.dump, ka_dir)
                    logger.info(
                        "build_dataset_ka checkpoint at chunk %d/%d → %s",
                        i + 1, len(chunks), ka_dir,
                    )
                except Exception as exc:
                    logger.warning(
                        "build_dataset_ka checkpoint dump failed: %s", str(exc)[:160]
                    )

        if failures:
            logger.info(
                "build_dataset_ka done: %d/%d chunks skipped (template=%s)",
                failures, len(chunks), template_path,
            )

        # feed_text invalidates the index (omem clear_index); rebuild once, then dump.
        if not ka.empty():
            await asyncio.to_thread(ka.build_index)
        await asyncio.to_thread(ka.dump, ka_dir)

        result = self._ka_to_extraction_result(ka)
        return DatasetKA(
            ka=ka, ka_dir=ka_dir, entity_chunks=entity_chunks, result=result
        )

    async def extract_batch(
        self, chunks: list[tuple[str, str]], doc_type: str | None = None
    ) -> list[ExtractionResult]:
        """Extract from multiple chunks concurrently (per-chunk granularity).

        Concurrency is delegated to hyper-extract's internal ``max_workers``
        (inside ``parse``); here we only fan out the awaitable per chunk.
        """
        if not chunks:
            return []
        return await asyncio.gather(
            *(self.extract(text, chunk_id=cid, doc_type=doc_type) for cid, text in chunks)
        )
