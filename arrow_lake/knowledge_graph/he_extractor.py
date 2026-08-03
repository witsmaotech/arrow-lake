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
- ``"map_reduce"`` — v1.9.8: builder-side path; reuses this class's per-chunk
                  ``extract`` concurrently (no extractor-side merge state), then
                  merges the per-chunk results globally in ``KGBuilder``.

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
    _PROJECT_TEMPLATES_DIR,
)
from arrow_lake.knowledge_graph.extractor import (
    _GENERIC_ENTITY_STOPWORDS,
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
)

logger = logging.getLogger(__name__)


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
        hugegraph_config: Any = None,
        template_type: str | None = None,
    ) -> None:
        self._llm_config = llm_config
        # [#10/#2] HugeGraphConfig for he_chunk_size / he_ka_base_dir. Passed
        # explicitly by _create_kg_extractor (previously relied on external
        # monkey-patching of _config, which broke the search/chat path that
        # instantiates the extractor without going through KGBuilder).
        self._hugegraph_config = hugegraph_config
        self._router = doc_type_router
        self._language = language
        self._model = model
        # [#KG-LLM-split] 两阶段独立 LLM（None → 回退全局 llm_config，向后兼容）。
        # 抽取 (feed_text) 用 _extract_llm_cfg；问答 (ka.chat) 用 _qa_llm_cfg。
        self._extract_llm_cfg = (
            getattr(hugegraph_config, "he_extract_llm", None) or llm_config
        )
        self._qa_llm_cfg = (
            getattr(hugegraph_config, "he_qa_llm", None) or llm_config
        )
        self._classifier = doc_type_classifier
        # v1.8.8: langchain Embeddings (_LakeEmbedderAdapter) for build_index.
        # None = parse-only (old behaviour); required for dataset granularity.
        self._embedder = embedder
        self._kg_granularity = kg_granularity
        # [#1] TemplateTypeSelector — structural Auto-Type axis, orthogonal to
        # doc_type. ``template_type`` is an optional config default (e.g.
        # he_template_type=temporal_graph); per-call extract(template_type=)
        # overrides it. None → DocTypeRouter drives selection (unchanged).
        from arrow_lake.knowledge_graph.template_type_selector import (
            TemplateTypeSelector,
        )
        self._type_selector = TemplateTypeSelector()
        self._default_template_type = template_type
        self._template_cache: dict[str, Any] = {}
        self._doc_type_cache: dict[str, str | None] = {}  # content digest → inferred doc_type
        # langchain ChatOpenAI clients (thread-safe, reused across parses). A fresh
        # hyper-extract KA is created per parse to avoid AutoGraph accumulating
        # mutable state (data/indexes) across many chunks → corrupted/empty output.
        # [#KG-LLM-split] separate cached clients for extract (build) vs qa (chat).
        self._extract_client: Any = None
        self._qa_client: Any = None
        # §12.6.① langchain-openai 1.3.x does not propagate ChatOpenAI(api_key=...)
        # to the underlying openai client, so OPENAI_API_KEY must be in the env.
        # Only set when absent (setdefault semantics) and log when an existing
        # DIFFERENT key wins, so a second extractor with another key is not silent.
        # Seeded from the EXTRACT config (build runs first / most frequent). The QA
        # client, if it uses 百炼 (aliyuncs), goes through hyperextract.create_client
        # which carries its own key and does not depend on this env var.
        _existing = os.environ.get("OPENAI_API_KEY")
        _want = self._extract_llm_cfg.api_key or "dummy"
        if _existing is None:
            os.environ["OPENAI_API_KEY"] = _want
        elif _existing != _want:
            logger.debug("OPENAI_API_KEY already set; not overridden by this extractor")

    def _build_client(self, cfg: Any, model: str) -> Any:
        """Build a langchain chat client from an LLMConfig + model name.

        Routes 百炼 (aliyuncs MaaS) through hyper-extract's official
        ``create_client`` instead of a direct langchain ChatOpenAI: ① direct
        ChatOpenAI has an api_key propagation bug once hyperextract is imported
        (openai.OpenAIError: Missing credentials); ② create_client handles the
        bailian provider config; ③ non-thinking models (qwen-turbo) make feed_text
        fast (~7.7s/chunk vs 8min for thinking models). The structured-output
        (``.parse()`` / json_schema) binding is applied by hyper-extract's template
        at EXTRACTION time (feed_text), NOT here — the returned client is a plain
        generator usable for both extraction and chat.
        """
        api_base = cfg.api_base or ""
        if "aliyuncs" in api_base:
            from hyperextract import create_client

            llm, _emb = create_client(
                f"bailian:{model}@{api_base}",
                api_key=cfg.api_key or "dummy",
            )
            return llm
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model,
            api_key=cfg.api_key or "dummy",
            base_url=cfg.api_base or None,
            temperature=0,
            # hyper-extract 抽取走结构化输出(.parse()); thinking 模型需撑大 max_tokens
            # 让 thinking + 结构化输出都装下,否则返空。走 cfg.max_tokens(默认 2048,
            # 生产用 ARROW_LAKE__LLM__MAX_TOKENS=8192 覆盖),避免硬编码让 env 失效。
            max_tokens=getattr(cfg, "max_tokens", 8192),
        )

    def _get_extract_client(self) -> Any:
        """Cached LLM client for the EXTRACTION phase (kg_build / feed_text).

        Uses ``he_extract_llm`` if set, else the global llm (+ ``he_model`` name
        override). A fresh KA is created per parse via :meth:`_create_ka` to avoid
        the AutoGraph accumulating mutable state across many chunks.
        """
        if self._extract_client is None:
            model = self._model or self._extract_llm_cfg.model or ""
            self._extract_client = self._build_client(self._extract_llm_cfg, model)
        return self._extract_client

    def _get_resolution_provider(self) -> Any:
        """[entity resolution] Cached LLM provider (extract LLM, qwen-turbo-grade).

        Resolution is a simple synonymy judgement — the extract llm (fast/cheap)
        is enough; no need for the flagship qa model. ``create_llm_provider`` is
        used (not the hyper-extract client) so we can send a free-form JSON prompt.
        """
        if self.__dict__.get("_resolution_provider") is None:
            from arrow_lake.rag.provider import create_llm_provider

            self.__dict__["_resolution_provider"] = create_llm_provider(self._extract_llm_cfg)
        return self.__dict__["_resolution_provider"]

    async def resolve_entities(
        self, result: Any, *, embeddings: list[list[float]] | None = None,
    ) -> tuple[Any, dict[str, str]]:
        """[entity resolution] Wrapper: inject embedder + LLM provider + config
        into :func:`entity_resolver.resolve_entities`. Best-effort — returns the
        input unchanged on any failure (no embedder / resolution error).

        ``embeddings`` (v1.9.9 reuse): precomputed vectors aligned to
        ``result.entities``; when passed, the embedder is not re-invoked."""
        cfg = self._hugegraph_config
        if self._embedder is None:
            logger.warning("entity resolution needs an embedder; skipped")
            return result, {}
        embed_fn = self._embedder.embed_documents

        async def generate_fn(prompt: str) -> str:
            from arrow_lake.rag.provider import LLMMessage

            provider = self._get_resolution_provider()
            messages = [
                LLMMessage(role="system", content="你是实体消歧专家，只返回JSON。"),
                LLMMessage(role="user", content=prompt),
            ]
            resp = await provider.generate(messages)
            return getattr(resp, "content", "") or ""

        from arrow_lake.knowledge_graph.entity_resolver import resolve_entities as _resolve

        return await _resolve(
            result,
            embed_fn=embed_fn,
            generate_fn=generate_fn,
            threshold=getattr(cfg, "he_resolution_threshold", 0.86),
            batch=getattr(cfg, "he_resolution_batch", 8),
            embeddings=embeddings,
        )

    def _get_qa_client(self) -> Any:
        """Cached LLM client for the Q&A phase (ka.chat generative answer).

        Uses ``he_qa_llm`` if set, else the global llm. When QA and extract share
        the same underlying config (both unset → global llm), the extract client
        is reused so a single global llm still serves both phases (backward compat).
        """
        if self._qa_client is None:
            if self._qa_llm_cfg is self._extract_llm_cfg:
                self._qa_client = self._get_extract_client()
            else:
                self._qa_client = self._build_client(
                    self._qa_llm_cfg, self._qa_llm_cfg.model or ""
                )
        return self._qa_client

    def _get_chat_client(self) -> Any:
        """Backward-compat alias for the extraction-phase client."""
        return self._get_extract_client()

    def _resolve_template(
        self, doc_type: str | None, content: str, template_type: str | None,
    ) -> str:
        """Resolve the hyper-extract template path.

        Priority (v1.10.0): **per-build ``_active_template_override``** (explicit
        per-dataset binding) > per-call ``template_type`` > config default
        ``template_type`` > :class:`TemplateTypeSelector` temporal heuristic >
        ``DocTypeRouter``. The override is the SINGLE chokepoint covering all
        granularities (dataset / map_reduce / chunk — every path calls this).
        """
        override = getattr(self, "_active_template_override", None)
        if override:
            return self._resolve_template_path(override)
        tt = template_type or self._default_template_type
        selected = self._type_selector.select(
            template_type=tt, doc_type=doc_type, content=content,
        )
        if selected:
            return selected
        return self._router.resolve(doc_type)

    def _resolve_template_path(self, ref: str) -> str:
        """[v1.10.0] Resolve a template reference (bare name OR path) to a
        loadable path.

        Bare names are looked up in the gallery (user / project / preset);
        paths (contain ``/`` or end ``.yaml``) are used directly if the file
        exists. Raises ``ValueError`` if unresolved so the build fails fast with
        a clear message instead of a downstream "Template not found".
        """
        import os
        if not ref:
            raise ValueError("empty template override")
        if ("/" in ref or ref.endswith(".yaml")) and os.path.isfile(ref):
            return ref
        from arrow_lake.knowledge_graph.doc_type_router import get_template_gallery
        gallery = get_template_gallery()
        hit = gallery.get(ref)  # exact path match, e.g. "general/concept_graph"
        if hit is None:  # bare name → user/project template
            hit = next((t for t in gallery.templates if t.name == ref.lower()), None)
        if hit is None:
            raise ValueError(f"template not found: {ref!r}")
        return hit.path

    def _create_ka(self, template_path: str, *, llm_client: Any = None) -> Any:
        """Create a fresh hyper-extract KA with the given llm + this embedder.

        ``llm_client`` selects the phase: extraction callers pass the extract
        client (default), query callers (``load_ka_for_query``) pass the QA
        client so ``ka.chat`` generates with the flagship model while the dump's
        extracted data/index (built by the lightweight extract model) is reused.

        AutoGraph's default node/edge merger is ``MergeStrategy.LLM.BALANCED``
        (``hyperextract/types/graph.py:164-167``), so ``feed_text`` activates
        cross-chunk LLM field-merging out of the box — no explicit merger kwarg
        needed. ``embedder`` (a langchain ``Embeddings`` via
        ``_LakeEmbedderAdapter``) enables ``build_index``; ``None`` keeps the
        old parse-only behaviour (per-chunk granularity without index).
        """
        from hyperextract import Template
        # 合并策略: ontomem MergeStrategy. 默认 LLM.BALANCED 在规模上会"合并爆炸"(每个重叠
        # 实体触发一次 LLM 合并调用 → 累积内存/延迟, grouped 100 OOM / 50 末组卡死的真凶)。
        # MERGE_FIELD 非 LLM 字段级合并(新节点非空字段填充旧节点空字段): 无额外 LLM 调用、
        # 稳定、跨 chunk 去重 + definition 补全。配 dataset 粒度(一份 KA 喂全部 chunk)通用且稳。
        from ontomem.merger import MergeStrategy

        cfg = self._hugegraph_config
        return Template.create(
            template_path,
            self._language,
            llm_client=llm_client or self._get_extract_client(),
            embedder=self._embedder,
            chunk_size=cfg.he_chunk_size,
            chunk_overlap=cfg.he_chunk_overlap,
            max_workers=cfg.he_max_workers,
            node_strategy_or_merger=MergeStrategy.MERGE_FIELD,
            edge_strategy_or_merger=MergeStrategy.MERGE_FIELD,
        )

    def _ka_dir_for(self, dataset_name: str) -> Any:
        """[#2] Resolve the per-dataset KA dump dir.

        Uses :func:`artifact_key_for` so the KA dir stays in lockstep with the
        HugeGraph graph name (``kg_{artifact_key}``) — same source, can't diverge.
        For canonical names (e.g. ``jd_ddd``) this equals the raw dataset name.
        """
        from pathlib import Path
        from arrow_lake.knowledge_graph._naming import artifact_key_for
        return Path(self._hugegraph_config.he_ka_base_dir) / artifact_key_for(dataset_name) / "ka"

    def _snapshot_template_into_dump(self, template_path: str, ka_dir: Any) -> None:
        """[v1.10.0 §4.7] Make a KA dump self-contained for file-path templates.

        For user/project templates (absolute ``.yaml`` path, NOT a preset like
        ``general/concept_graph``), copy the template into ``ka_dir/template.yaml``
        and patch ``metadata.json``'s ``template`` field to that snapshot path.
        The query path (:meth:`_build_ka_for_query`) then loads the snapshot via
        the path guard (value contains ``/`` and ends ``.yaml`` → skips the
        project-dir-only stem resolution) — fixing the C1 "Template not found"
        for user templates — and the dump stays queryable even if the source
        template is later edited or deleted (H2). Presets are left untouched
        (stable in the installed package). Best-effort: never raises.
        """
        import json as _json
        import os
        import shutil
        if (not template_path or "/" not in template_path
                or not template_path.endswith(".yaml")):
            return  # preset path or empty → no snapshot needed
        if not os.path.isfile(template_path):
            logger.warning("snapshot: template file missing, skipped: %s", template_path)
            return
        try:
            snap = ka_dir / "template.yaml"
            shutil.copy2(template_path, snap)
            meta_path = ka_dir / "metadata.json"
            meta: dict = {}
            if meta_path.is_file():
                try:
                    meta = _json.loads(meta_path.read_text("utf-8"))
                except (OSError, ValueError):
                    meta = {}
            meta["template"] = str(snap.resolve())
            meta_path.write_text(_json.dumps(meta, ensure_ascii=False), "utf-8")
        except Exception as exc:  # noqa: BLE001 — best-effort; query degrades to default
            logger.warning("template snapshot into dump failed (%s): %s",
                           template_path, str(exc)[:160])

    def load_ka_for_query(self, dataset_name: str) -> Any:
        """[#2][#P0-4] Load a dumped per-dataset KA for search/chat, with an
        instance cache keyed by dataset and invalidated on dump mtime change.

        Reads the template from ``metadata.json`` (falling back to the default
        template), builds a fresh KA with this extractor's llm+embedder via
        :meth:`_create_ka`, then ``ka.load(ka_dir)`` to restore data + FAISS
        index. ``Template.load`` does not exist in this hyper-extract version,
        so we create-then-load.

        [#P0-4] Loading a large KA (``_create_ka`` + ``ka.load`` + index
        restore ≈ 60s on big graphs) on every query dominated GraphRAG latency
        once the vector/graph fan-out was parallelised. We now memoise the
        built KA per dataset and revalidate against the dump's mtime signature
        (``data.json`` + ``metadata.json`` + ``index/``); a kg_build re-run
        bumps the mtime and invalidates cleanly. Cache is per-instance (one
        extractor per Lake; bounded by dataset count). See
        :meth:`_ka_mtime_sig` / :meth:`_build_ka_for_query`.
        """
        ka_dir = self._ka_dir_for(dataset_name)
        sig = self._ka_mtime_sig(ka_dir)
        cache = self.__dict__.setdefault("_ka_query_cache", {})  # dataset -> (sig, ka)
        hit = cache.get(dataset_name)
        if hit is not None and hit[0] == sig:
            return hit[1]
        ka = self._build_ka_for_query(ka_dir)
        cache[dataset_name] = (sig, ka)
        return ka

    def _build_ka_for_query(self, ka_dir: Path) -> Any:
        """[#P0-4] Uncached KA build: read template from ``metadata.json``,
        create the KA (QA client), and load the dump. Isolated so
        :meth:`load_ka_for_query` can memoise its result."""
        import json
        template = self._router.default_template()
        meta_path = ka_dir / "metadata.json"
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                template = meta.get("template") or template
            except Exception:
                pass
        # metadata stores the template STEM (e.g. "ddd_concept_graph"); hyper-
        # extract's factory only recognises gallery preset paths ("general/…")
        # or full file paths. Project-local templates must be resolved back to
        # their full path or Template.create raises "Template not found".
        if template and "/" not in template and not template.endswith(".yaml"):
            candidate = _PROJECT_TEMPLATES_DIR / f"{template}.yaml"
            if candidate.is_file():
                template = str(candidate)
        # [#KG-LLM-split] query path uses the QA (flagship) client so ka.chat
        # generates answers with a stronger model than the lightweight extractor.
        ka = self._create_ka(template, llm_client=self._get_qa_client())
        ka.load(ka_dir)
        return ka

    @staticmethod
    def _ka_mtime_sig(ka_dir: Path) -> int | None:
        """[#P0-4] Mtime signature of a KA dump: the newest ns-precision mtime
        across ``data.json``, ``metadata.json`` and the ``index/`` subdir.
        ``None`` when the dump is absent (forces a rebuild attempt so callers
        get the usual not-found error rather than a stale cache hit)."""
        best = 0
        found = False
        for name in ("data.json", "metadata.json", "index"):
            p = ka_dir / name
            if p.exists():
                best = max(best, p.stat().st_mtime_ns)
                found = True
        return best if found else None

    def _ensure_ka_index(self, ka: Any, dataset_name: str) -> None:
        """[#2] Build the KA FAISS index only when the loaded KA lacks one.

        ``ka.load(ka_dir)`` already restores the index from the dump's
        ``index/`` subdir when present (hyper-extract ``base.load``). Rebuilding
        unconditionally wastes ~60s per query for large graphs, so we check the
        in-memory index state and build only when missing (e.g. the dump had no
        index, or index load failed). Errors are logged, not swallowed.
        """
        try:
            need = not (
                getattr(ka, "_node_memory", None) is not None and ka._node_memory.has_index()
                and getattr(ka, "_edge_memory", None) is not None and ka._edge_memory.has_index()
            )
        except AttributeError:
            need = True
        if need:
            try:
                ka.build_index()
            except Exception as exc:  # noqa: BLE001 — degrade to no-index (search will raise clearly)
                logger.warning(
                    "KA build_index failed for '%s' (search/chat will be unavailable): %s",
                    dataset_name, exc,
                )

    def search_ka(self, dataset_name: str, query: str, top_k: int = 5) -> tuple:
        """[#2] Semantic search over a dataset's KA: load → ensure index → search.

        Returns ``(nodes, edges)`` (hyper-extract ``AutoGraph.search`` result).
        Enables definition-based semantic recall that HugeGraph's keyword/graph
        traversal cannot do.
        """
        ka = self.load_ka_for_query(dataset_name)
        self._ensure_ka_index(ka, dataset_name)
        return ka.search(query, top_k=top_k)

    def chat_ka(self, dataset_name: str, question: str, top_k: int = 5) -> Any:
        """[#2] RAG Q&A over a dataset's KA: load → ensure index → chat.

        Returns a langchain ``AIMessage`` (``.content`` = answer,
        ``.additional_kwargs['retrieved_items']`` = source nodes/edges).
        """
        ka = self.load_ka_for_query(dataset_name)
        self._ensure_ka_index(ka, dataset_name)
        return ka.chat(question, top_k=top_k)

    def rebuild_ka_index(self, dataset_name: str) -> dict[str, Any]:
        """[#7] Rebuild a dataset's KA FAISS index from its dump — no LLM re-extract.

        Loads the dumped KA (data + whatever index), force-rebuilds the FAISS
        index over the current nodes/edges, and persists it back to the dump.
        Far cheaper than a full ``kg_build`` (no LLM extraction): use it when the
        index is stale/corrupt, the embedder changed, or the dump predates
        ``build_index``. arrow_lake has no incremental feed path — ``kg_build`` is
        a full re-extract — so this is the lightweight index-only refresh.

        Raises ``FileNotFoundError`` if no KA dump exists for ``dataset_name``.
        """
        ka_dir = self._ka_dir_for(dataset_name)
        if not (ka_dir / "data.json").is_file():
            logger.warning("no KA dump for dataset %r at %s", dataset_name, ka_dir)
            raise FileNotFoundError(
                f"no KA dump for dataset {dataset_name!r} — run kg_build first"
            )
        ka = self.load_ka_for_query(dataset_name)
        ka.build_index()  # force rebuild (bypasses _ensure_ka_index's "already loaded" guard)
        ka.dump(ka_dir)
        nodes = getattr(getattr(ka, "_node_memory", None), "items", None) or []
        edges = getattr(getattr(ka, "_edge_memory", None), "items", None) or []
        return {
            "dataset": dataset_name,
            "index_rebuilt": True,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "ka_dir": str(ka_dir),
        }

    def export_ka_obsidian(
        self, dataset_name: str, out_dir: Any, *,
        vault_name: str = "Knowledge Vault", overwrite: bool = False,
    ) -> dict[str, Any]:
        """[#5] Export a dataset's KA as an Obsidian vault (Markdown notes + wikilinks).

        One ``.md`` note per node (fields as YAML front-matter), every edge
        rendered as an ``[[wikilink]]`` under its source note. Open the resulting
        folder in Obsidian to roam the extracted graph in graph view. Only Graph-
       系 KA (concept_graph / domain templates) have relations — Record 系
        (list/set/model) export notes without links.

        Args:
            dataset_name: Lake dataset whose KA to export (must have a dump).
            out_dir: Destination vault directory (created if absent).
            vault_name: Title for the generated index note.
            overwrite: Overwrite an existing vault at ``out_dir``.

        Raises ``FileNotFoundError`` if no KA dump exists for ``dataset_name``.
        """
        ka_dir = self._ka_dir_for(dataset_name)
        if not (ka_dir / "data.json").is_file():
            logger.warning("no KA dump for dataset %r at %s", dataset_name, ka_dir)
            raise FileNotFoundError(
                f"no KA dump for dataset {dataset_name!r} — run kg_build first"
            )
        ka = self.load_ka_for_query(dataset_name)
        vault = ka.export_obsidian(
            out_dir, vault_name=vault_name, overwrite=overwrite,
        )
        nodes = getattr(getattr(ka, "_node_memory", None), "items", None) or []
        edges = getattr(getattr(ka, "_edge_memory", None), "items", None) or []
        return {
            "dataset": dataset_name,
            "vault_path": str(vault),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "vault_name": vault_name,
        }

    def _parse_fresh(self, template_path: str, text: str) -> Any:
        """Create a ONE-SHOT hyper-extract KA and parse ``text``.

        Each call builds a fresh KA (reusing the cached langchain client) so no
        mutable AutoGraph state persists between chunks (per-chunk granularity).
        """
        return self._create_ka(template_path).parse(text)

    def _get_type_enum(self, template_path: str) -> list[str] | None:
        """[#3] Extract the legal entity-type enum from a template's YAML
        (the type field description's ``之一：A/B/C`` clause). Returns ``None``
        when the template has no strict enum (generic concept_graph) — in that
        case no post-filtering is applied. Cached per template_path so each
        chunk reuses the parsed enum without re-reading the YAML."""
        cache = self.__dict__.setdefault("_type_enum_cache", {})
        if template_path in cache:
            return cache[template_path]
        import os
        import re as _re
        import hyperextract
        # Resolve like _create_ka: project-local bare stem first (templates/),
        # then gallery presets. Without this, project templates (ddd/medical/…
        # and the strict concept_graph default) were looked up only under the
        # gallery presets dir → never found → enum None → no type post-filter.
        if os.path.isfile(template_path):
            yml = template_path
        else:
            cand = _PROJECT_TEMPLATES_DIR / f"{template_path}.yaml"
            yml = str(cand) if cand.is_file() else os.path.join(
                os.path.dirname(hyperextract.__file__), "templates", "presets",
                template_path + ".yaml")
        enum: list[str] | None = None
        if os.path.isfile(yml):
            try:
                import yaml as _yaml
                data = _yaml.safe_load(open(yml, encoding="utf-8"))
                fields = ((data.get("output") or {}).get("entities") or {}).get("fields", [])
                tf = next((f for f in fields if isinstance(f, dict) and f.get("name") == "type"), None)
                if tf:
                    desc = tf.get("description", {})
                    dz = desc.get("zh", "") if isinstance(desc, dict) else str(desc or "")
                    dz_clean = _re.sub(r"（[^）]*）|\([^)]*\)", "", dz)  # strip (说明) so '/' inside doesn't break split
                    m = _re.search(r"之一[：:]\s*([^。]+)", dz_clean)
                    if m:
                        enum = [t.strip() for t in _re.split(r"[/、]", m.group(1)) if t.strip()]
            except Exception:
                enum = None
        cache[template_path] = enum
        return enum

    def _get_relation_enum(self, template_path: str) -> list[str] | None:
        """[#relation-snap] Extract the legal relation-type enum from a
        template's YAML (the relation ``type`` field description's
        ``之一：A/B/C`` clause). Mirrors ``_get_type_enum`` but reads
        ``output.relations.fields[name=type]``. Returns ``None`` when the
        template has no strict relation enum (no post-filter applied).

        Context (memory issue_kg_mapping_layer_fix): after the type enum was
        snapped to ~11 values, relation_type stayed noisy (186 distinct
        values) because LLMs emit English/synonym variants not in the enum.
        This parser + the snap in ``_ka_to_extraction_result`` collapse that
        noise into the template's declared vocabulary."""
        cache = self.__dict__.setdefault("_relation_enum_cache", {})
        if template_path in cache:
            return cache[template_path]
        import os
        import re as _re
        import hyperextract
        if os.path.isfile(template_path):
            yml = template_path
        else:
            cand = _PROJECT_TEMPLATES_DIR / f"{template_path}.yaml"
            yml = str(cand) if cand.is_file() else os.path.join(
                os.path.dirname(hyperextract.__file__), "templates", "presets",
                template_path + ".yaml")
        enum: list[str] | None = None
        if os.path.isfile(yml):
            try:
                import yaml as _yaml
                data = _yaml.safe_load(open(yml, encoding="utf-8"))
                fields = ((data.get("output") or {}).get("relations") or {}).get("fields", [])
                tf = next((f for f in fields if isinstance(f, dict) and f.get("name") == "type"), None)
                if tf:
                    desc = tf.get("description", {})
                    dz = desc.get("zh", "") if isinstance(desc, dict) else str(desc or "")
                    dz_clean = _re.sub(r"（[^）]*）|\([^)]*\)", "", dz)
                    m = _re.search(r"之一[：:]\s*([^。]+)", dz_clean)
                    if m:
                        enum = [t.strip() for t in _re.split(r"[/、]", m.group(1)) if t.strip()]
            except Exception:
                enum = None
        cache[template_path] = enum
        return enum

    @staticmethod
    def _ka_to_extraction_result(ka: Any, raw_text: str = "", valid_types: list[str] | None = None, valid_relations: list[str] | None = None, strict_definition: bool = False) -> ExtractionResult:
        """Convert a hyper-extract KA (``nodes``/``edges``) to an ExtractionResult.

        Applies the same §11.3 stopword filter + endpoint validity as legacy
        ``EntityExtractor``, so the per-chunk (``parse``) and per-dataset
        (``feed_text``) paths share one conversion.
        """
        nodes = getattr(ka, "nodes", None) or []
        edges = getattr(ka, "edges", None) or []

        # nodes → entities (+ §11.3 stopword filter)
        # entity_type keeps the LLM-extracted RAW type (Chinese 概念/属性/架构
        # 组件/...). We deliberately do NOT collapse the raw type to a small
        # label set here (an English-only normalize map erased the 81-way type
        # info). route_entity_type does typed-vertex routing on this raw value.
        entities: list[ExtractedEntity] = []
        name_set: set[str] = set()
        for n in nodes:
            name = getattr(n, "name", None)
            name = str(name).strip() if name else ""
            # filter single-char names (e/int-style LLM noise like "e", "tp"
            # fragments) + generic stopwords. 2-3 char acronyms (DO/PO/API/DDD)
            # are legitimate DDD terms — kept.
            if not name or len(name) <= 1 or name in _GENERIC_ENTITY_STOPWORDS:
                continue
            entity_type = str(getattr(n, "type", "") or "").strip() or "未知"
            # [#3] type post-filter: LLM occasionally (~5%) emits a type outside
            # the template's strict enum (e.g. "概念" after we removed it). Snap
            # non-enum types to "实体" (most generic in the enum) so the graph's
            # type distribution stays within the template's vocabulary.
            if valid_types and entity_type != "未知" and entity_type not in valid_types:
                # v1.9.6 P0-3: 编辑距离归一化(「架构组件」→「组件」),非粗暴塌缩「实体」。
                import difflib
                _match = difflib.get_close_matches(entity_type, valid_types, n=1, cutoff=0.4)
                entity_type = _match[0] if _match else ("实体" if "实体" in valid_types else valid_types[0])
            definition = str(getattr(n, "definition", "") or "").strip()
            # v1.9.6 P0-3: strict mode drops entities with empty definition (LLM noise).
            if strict_definition and not definition:
                continue
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
            # [#relation-snap] collapse non-enum relation types (English
            # variants / synonyms like "related_to"/"is_a") into the template's
            # declared vocabulary. Prefer a generic "相关" if present, else the
            # first enum member; same defensive shape as the type snap above.
            if valid_relations and rel_type not in valid_relations:
                # v1.9.6 P0-3: 编辑距离归一化(「is_a」→「属于」等),非粗暴塌缩「相关」。
                import difflib
                _match = difflib.get_close_matches(rel_type, valid_relations, n=1, cutoff=0.4)
                rel_type = _match[0] if _match else ("相关" if "相关" in valid_relations else valid_relations[0])
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
        self, text: str, *, chunk_id: str = "", doc_type: str | None = None,
        template_type: str | None = None,
    ) -> ExtractionResult:
        """Extract entities/relations from ``text`` via hyper-extract (per-chunk).

        Gracefully degrades to an empty result on any failure (matching
        legacy ``EntityExtractor`` behavior). ``chunk_id`` is accepted for
        contract symmetry and used only for logging.

        [#1] ``template_type`` (one of graph/temporal_graph/hypergraph/list/set/
        model) overrides doc_type routing to pick a template of that Auto-Type.
        When None, the temporal heuristic may auto-pick temporal_graph; otherwise
        doc_type drives selection via DocTypeRouter (unchanged).
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

        template_path = self._resolve_template(doc_type, stripped, template_type)
        # [#racefix] keep the type enum LOCAL, not on self: extract() runs under
        # extract_batch's asyncio.gather, and a self attribute is clobbered by
        # overlapping coroutines (chunk A filtered by chunk B's enum).
        type_enum = self._get_type_enum(template_path)
        relation_enum = self._get_relation_enum(template_path)
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
                    type_enum = self._get_type_enum(default_path)
                    relation_enum = self._get_relation_enum(default_path)
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

        return self._ka_to_extraction_result(
            result, text, valid_types=type_enum, valid_relations=relation_enum,
            strict_definition=getattr(getattr(self, "_hugegraph_config", None), "he_strict_definition", False))

    @staticmethod
    def _is_transient_feed_error(exc: Exception) -> bool:
        """Heuristic: is a feed_text (LLM) failure worth retrying?"""
        msg = str(exc).lower()
        return any(k in msg for k in (
            "timeout", "timed out", "connection", "connect", "eof", "reset",
            "502", "503", "504", "overloaded", "rate limit", "retry", "unavailable",
        ))

    def _feed_with_retry(self, ka: Any, text: str, *, attempts: int = 3) -> None:
        """[#step4-B] feed_text with bounded retry+backoff on transient LLM errors.

        Hard failures (template/schema/4xx) raise on attempt 1 (no retry).
        Transient (timeout/5xx/conn) retries with 1s, 2s backoff. Prevents silent
        chunk loss on a rate-limit spike over a large corpus.
        """
        import time

        for attempt in range(attempts):
            try:
                ka.feed_text(text)
                return
            except Exception as exc:
                if attempt < attempts - 1 and self._is_transient_feed_error(exc):
                    time.sleep(1.0 * (2 ** attempt))  # 1s, 2s
                    continue
                raise

    async def build_dataset_ka(
        self,
        template_path: str,
        chunks: list[tuple[str, str]],
        ka_dir: Path,
        *,
        checkpoint_every: int = 20,
        incremental: bool = False,
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
        ka_dir = Path(ka_dir)
        ka_dir.mkdir(parents=True, exist_ok=True)
        entity_chunks: dict[str, list[str]] = {}
        failures = 0

        # [#incremental] When an existing dump is present + the template matches
        # + the caller asked incremental: load the KA and feed ONLY new chunks
        # (chunk_id not in fed_chunks). fed_chunks is a sidecar file (hyper-
        # extract owns metadata.json). chunk_id = str row-index is stable for
        # APPEND (the incremental use case); re-ingest or a template change must
        # go through a full rebuild (clear + rebuild). A template mismatch or a
        # missing/corrupt fed_chunks falls back to a full feed (correct, safe).
        import json as _json
        fed_path = ka_dir / "fed_chunks.json"
        def _chunk_hash(t: str) -> str:
            import hashlib
            return hashlib.sha1((t or "").encode("utf-8", "replace")).hexdigest()[:16]

        prev_fed_map: dict[str, str] = {}  # [#step3-C] {chunk_id: content_hash}
        can_incremental = False
        if incremental and (ka_dir / "data.json").is_file():
            try:
                meta = _json.loads((ka_dir / "metadata.json").read_text("utf-8"))
                prev_stem = (meta.get("template") or "").strip()
                can_incremental = prev_stem == Path(template_path).stem
                if can_incremental and fed_path.is_file():
                    raw = _json.loads(fed_path.read_text("utf-8"))
                    # backward-compat: legacy fed_chunks.json was list[str] of ids
                    # (no hash) → fed-with-unknown-hash so the content check
                    # re-feeds once, then hashes are established.
                    if isinstance(raw, dict):
                        prev_fed_map = {str(k): str(v) for k, v in raw.items()}
                    elif isinstance(raw, list):
                        prev_fed_map = {str(c): "" for c in raw}
            except Exception as exc:  # noqa: BLE001 — fall back to full feed
                logger.warning("incremental KA prep failed (%s): full feed", str(exc)[:120])
                can_incremental = False

        ka = self._create_ka(template_path)
        if can_incremental:
            try:
                await asyncio.to_thread(ka.load, ka_dir)
            except Exception as exc:  # noqa: BLE001 — corrupt/unreadable dump
                logger.warning("incremental KA load failed (%s): full feed", str(exc)[:120])
                ka = self._create_ka(template_path)
                can_incremental = False
                prev_fed_map = {}

        chunks_to_feed = (
            [(cid, text) for cid, text in chunks
             if prev_fed_map.get(cid, "") != _chunk_hash(text)]
            if can_incremental else list(chunks)
        )
        if can_incremental:
            logger.info(
                "incremental KA: %d total chunks, %d new (already fed=%d)",
                len(chunks), len(chunks_to_feed), len(prev_fed_map),
            )

        for i, (cid, text) in enumerate(chunks_to_feed):
            stripped = (text or "").strip()
            if not stripped:
                continue
            before = {getattr(n, "name", "") for n in (getattr(ka, "nodes", None) or [])}
            try:
                await asyncio.to_thread(self._feed_with_retry, ka, stripped)
            except Exception as exc:
                failures += 1
                logger.warning(
                    "build_dataset_ka feed_text failed chunk %s (%d/%d), skipped: %s",
                    cid, i + 1, len(chunks_to_feed), str(exc)[:160],
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
                    logger.debug(
                        "build_dataset_ka checkpoint at chunk %d/%d → %s",
                        i + 1, len(chunks_to_feed), ka_dir,
                    )
                except Exception as exc:
                    logger.warning(
                        "build_dataset_ka checkpoint dump failed: %s", str(exc)[:160]
                    )

        if failures:
            logger.debug(
                "build_dataset_ka done: %d/%d chunks skipped (template=%s)",
                failures, len(chunks_to_feed), template_path,
            )

        # feed_text invalidates the index (omem clear_index); rebuild once, then dump.
        # build_index (FAISS via embedder) is for RAG semantic search over the KA — it is
        # DECOUPLED from KG insertion: entities/relations are already extracted in the KA.
        # An embedder/FAISS failure must NOT fail the build — warn + continue so the builder
        # still dumps + inserts KG vertices (only the RAG index is skipped).
        if not ka.empty():
            try:
                # wait_for: a build_index HANG (e.g. ollama embedder deadlock / FAISS stall)
                # becomes a TimeoutError → caught below → non-fatal. Without this a hang blocks
                # the build forever (try/except only catches errors, not hangs).
                await asyncio.wait_for(asyncio.to_thread(ka.build_index), timeout=600)
            except Exception as exc:  # noqa: BLE001 — RAG index is best-effort, non-fatal (incl. TimeoutError)
                logger.warning(
                    "build_dataset_ka build_index failed/timed-out — KA RAG index skipped, "
                    "KG insert continues: %s", str(exc)[:160],
                )
        await asyncio.to_thread(ka.dump, ka_dir)

        # v1.10.0 §4.7 (C1/H2): snapshot the file-path template into the dump so
        # the query path can reload it (user templates otherwise "Template not
        # found" — _build_ka_for_query resolves stems against the project dir
        # only) and the dump stays queryable if the source is later edited/deleted.
        self._snapshot_template_into_dump(template_path, ka_dir)

        # [#incremental] persist the full set of fed chunk_ids so the next
        # incremental build can diff. Best-effort: a missing sidecar just means
        # the next build re-feeds everything (correct, not lossy).
        try:
            all_fed_map = {
                **prev_fed_map,
                **{cid: _chunk_hash(text) for cid, text in chunks_to_feed},
            }
            fed_path.write_text(_json.dumps(all_fed_map), "utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("fed_chunks sidecar write failed: %s", str(exc)[:120])

        # Pass the resolved type/relation enums so the dataset path (default
        # granularity) also gets post-filtering — previously only the per-chunk
        # path did, so non-enum LLM noise (entity "实体/方法"; relation English
        # variants) flowed straight into HugeGraph.
        result = self._ka_to_extraction_result(
            ka, valid_types=self._get_type_enum(template_path),
            valid_relations=self._get_relation_enum(template_path),
            strict_definition=getattr(getattr(self, "_hugegraph_config", None), "he_strict_definition", False))
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
