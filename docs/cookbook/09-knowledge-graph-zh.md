# 知识图谱与 GraphRAG

Arrow Lake 内置知识图谱 (KG) 子系统，通过 LLM 实体抽取将非结构化文本转化为结构化的实体 - 关系图，
并写入 HugeGraph 图数据库。当 `hugegraph.enabled=True` 时，RAG 管线自动升级为 GraphRAG，
在向量检索的基础上融合图谱邻居上下文，显著提升多跳推理问题的回答质量。

> 前置准备：安装依赖 `pip install arrow-lake[kg]`，部署 HugeGraph 服务，
> 并在配置中启用 `hugegraph.enabled = True`。

***

## 1. 构建知识图谱

`Lake.kg_build()` 读取指定数据集的文本块，调用 LLM 抽取实体与关系，
批量写入 HugeGraph。构建过程异步执行，返回 task\_id 用于跟踪进度。

```python
import asyncio
from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig

# 启用知识图谱
config = ArrowLakeConfig()
config.hugegraph.enabled = True
config.hugegraph.host = "localhost"
config.hugegraph.port = 8089
config.hugegraph.graph_name = "arrow_lake_kg"

lake = Lake(base_uri="./data", config=config)

# 触发异步构建 — 对 "docs" 数据集进行实体抽取
task_id = asyncio.run(lake.kg_build("docs"))
print(f"构建任务已提交：{task_id}")
```

### 1.1 轮询构建状态

```python
import asyncio

status = asyncio.run(lake.kg_build_status(task_id))
if status:
    print(f"状态：{status['status']}")
    print(f"数据集：{status['dataset_name']}")
    print(f"总块数：{status['total_chunks']}")
    print(f"已处理：{status['processed_chunks']}")
    print(f"实体数：{status['entity_count']}")
    print(f"关系数：{status['relation_count']}")
```

`kg_build_status()` 返回字典包含以下字段：

| 字段                 | 类型            | 说明                                             |
| ------------------ | ------------- | ---------------------------------------------- |
| `task_id`          | `str`         | 任务唯一标识                                         |
| `status`           | `str`         | `pending` / `running` / `completed` / `failed` |
| `dataset_name`     | `str`         | 源数据集名称                                         |
| `total_chunks`     | `int`         | 待处理文本块总数                                       |
| `processed_chunks` | `int`         | 已处理文本块数                                        |
| `entity_count`     | `int`         | 已提取实体数                                         |
| `relation_count`   | `int`         | 已提取关系数                                         |
| `error`            | `str \| None` | 错误信息 (仅 failed 状态)                             |

***

## 2. 图谱统计

`Lake.kg_stats()` 返回图谱的顶点和边计数，用于快速了解图谱规模。

```python
import asyncio

stats = asyncio.run(lake.kg_stats())
print(f"顶点数：{stats.get('vertex_count', 0)}")
print(f"边数：{stats.get('edge_count', 0)}")
```

底层调用 `HugeGraphClient.get_stats()`，通过 HugeGraph REST API 的
`/graphs/{graph_name}/stats` 端点获取统计信息。

***

## 3. Gremlin 查询

`Lake.kg_query()` 执行原生 Gremlin 查询语句，直接查询 HugeGraph 图数据库。
适合需要灵活查询模式的场景。

```python
import asyncio

# 查询所有实体标签
labels = asyncio.run(
    lake.kg_query("g.V().label().dedup()")
)
print(f"实体标签：{labels}")

# 查询前 10 个实体顶点
entities = asyncio.run(
    lake.kg_query("g.V().hasLabel('entity').limit(10)")
)
for entity in entities:
    print(f"  {entity.get('id')}: {entity.get('name', '')}")

# 查询特定名称的实体
results = asyncio.run(
    lake.kg_query("g.V().has('entity', 'name', 'Arrow Lake').valueMap()")
)
print(results)
```

> 注意：Gremlin 查询中的写操作（如 `addV()`、`addE()`）会被 REST API 端点
> 拦截以防止误操作，仅在 Python SDK 直接调用 `kg_query()` 时执行。

***

## 4. 邻居遍历

`Lake.kg_get_neighbors()` 从指定实体出发，按深度进行 K 邻居遍历，
返回可达的所有邻居顶点。这是 GraphRAG 核心能力的基础。

```python
import asyncio

# 获取实体的一阶邻居
neighbors_1 = asyncio.run(
    lake.kg_get_neighbors(entity_id="arrow_lake:entity:42", depth=1)
)
print(f"一阶邻居数：{len(neighbors_1)}")
for n in neighbors_1:
    print(f"  [{n.get('label')}] {n.get('name', n.get('id'))}")

# 获取二阶邻居 — 发现更远距离的关联实体
neighbors_2 = asyncio.run(
    lake.kg_get_neighbors(entity_id="arrow_lake:entity:42", depth=2)
)
print(f"二阶邻居数：{len(neighbors_2)}")
```

参数说明：

* `entity_id` — 起始顶点 ID 字符串
* `depth` — 遍历跳数，默认 1，最大值受 `max_traversal_depth` 配置约束（默认 5）

底层调用 `HugeGraphClient.traverser_kneighbor()`，
使用 HugeGraph 的 `/graphs/{name}/traversers/kneighbor` 端点。

***

## 5. GraphRAG 增强问答

当 `hugegraph.enabled=True` 时，`Lake.rag_query()` 自动创建 `GraphRAGPipeline`
替代基础 `RAGPipeline`。GraphRAG 管线在回答问题前，先从用户问题中抽取关键实体，
然后在知识图谱中进行邻居遍历获取结构化上下文，与向量检索结果一起注入 LLM 提示。

