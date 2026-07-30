# RAG 问答管线

> 版本：1.9.6

Arrow Lake 内置 RAG（检索增强生成）管线，支持多检索策略、流式输出、
多轮对话和知识图谱增强。返回 `RAGResponse`，包含回答、引用来源和性能指标。

> 前置准备：安装依赖 `pip install arrow-lake[rag]`，配置 LLM 提供商，
> 并确保目标数据集已嵌入向量索引。

***

## 0. 前置准备：创建向量索引

RAG 查询依赖目标数据集上的向量索引。若尚未创建，请先执行以下步骤：

```python
import numpy as np
import pyarrow as pa
from arrow_lake import Lake

lake = Lake(base_uri="./data")

# 1. 摄入文档
report = lake.ingest("docs", ["guide.md"])
print(f"摄入 {report.total_rows} 行")

# 2. 生成嵌入向量（替换为实际嵌入模型）
#    列名需与 RAG 管线期望的一致（默认：text_embedding）
DIM = 1024  # bge-m3 / qwen3-embedding 维度（占位；实际用配置的嵌入模型）
embeddings = np.random.randn(report.total_rows, DIM).astype(np.float32)
embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)
vec_table = pa.table({
    "text_embedding": pa.FixedSizeListArray.from_arrays(embeddings.ravel(), DIM),
})
lake.append_dataset("docs", vec_table)

# 3. 创建向量索引
lake.create_vector_index("docs", "text_embedding")

# 4. 可选：创建全文索引用于混合检索
lake.create_fts_index("docs", columns=["text_content"])

# 5. RAG 已就绪
import asyncio
response = asyncio.run(lake.rag_query("什么是 Arrow Lake？", "docs"))
```

> **注意**：若未创建向量索引，`rag_query()` 会回退为纯 FTS 检索（如有），
> 或在无可用检索策略时抛出 `RAGError`。

***

## 1. 基础 RAG 查询

`Lake.rag_query()` 执行完整的检索 - 增强 - 生成流程，返回结构化回答和引用。

```python
import asyncio
from arrow_lake import Lake

lake = Lake(base_uri="./data")

response = asyncio.run(
    lake.rag_query("什么是 Arrow Lake 的核心架构？", "docs")
)

# 如果在已有事件循环中运行（Jupyter、FastAPI 等），
# 直接使用 `await` 代替 asyncio.run()：
#   response = await lake.rag_query("...", "docs")

print(response.answer)
print(f"检索文档数：{response.retrieval_count}")
print(f"上下文 Token: {response.context_tokens}")
print(f"延迟：{response.latency_ms} ms")

for citation in response.citations:
    print(
        f"  来源：[{citation.chunk_index}] "
        f"数据集={citation.dataset}, "
        f"行 ID={citation.row_id}, "
        f"相关度={citation.score:.2f}"
    )
    print(f"    摘录：{citation.text_excerpt[:80]}...")
```

`RAGResponse` 字段一览：

| 字段                | 类型                        | 说明               |
| ----------------- | ------------------------- | ---------------- |
| `answer`          | `str`                     | LLM 生成的回答文本      |
| `citations`       | `tuple[RAGCitation, ...]` | 引用来源列表           |
| `retrieval_count` | `int`                     | 检索到的文档块数         |
| `context_tokens`  | `int \| None`             | 上下文窗口使用的 Token 数 |
| `verification`    | `dict \| None`            | 忠实度校验结果（v1.9.6；需开 `enable_verification`，含 support_ratio/sentences/valid_refs） |
| `latency_ms`      | `float \| None`           | 端到端延迟（毫秒）        |
| `session_id`      | `str \| None`             | 会话标识符            |

***

## 2. 流式响应

`Lake.rag_query_stream()` 逐 Token 返回生成内容，适合实时展示场景。
底层使用 SSE（Server-Sent Events）协议传输。

```python
import asyncio
from arrow_lake import Lake

lake = Lake(base_uri="./data")


async def stream_rag_response():
    """流式 RAG 响应，逐片段输出。"""
    full_answer = []
    async for chunk in lake.rag_query_stream(
        "解释 DuckLake 物化视图的工作原理", "docs"
    ):
        full_answer.append(chunk)
        print(chunk, end="", flush=True)

    print(f"\n\n完整回答长度：{sum(len(c) for c in full_answer)} 字符")


asyncio.run(stream_rag_response())
```

