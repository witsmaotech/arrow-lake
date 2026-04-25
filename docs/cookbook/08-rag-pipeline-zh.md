# RAG 问答管线

Arrow Lake 内置 RAG（检索增强生成）管线，支持多检索策略、流式输出、
多轮对话和知识图谱增强。返回 `RAGResponse`，包含回答、引用来源和性能指标。

> 前置准备：安装依赖 `pip install arrow-lake[rag]`，配置 LLM 提供商，
> 并确保目标数据集已嵌入向量索引。

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
| `llm_usage`       | `dict \| None`            | LLM Token 用量统计   |
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

默认策略由 `rag.default_retrieval_strategy` 配置控制：

```python
from arrow_lake.config import ArrowLakeConfig

config = ArrowLakeConfig()
config.rag.default_retrieval_strategy = "hybrid"  # "fts" | "vector" | "hybrid"
config.rag.default_top_k = 10                      # 默认检索文档数
```

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

通过 `template_name` 参数选择 Jinja2 提示词模板。

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

r1 = await lake.rag_query("安全机制有哪些？", "docs", template_name="default_qa")
r2 = await lake.rag_query("组件依赖关系？", "docs", template_name="graph_qa")
r3 = await lake.rag_extract("docs", template_name="entity_extract")
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

***

## 8. 错误处理

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