```python
import asyncio
from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig

# 配置：启用知识图谱
config = ArrowLakeConfig()
config.hugegraph.enabled = True
config.hugegraph.host = "localhost"
config.hugegraph.port = 8089
config.llm.provider = "openai"

lake = Lake(base_uri="./data", config=config)

# GraphRAG 自动启用 — 无需额外代码
response = asyncio.run(
    lake.rag_query(
        question="Arrow Lake 的知识图谱和向量检索是如何协同工作的？",
        dataset_name="docs",
        top_k=5,
    )
)

print(response.answer)
print(f"引用文档数：{response.retrieval_count}")
for citation in response.citations:
    print(f"  - {citation.document_name} (score={citation.score:.3f})")
```

GraphRAG 管线的工作流程：

1. **实体抽取** — 从用户问题中提取关键实体名称
2. **图谱检索** — 对每个实体执行邻居遍历，获取关联实体和关系
3. **向量检索** — 按常规策略（vector/fts/hybrid）检索相关文档
4. **上下文融合** — 将图谱上下文和文档片段合并为增强提示
5. **LLM 生成** — 基于融合上下文生成最终回答

如果 HugeGraph 不可用或连接失败，管线自动降级为标准 RAG 模式，不会中断服务。

***

## 6. 图谱清理

`Lake.kg_delete_graph()` 清空图谱中的所有顶点和边（包括 schema）。
此操作不可逆，请谨慎使用。

```python
import asyncio

# 确认后执行清理
asyncio.run(lake.kg_delete_graph())
print("图谱已清空")

# 验证清理结果
stats = asyncio.run(lake.kg_stats())
print(f"顶点数：{stats.get('vertex_count', 0)}")
print(f"边数：{stats.get('edge_count', 0)}")
```

底层调用 `HugeGraphClient.clear()`，依次执行：

1. 清除所有顶点数据
2. 清除所有边数据
3. 清除 schema（顶点标签和边标签定义）

***

## 7. 完整工作流示例

以下是一个从数据摄取到 GraphRAG 问答的完整端到端流程：

```python
import asyncio
from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig

async def main():
    # 1. 配置
    config = ArrowLakeConfig()
    config.hugegraph.enabled = True
    config.hugegraph.host = "localhost"
    config.hugegraph.port = 8089
    config.hugegraph.build_batch_size = 100
    config.hugegraph.default_traversal_depth = 2
    config.llm.provider = "openai"

    lake = Lake(base_uri="./data", config=config)

    # 2. 摄取文档
    report = lake.ingest("my_docs", ["technical_guide.md"])
    print(f"摄取：{report.total_files} 个文件，{report.total_rows} 行")

    # 3. 构建知识图谱
    task_id = await lake.kg_build("my_docs")
    print(f"构建任务：{task_id}")

    # 4. 等待构建完成
    status = await lake.kg_build_status(task_id)
    while status and status["status"] not in ("completed", "failed"):
        await asyncio.sleep(2)
        status = await lake.kg_build_status(task_id)
        if status:
            print(f"  进度：{status['processed_chunks']}/{status['total_chunks']}")

    # 5. 查看统计
    stats = await lake.kg_stats()
    print(f"图谱：{stats.get('vertex_count', 0)} 个顶点，{stats.get('edge_count', 0)} 条边")

    # 6. GraphRAG 问答
    response = await lake.rag_query(
        question="系统的核心组件有哪些？它们之间是什么关系？",
        dataset_name="my_docs",
    )
    print(f"回答：{response.answer[:200]}...")

    # 7. 清理
    await lake.kg_delete_graph()
    lake.shutdown()

asyncio.run(main())
```

***

## 8. 配置参考

`HugeGraphConfig` 完整配置项：

| 配置项                       | 类型      | 默认值               | 说明                    |
| ------------------------- | ------- | ----------------- | --------------------- |
| `enabled`                 | `bool`  | `False`           | 是否启用知识图谱功能            |
| `host`                    | `str`   | `"localhost"`     | HugeGraph 服务器地址       |
| `port`                    | `int`   | `8089`            | HugeGraph REST API 端口 |
| `graph_name`              | `str`   | `"arrow_lake_kg"` | 图数据库在 HugeGraph 中的名称  |
| `timeout_seconds`         | `float` | `30.0`            | HTTP 请求超时（秒）          |
| `username`                | `str`   | `""`              | 认证用户名（空则不认证）          |
| `password`                | `str`   | `""`              | 认证密码                  |
| `auto_build_on_ingest`    | `bool`  | `False`           | 摄取时自动构建图谱             |
| `build_batch_size`        | `int`   | `50`              | 批量插入顶点/边的数量           |
| `default_traversal_depth` | `int`   | `2`               | 默认图遍历跳数               |
| `max_traversal_depth`     | `int`   | `5`               | 最大允许遍历跳数 (1-10)       |

配置约束：

* `max_traversal_depth` 取值范围为 1-10
* `build_batch_size` 必须大于等于 1
* `timeout_seconds` 必须大于等于 1.0

***

## 9. 常见问题

**Q: 构建任务卡在 pending 状态怎么办？**

检查 HugeGraph 服务是否可达：`lake.kg_stats()` 如果抛出连接错误，
说明 HugeGraph 未启动或网络配置有误。

**Q: 实体抽取质量如何优化？**

配置更强的 LLM 模型（如 GPT-4、Claude），或在 prompt 中指定领域关键词。
`EntityExtractor` 支持通过 `llm` 配置项切换底层模型。

**Q: GraphRAG 对性能有多大影响？**

GraphRAG 额外的图谱遍历通常增加 50-200ms 延迟，但能显著提升多跳推理
和实体关联类问题的回答质量。如果对延迟敏感，可设置 `hugegraph.enabled=False`
降级为标准 RAG。