**在 FastAPI 中返回 SSE**：使用 `StreamingResponse` 包装异步生成器，
设置 `media_type="text/event-stream"`，逐块 `yield f"data: {chunk}\n\n"`。

***

## 3. 多轮对话

通过 `session_id` 参数启用会话历史，管线自动保存每轮问答，后续查询可
引用之前的上下文。

```python
import asyncio
from arrow_lake import Lake

lake = Lake(base_uri="./data")
session_id = "user-123-session-abc"

async def multi_turn():
    # 第一轮
    r1 = await lake.rag_query("Arrow Lake 支持哪些向量索引？", "docs",
                              session_id=session_id)
    print(f"A1: {r1.answer}\n")

    # 第二轮 — 上下文延续
    r2 = await lake.rag_query("其中哪种适合百万级数据集？", "docs",
                              session_id=session_id)
    print(f"A2: {r2.answer}\n")

    # 查看会话历史
    history = lake.rag_get_history(session_id)
    print(f"共 {len(history)} 轮对话")

asyncio.run(multi_turn())
```

> 会话历史持久化需要配置 `rag.history_dataset`，默认使用 `_rag_sessions`。
> 如果没有配置会话存储，`rag_get_history()` 返回空列表。

***

## 4. 检索策略

`strategy` 参数控制文档检索方式，支持三种策略：

| 策略   | 值          | 说明               |
| ---- | ---------- | ---------------- |
| 全文搜索 | `"fts"`    | 关键词匹配，适合精确术语查询   |
| 向量搜索 | `"vector"` | 语义相似度，适合自然语言问题   |
| 混合搜索 | `"hybrid"` | RRF 融合，综合相关度（默认） |

```python
import asyncio
from arrow_lake import Lake

lake = Lake(base_uri="./data")

async def compare_strategies():
    question = "Arrow Lake 如何处理数据版本控制？"

    r_fts = await lake.rag_query(question, "docs", strategy="fts")
    print(f"[FTS]   检索 {r_fts.retrieval_count} 块，{r_fts.latency_ms} ms")

    r_vec = await lake.rag_query(question, "docs", strategy="vector")
    print(f"[Vector] 检索 {r_vec.retrieval_count} 块，{r_vec.latency_ms} ms")

    r_hybrid = await lake.rag_query(question, "docs", strategy="hybrid")
    print(f"[Hybrid] 检索 {r_hybrid.retrieval_count} 块，{r_hybrid.latency_ms} ms")

asyncio.run(compare_strategies())
```

默认策略由 `rag.default_retrieval_strategy` 配置控制（默认 `"hybrid"`，v1.9.5 起真正生效——
此前 strategy 仅用于选 score 列名，检索实际只走 FTS，向量索引 0 参与）：

```python
from arrow_lake.config import ArrowLakeConfig

config = ArrowLakeConfig()
config.rag.default_retrieval_strategy = "hybrid"  # "fts" | "vector" | "hybrid"
config.rag.default_top_k = 10                      # 默认检索文档数
```

**`use_kg` per-query 开关**（v1.9.5）：`rag_query(..., use_kg=False)` 单次降级为纯向量/FTS，
**无需关闭 `hugegraph.enabled`** 做对比；默认 True，hugegraph 启用时自动注入图谱上下文。

***

## 5. 上下文窗口管理

RAG 管线通过 `ContextWindow` 管理 Token 预算，避免超出 LLM 上下文窗口限制。

| 参数                      | 配置项                         | 默认值       | 说明            |
| ----------------------- | --------------------------- | --------- | ------------- |
| `top_k`                 | `rag.default_top_k`         | `10`      | 检索文档数         |
| `max_context_chunks`    | `rag.max_context_chunks`    | `20`      | 上下文最大块数       |
| `context_budget_ratio`  | `rag.context_budget_ratio`  | `0.75`    | 上下文占 LLM 窗口比例 |
| `context_window_tokens` | `llm.context_window_tokens` | `128,000` | LLM 总上下文窗口    |

```python
import asyncio
from arrow_lake.config import ArrowLakeConfig
from arrow_lake import Lake

config = ArrowLakeConfig()
config.rag.default_top_k = 15
config.rag.max_context_chunks = 20
config.rag.context_budget_ratio = 0.75       # 75% 用于检索上下文
config.llm.context_window_tokens = 128_000    # LLM 总窗口

lake = Lake(base_uri="./data", config=config)

response = asyncio.run(
    lake.rag_query("详细介绍 Arrow Lake 的存储层设计", "docs", top_k=15)
)

# 有效上下文 Token = budget_ratio * context_window_tokens
print(f"上下文 Token: {response.context_tokens}")
print(f"检索块数：{response.retrieval_count}")
```

