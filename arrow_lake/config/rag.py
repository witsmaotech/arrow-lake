"""RAG and LLM configuration — LLM, RAG, HugeGraph."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator

from arrow_lake.config._enums import LLMProviderType


class LLMConfig(BaseModel):
    """LLM provider configuration for RAG generation.

    Attributes:
        provider: LLM backend type (openai, anthropic, vllm, ollama).
        model: Model name to use for generation.
        api_key: API key (empty for local models).
        api_base: Custom API base URL (empty = use provider default).
        temperature: Sampling temperature (0.0-2.0).
        max_tokens: Maximum tokens to generate.
        context_window_tokens: Model context window size for budget calculation.
        timeout_seconds: HTTP request timeout.
    """

    provider: LLMProviderType = LLMProviderType.OPENAI
    model: str = "gpt-4o-mini"
    api_key: str = ""
    api_base: str = ""
    temperature: float = 0.7
    max_tokens: int = 2048
    context_window_tokens: int = 128000
    timeout_seconds: float = 60.0
    anthropic_version: str = "2023-06-01"

    @field_validator("temperature")
    @classmethod
    def validate_temperature(cls, v: float) -> float:
        if not 0.0 <= v <= 2.0:
            raise ValueError(f"temperature must be 0.0-2.0, got {v}")
        return v

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, v: float) -> float:
        if v < 1.0:
            raise ValueError(f"timeout_seconds must be >= 1.0, got {v}")
        return v

    @field_validator("max_tokens")
    @classmethod
    def validate_max_tokens(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"max_tokens must be >= 1, got {v}")
        return v


class RAGConfig(BaseModel):
    """RAG pipeline configuration.

    Attributes:
        enabled: Whether RAG endpoints are active.
        default_retrieval_strategy: Default retrieval strategy (vector, fts, hybrid).
        default_top_k: Default number of results to retrieve.
        max_context_chunks: Maximum chunks in context window.
        context_budget_ratio: Fraction of context window for retrieved chunks.
        system_prompt: Default system prompt override.
        history_dataset: Lance dataset name for session history.
        enable_citations: Whether to track and return citations.
    """

    enabled: bool = False
    default_retrieval_strategy: str = "hybrid"
    default_top_k: int = 10
    max_context_chunks: int = 20
    context_budget_ratio: float = 0.75
    system_prompt: str = ""
    history_dataset: str = "_rag_sessions"
    enable_citations: bool = True
    # v1.9.6 P0-1: faithfulness verification (lightweight [n] ref check).
    enable_verification: bool = False
    reranker: str = "ollama"
    reranker_model: str = "dengcao/Qwen3-Reranker-0.6B:F16"
    reranker_base_url: str = ""
    reranker_top_n: int = 10
    # v1.9.6 P0-2: cross-encoder device + warmup (bge-reranker-v2-m3 连续分精排)。
    reranker_device: str = "auto"
    reranker_warmup_on_init: bool = True
    query_transform: str = "none"
    hyde_max_tokens: int = 256
    multi_query_variants: int = 3
    history_injection_enabled: bool = True
    history_budget_ratio: float = 0.2
    history_max_turns: int = 6
    # [#RAG-LLM-split] RAG 两阶段独立 LLM(对称,镜像 KG 的 he_extract_llm/he_qa_llm)。
    #   extract_llm: 抽取/重排阶段(默认走全局轻量 llm;设了可用百炼 qwen-turbo 等)。
    #   qa_llm:      问答生成阶段(设旗舰 qwen-max@百炼 可显著提质量 + 不依赖本地 ollama)。
    # 任一为 None → 回退全局 llm。两者独立。
    extract_llm: LLMConfig | None = None
    qa_llm: LLMConfig | None = None

    @field_validator("context_budget_ratio")
    @classmethod
    def validate_ratio(cls, v: float) -> float:
        if not 0.1 <= v <= 0.95:
            raise ValueError(f"context_budget_ratio must be 0.1-0.95, got {v}")
        return v

    @field_validator("default_top_k")
    @classmethod
    def validate_top_k(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"default_top_k must be >= 1, got {v}")
        return v


class HugeGraphConfig(BaseModel):
    """HugeGraph 知识图谱配置 (M3).

    Attributes:
        enabled: Whether KG functionality is active.
        host: HugeGraph server hostname.
        port: HugeGraph REST API port.
        graph_name: Name of the graph in HugeGraph.
        timeout_seconds: HTTP request timeout.
        username: Auth username (empty = no auth).
        password: Auth password.
        build_batch_size: Vertices/edges per batch insert.
        build_concurrency: Max parallel LLM calls during entity extraction.
        build_batch_delay: Seconds to wait between extraction batches.
        default_traversal_depth: Default hop depth for graph queries.
        max_traversal_depth: Maximum allowed traversal depth.
    """

    enabled: bool = False
    host: str = "localhost"
    port: int = 8091
    graph_name: str = "hugegraph"
    # Storage backend — must match the HugeGraph server deployment. rocksdb
    # (single-node, default) gives true per-graph store isolation; hstore is the
    # PD-cluster distributed backend. ``ensure_graph`` creates per-dataset graphs
    # with this backend (and the matching task scheduler).
    backend: str = "rocksdb"
    timeout_seconds: float = 30.0
    username: str = ""
    password: str = ""
    build_batch_size: int = 50
    build_concurrency: int = 3
    # Write-side semaphore for batch inserts, SEPARATE from build_concurrency
    # (which gates LLM extraction). HugeGraph rocksdb is the write bottleneck,
    # so this defaults lower to avoid saturating it into "too busy to write".
    write_concurrency: int = 2
    build_batch_delay: float = 0.5
    default_traversal_depth: int = 2
    max_traversal_depth: int = 5
    vermeer_host: str = "localhost"
    vermeer_port: int = 8081
    # --- v1.7.0 hyper-extract 抽取后端（§4.3）---
    # hyper-extract 为默认 KG 抽取组件（结构化模板 + doc_type 路由，质量优于 legacy 通用 prompt）。
    # legacy 通用 LLM 抽取器作为回退保留。
    extractor_backend: Literal["legacy", "he"] = "he"
    # he_default_template MUST be a usable extraction template (the base_* presets
    # are AutoType base classes, not directly extractable → "Template not found").
    # Project-local entity_graph (default general extraction: concrete entities +
    # explicit/implicit relations, strict type/relation enum + required
    # definition + concise naming). concept_graph reserved for taxonomy/concept
    # content. See arrow_lake/knowledge_graph/templates/.
    he_default_template: str = "entity_graph"
    # Common doc_types → fitting templates; unmapped falls back to he_default_template.
    # NOTE: only canonical keys here — aliases (e.g. "guide"→"manual", "论文"→"paper")
    # collapse via normalize_doc_type before lookup, so listing them is redundant.
    he_doc_type_templates: dict[str, str] = {
        # paper/report(含 tech/技术/技术文档 alias)→ entity_graph(通用实体抽取);
        # concept_graph 仅留给 concept/taxonomy 场景(走 tag 匹配/default)。
        "paper": "entity_graph",
        "report": "entity_graph",
        "manual": "general/workflow_graph",
        "biography": "general/biography_graph",
        # [#9] 多领域项目模板 (arrow_lake/knowledge_graph/templates/*.yaml) —
        # tight type/relation 枚举 + 必填定义，避免 general/concept_graph 的自由类型 +
        # optional description 导致的 0% 描述 + 80+ 类型。override 优先于 gallery 自动匹配。
        "medicine": "medical_concept_graph",
        "legal": "legal_concept_graph",
        "finance": "finance_concept_graph",
        "project": "project_concept_graph",
    }
    he_language: Literal["zh", "en"] = "zh"
    he_model: str | None = None
    # [#KG-LLM-split] 两阶段独立 LLM（None → 回退全局 llm，向后兼容）。
    # KG 流程 LLM 用于关注点不同的两阶段：
    #   - 抽取构建 (kg_build → feed_text)：需 json_schema 约束 + 高吞吐 + 低成本 → 轻量
    #     模型够用 (ministral-3:3b / 百炼 qwen-turbo)。
    #   - RAG 问答 (load_ka_for_query → ka.chat 生成)：需强推理 + 中文生成 → 旗舰更合适
    #     (deepseek-v3 / qwen-max / qwen3.5:27b)。
    # he_extract_llm: 抽取阶段完整 LLM 配置。None → 全局 llm（he_model 名覆盖仍生效）。
    # he_qa_llm: 问答阶段完整 LLM 配置。None → 全局 llm。设成旗舰可显著提升回答质量。
    # 优先级：he_extract_llm(全配置) > he_model(仅名) > 全局 llm。
    he_extract_llm: LLMConfig | None = None
    he_qa_llm: LLMConfig | None = None
    # v1.8.8 per-dataset KA 抽取粒度。v1.9.4: 合并策略改 MERGE_FIELD(非 LLM, 见
    # he_extractor._create_ka), BALANCED 合并爆炸消除 → dataset 对任意规模都稳定,
    # 不再需要 grouped 分组档(已移除)。
    # "map_reduce"(默认, v1.9.11 起统一) = 并发 per-chunk 抽取(不 insert)→ 全局精确名去重 +
    #   provenance 合并 → entity_resolver 同义消歧 + type-pair 过滤 → 一次 insert。
    #   兼得并发速度与全局合并质量, 绕开 dataset path 串行/卡死。wuhu 等大数据集已验证稳定。
    # "auto" = 按 chunk 数自动——N > chunk_min_chunks 用 map_reduce, 否则 dataset。
    # "dataset" = 整 dataset 一个 KA, chunk 逐个 feed_text, 跨 chunk MERGE_FIELD 字段合并
    #   (非 LLM 合并, 无爆炸, 任意规模稳定; build_index 已解耦, KG 入库可靠)。
    # "chunk"   = 旧 per-chunk fresh KA.parse() 路径, 无合并 (并发快, 无统一 KA dump)。
    he_kg_granularity: Literal["auto", "dataset", "chunk", "map_reduce"] = "map_reduce"
    # auto 阈值(builder.py 只读此项: N > chunk_min_chunks → map_reduce, 否则 dataset)。
    he_kg_chunk_min_chunks: int = 500    # N > 此值 → map_reduce
    # Local filesystem root for per-dataset KA dumps (<root>/<dataset>/ka/).
    # MUST be a local path — hyper-extract's ``ka.dump`` writes data.json /
    # metadata.json / index/ to the filesystem, NOT to minio/s3. The storage
    # ``base_uri`` is a bucket name ("arrow-lake") and must NOT be used here.
    # Defaults to /data/ka (same volume as metaflow in the deploy compose).
    he_ka_base_dir: str = "/data/ka"
    # v1.10.0: 用户知识抽取模板目录(writable volume)。CRUD 经 /admin/extraction-templates
    # 写盘 → reset_gallery_cache() 刷内存 gallery → 下次 kg_build 生效(不 rebuild/不 restart)。
    # MUST 指可写卷(/data/lake/templates),read_only 根 FS 下 /app/ 不可写(同 he_ka_base_dir)。
    he_user_templates_dir: str = "/data/lake/templates"
    # hyper-extract chunking / concurrency tuning (#10) — passed through to
    # Template.create → AutoGraph.__init__. Defaults match hyper-extract.
    he_chunk_size: int = 2048
    he_chunk_overlap: int = 256
    he_max_workers: int = 10
    # [#11] Max archived KA versions to retain per dataset (prune oldest beyond
    # this after each kg_build). 0 = keep all (no prune); negative = keep all.
    # Versioning archives the pre-build dump to <base>/<ds>/ka/versions/v{ts}/
    # so a regressive/failed rebuild can be rolled back.
    he_ka_max_versions: int = 5
    # v1.9.6 P0-3: strict mode — drop entities with empty definition (noise reduction).
    he_strict_definition: bool = False
    # [#1] Optional structural Auto-Type override (orthogonal to doc_type):
    # one of graph/temporal_graph/hypergraph/list/set/model. When set, the
    # TemplateTypeSelector picks that Auto-Type's template instead of doc_type
    # routing (hypergraph is opt-in + warned — high-risk on sparse content).
    # None = doc_type drives selection (DocTypeRouter); temporal heuristic may
    # still auto-pick temporal_graph on event-heavy content. See
    # template_type_selector.py.
    he_template_type: str | None = None
    # KG 实体消歧后处理(per-dataset 抽取后):off=禁用;auto=embedding 聚类 +
    # 批量 LLM 合并同义实体(如"应急指挥中心"/"市应急指挥中心"→一个 canonical)。
    # 治 MERGE_FIELD 只精确名合并导致的同实体多顶点/碎片/孤立。默认 off(opt-in,增 LLM 成本)。
    he_entity_resolution: Literal["off", "auto"] = "off"
    he_resolution_threshold: float = 0.86   # cosine 合并阈值
    he_resolution_batch: int = 8             # 每批送 LLM 的候选簇大小
    # v1.9.8 map_reduce: 合并阶段是否对 project_concept_graph 关系做 type-pair
    # 白名单过滤(丢弃无语义边,如"金额—训练→硬件")。仅 project_concept_graph 生效,
    # 其他模板 type 体系不同 → 跳过。默认开。
    he_kg_type_pair: bool = True
    # v1.9.9 孤儿点连接: 软降级 + 消歧后, 仍无任何 entity↔entity 边的孤立顶点,
    # 按共现(同 chunk)+ embedding 余弦 + type-pair 合法动词启发式连接到已连通实体
    # (无额外 LLM)。三重证据门控——共现是证据, 不臆造常识关系。默认 "auto"(开):
    # embedder 恒在(he 后端), 最坏只是几条低权 related_to, 可 off 关。仅 project_concept_graph。
    he_orphan_linking: Literal["off", "auto"] = "auto"
    he_orphan_threshold: float = 0.75    # 孤儿连接 cosine 阈值(低于消歧 0.86, "相关"<"同义")
    he_orphan_max_partners: int = 3      # 每个孤立最多新增几条边
    he_orphan_max_links: int = 500       # 全局新增边上限(封顶, 控制噪声与工作量)

    @field_validator("max_traversal_depth")
    @classmethod
    def validate_max_depth(cls, v: int) -> int:
        if not 1 <= v <= 10:
            raise ValueError(f"max_traversal_depth must be 1-10, got {v}")
        return v

    @field_validator("build_batch_size")
    @classmethod
    def validate_batch_size(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"build_batch_size must be >= 1, got {v}")
        return v

    @field_validator("write_concurrency")
    @classmethod
    def validate_write_concurrency(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"write_concurrency must be >= 1, got {v}")
        return v

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, v: float) -> float:
        if v < 1.0:
            raise ValueError(f"timeout_seconds must be >= 1.0, got {v}")
        return v