**工作原理**：检索 `top_k` 篇文档，逐块加入 `ContextWindow`，自动去重，
超出 Token 预算时截断，超出 `max_context_chunks` 时停止添加。

***

## 6. Prompt 模板

通过 `template` 参数选择 Jinja2 提示词模板。

| 模板名              | 类型      | 说明               |
| ---------------- | ------- | ---------------- |
| `default_qa`     | QA      | 默认问答模板，基于上下文回答问题 |
| `entity_extract` | EXTRACT | 从文本中提取命名实体       |
| `summarize`      | SUMMARY | 摘要模板，保留关键事实      |
| `graph_qa`       | QA      | 结合文档和知识图谱的问答模板   |

```python
import asyncio
from arrow_lake import Lake
lake = Lake(base_uri="./data")

r1 = await lake.rag_query("安全机制有哪些？", "docs", template="default_qa")
r2 = await lake.rag_query("组件依赖关系？", "docs", template="graph_qa")
r3 = await lake.rag_extract(
    text="Arrow Lake 使用 Lance 格式存储，DuckDB 提供分析能力。",
    schema={"entities": "list[str]", "relationships": "list[tuple[str,str,str]]"},
    dataset_name="docs",
)
```

**查看和注册自定义模板**：

```python
from arrow_lake.rag.prompt import PromptTemplate, PromptType, PromptRegistry

registry = PromptRegistry()

# 列出所有模板
print(registry.list_templates())

# 按类型筛选
for t in registry.list_by_type(PromptType.QA):
    print(f"  {t.name}: {t.description}")

# 注册自定义模板
registry.register(PromptTemplate(
    name="zh_strict_qa",
    type=PromptType.QA,
    description="严格基于上下文回答，拒绝编造",
    template=(
        "只使用以下上下文回答问题，信息不足时明确说明。\n\n"
        "上下文:\n{{ context }}\n\n"
        "问题：{{ question }}\n\n回答："
    ),
))
```

***

## 7. LLM 提供商配置

Arrow Lake 支持 OpenAI、Anthropic、Ollama 和 vLLM 四种提供商，
均通过 OpenAI 兼容接口（Anthropic 除外）通信。

### OpenAI

```python
from arrow_lake.config import ArrowLakeConfig

config = ArrowLakeConfig()
config.llm.provider = "openai"
config.llm.model = "gpt-4o-mini"
config.llm.api_key = "sk-..."
config.llm.temperature = 0.7
config.llm.max_tokens = 2048
config.llm.context_window_tokens = 128_000
```

### Anthropic

```python
config.llm.provider = "anthropic"
config.llm.model = "claude-sonnet-4-20250514"
config.llm.api_key = "sk-ant-..."
config.llm.max_tokens = 4096
config.llm.context_window_tokens = 200_000
```

> Anthropic 将 `system` 消息提取为 API 顶层 `system` 字段。

### Ollama（本地模型）

```python
config.llm.provider = "ollama"
config.llm.model = "qwen3:8b"
config.llm.api_base = "http://localhost:11434/v1"
config.llm.timeout_seconds = 120.0
```

> Ollama 自动为 qwen3.x 禁用 extended thinking，避免思考 Token 耗尽预算。

### vLLM

```python
config.llm.provider = "vllm"
config.llm.model = "Qwen/Qwen2.5-7B-Instruct"
config.llm.api_base = "http://localhost:8000/v1"
```

### YAML 配置

```yaml
# configs/rag.yaml
llm:
  provider: openai
  model: gpt-4o-mini
  temperature: 0.7
  max_tokens: 2048
  context_window_tokens: 128000
rag:
  enabled: true
  default_retrieval_strategy: hybrid
  default_top_k: 10
  max_context_chunks: 20
  enable_citations: true
```

```python
from arrow_lake import Lake
lake = Lake.from_yaml("configs/rag.yaml", base_uri="./data")
```

环境变量方式：`ARROW_LAKE__LLM__PROVIDER=openai`，
`ARROW_LAKE__RAG__DEFAULT_TOP_K=10` 等。

### 两阶段独立 LLM（v1.9.5）

RAG 框架支持抽取/重排与问答生成用**不同模型**（`rag.extract_llm` / `rag.qa_llm`，均为 `LLMConfig`，
任一为 None 回退全局 `llm`）。典型搭配：抽取用轻量 `qwen-turbo`（快），生成用旗舰 `qwen-plus@16384`
（≈ qwen-max 质量、便宜约 4.8 倍）。百炼（dashscope）须走代理，勿放入 `NO_PROXY`。

```yaml
rag:
  extract_llm: { provider: openai, api_base: "https://dashscope.aliyuncs.com/compatible-mode/v1",
                 api_key: "sk-...", model: qwen-turbo }
  qa_llm:      { provider: openai, api_base: "https://dashscope.aliyuncs.com/compatible-mode/v1",
                 api_key: "sk-...", model: qwen-plus, max_tokens: 16384, temperature: 0.3 }
```

> **查询变换**（可选）：`rag.query_transform`（`none`/`hyde`/`multi_query`）+ `multi_query_variants=3`
> 对复杂多 facet 问题并行多路检索再合并去重，提升召回。

***

## 8. 批量查询

`Lake.rag_batch_query()` 一次处理多个问题，返回 `RAGResponse` 列表。

```python
import asyncio
from arrow_lake import Lake

lake = Lake(base_uri="./data")


async def batch_example():
    requests = [
        "Arrow Lake 的存储格式是什么？",
        "版本控制如何工作？",
        "有哪些 OLAP 功能？",
    ]
    results = await lake.rag_batch_query(requests, "docs")
    for q, r in zip(requests, results):
        print(f"Q: {q}")
        print(f"A: {r.answer[:100]}...\n")


asyncio.run(batch_example())
```

***

## 9. 结构化提取

`Lake.rag_extract()` 使用提供的 schema 从自由文本中提取结构化数据，
复用 RAG 查询的 LLM 后端。

```python
import asyncio
from arrow_lake import Lake

lake = Lake(base_uri="./data")


async def extract_example():
    text = (
        "Arrow Lake v1.5.3 于 2025-01-15 发布。"
        "新增通过 HugeGraph 的知识图谱支持、"
        "基于 DuckDB 的 OLAP 分析，以及混合检索的 RAG 管线。"
    )
    schema = {
        "product_name": "str",
        "version": "str",
        "release_date": "str",
        "features": "list[str]",
    }
    response = await lake.rag_extract(text, schema, dataset_name="docs")
    print(response.answer)


asyncio.run(extract_example())
```

***

## 10. 反馈与会话管理

### 提交反馈

`Lake.rag_feedback()` 为会话中的特定轮次记录用户反馈，适用于收集评分以评估和改进 RAG 质量。

```python
lake.rag_feedback(
    session_id="user-123-session-abc",
    turn_id="turn-001",
    rating=5,              # 1-5 分
    comment="准确且简洁的回答",
)
```

### 查询反馈

```python
feedback_list = lake.rag_get_feedback("user-123-session-abc")
for fb in feedback_list:
    print(f"轮次 {fb['turn_id']}：评分={fb['rating']}，评论={fb.get('comment', '')}")
```

### 清理过期会话

`Lake.rag_cleanup_expired_sessions()` 清除超过 TTL 的会话，返回清理数量。

```python
removed = lake.rag_cleanup_expired_sessions()
print(f"已清理 {removed} 个过期会话")
```

***

## 11. 错误处理

```python
import asyncio
from arrow_lake import Lake, RAGError

lake = Lake(base_uri="./data")


async def safe_rag_query():
    try:
        response = await lake.rag_query("测试问题", "nonexistent_dataset")
    except RAGError as e:
        print(f"RAG 错误 [{e.error_code.name}]: {e.message}")
        print(f"上下文：{e.context}")
    except Exception as e:
        print(f"未知错误：{type(e).__name__}: {e}")


asyncio.run(safe_rag_query())
```

常见错误码：`RAG_PROVIDER_ERROR`（LLM 调用失败）、`RAG_CONTEXT_EMPTY`（检索为空）。

***

## 12. 重排序（Reranking）

第一阶段检索召回候选分块；**重排序器（Reranker）** 锐化顺序，让最相关的证据优先送达 LLM。
RAG 管线的重排序器由 `rag.*` 下的**扁平字段**配置（注意：是扁平字段，不是嵌套对象）：

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `rag.reranker` | `"ollama"` | 策略：`ollama` / `cross-encoder` / `llm` / `noop` |
| `rag.reranker_model` | `"dengcao/Qwen3-Reranker-0.6B:F16"` | ollama 时为模型 tag；cross-encoder 时为 HF 模型名 |
| `rag.reranker_base_url` | `""` | 远程端点（空 = 从 embedding api_base 推导） |
| `rag.reranker_top_n` | `10` | 重排后保留的块数 |
| `rag.reranker_device` | `"auto"` | cross-encoder 设备：auto / cpu / cuda（v1.9.6 P0-2） |
| `rag.reranker_warmup_on_init` | `True` | 启动预热，消除首次查询冷启动 |

> **默认是 `ollama`，不是 `cross-encoder`**（v1.9.5 修复死配置后确立）。`ollama` 用 Qwen3-Reranker
> 做是/否二值判断（区分度有限）；若要连续分精排，切 `reranker="cross-encoder"` +
> `reranker_model="BAAI/bge-reranker-v2-m3"`（从 `HF_HOME` 加载，气隙部署需预下载）。
> 注意 **search 端点**（非 RAG）的重排由 `hybrid.reranker_model` 配置（默认即 bge-reranker-v2-m3）。
> 若配置的重排器运行时无法加载，管线透明回退 `noop` 并告警，检索绝不因重排器配置错误而硬失败。

```yaml
# configs/rag.yaml —— 扁平字段（非嵌套对象）
rag:
  reranker: ollama                      # 或 cross-encoder / llm / noop
  reranker_model: dengcao/Qwen3-Reranker-0.6B:F16
  reranker_top_n: 10
  reranker_device: auto                 # cross-encoder 时生效
  reranker_warmup_on_init: true
```

***

## 13. 忠实度校验（防幻觉）

开启后，生成回答中的 `[n]` 引用编号会被校验是否落在检索上下文范围内，并逐句标注
`supported`（携带有效 `[n]` 引用）或 `unverified`（无引用）；响应携带 `verification` 块，
给出 `support_ratio` 与逐句明细，让调用方可以标记无依据论断，而非默默信任。

```yaml
rag:
  enable_verification: true      # opt-in；默认关闭
```

当前实现为 **lightweight 模式**（纯 stdlib，零额外成本）：校验 `[n]` 引用有效性 + 逐句标签。
embedding 余弦模式与 LLM judge 模式为规划中的扩展（见 `arrow_lake/rag/verifier.py` 末尾注释），尚未实现。

```python
response = await lake.rag_query("总结三季度发现", "reports")
print(response.answer)
v = response.verification          # dict 或 None（未开启时）
if v:
    print(f"支撑率：{v['support_ratio']}")     # 0.0 – 1.0
    print(f"有效引用：{v['valid_refs']}，无效引用：{v['invalid_refs']}")
    # v['sentences']：逐句明细 [{text, label: "supported"|"unverified", refs}]
```

对于流式响应，**末帧**携带 `verification` 块（连同 `citations` 与 `latency`），让流式 UI 可以在生成完成后展示支撑率。

***

## 14. GraphRAG（知识图谱增强）

当 `hugegraph.enabled=true` 且数据集已 `kg_build` 时，RAG 管线自动升级为 `GraphRAGPipeline`：
从问题抽实体 → 并行检索（向量 + 图三元组 + 邻居）→ RRF 融合 → 用 `graph_qa` 模板生成。
KG 不可用时优雅降级为纯向量 RAG（`graph_rag.py` 内置降级）。

- **三路并行**（v1.9.6 P0-4）：`_graphrag_retrieve` 用 `asyncio.gather` 并行跑 vector / search_ka / neighbor，
  延迟较串行降 40~50%；`QuestionEntityCache` 用 monotonic 时钟防 NTP 跳变致 TTL 批量失效。
- **per-query `use_kg`**：传 `use_kg=False` 单次绕过 KG（降级 `super().query()`），无需关 hugegraph。
- **延迟优化**：实体抽取/查询变体用 `extract_llm=qwen-turbo`（快），`qa_llm` 用 qwen-plus（生成）。

```python
# GraphRAG（hugegraph 已启用 + 数据集已建 KG）
r = await lake.rag_query("哪些系统依赖认证服务？", "docs")           # use_kg 默认 True
r2 = await lake.rag_query("同问题纯向量对比", "docs", use_kg=False)  # 单次绕过 KG
```

REST 专用端点 `POST /api/v1/kg/query/graphrag`（body 用 `question` + `dataset`）。
