# 知识图谱与 GraphRAG

> 版本：1.9.6

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
config.hugegraph.port = 8091            # 代码默认 8091（生产 compose 常重写为 8089）
config.hugegraph.graph_name = "hugegraph"  # 基础图名；实际图按数据集派生 kg_{dataset}（per-dataset 隔离）

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

# 获取出边方向的一阶邻居
neighbors_out = asyncio.run(
    lake.kg_get_neighbors(
        entity_id="arrow_lake:entity:42",
        direction="out",
        depth=1,
    )
)
print(f"出边邻居数：{len(neighbors_out)}")
for n in neighbors_out:
    print(f"  [{n.get('label')}] {n.get('name', n.get('id'))}")

# 获取双向二阶邻居，限制返回 200 条
neighbors_2 = asyncio.run(
    lake.kg_get_neighbors(
        entity_id="arrow_lake:entity:42",
        direction="both",
        depth=2,
        limit=200,
    )
)
print(f"二阶邻居数：{len(neighbors_2)}")
```

参数说明：

* `entity_id` — 起始顶点 ID 字符串
* `direction` — 边方向：`"out"`、`"in"` 或 `"both"`（默认：`"both"`）
* `depth` — 遍历跳数，默认 1，最大值受 `max_traversal_depth` 配置约束（默认 5）
* `limit` — 返回的最大邻居顶点数（默认：100）

底层调用 `HugeGraphClient.traverser_kneighbor()`，
使用 HugeGraph 的 `/graphs/{name}/traversers/kneighbor` 端点。

***

## 5. 最短路径遍历

Arrow Lake 提供多种最短路径算法，用于查找实体间的路径。

### 所有点最短路径

`Lake.kg_all_shortest_paths()` 查找两个顶点之间的所有最短路径：

```python
import asyncio

paths = asyncio.run(
    lake.kg_all_shortest_paths(
        source="arrow_lake:entity:1",
        target="arrow_lake:entity:42",
    )
)
for path in paths.get("paths", []):
    print(" -> ".join(str(v) for v in path))
```

### 加权最短路径

`Lake.kg_weighted_shortest_path()` 考虑边权重计算最短路径：

```python
result = asyncio.run(
    lake.kg_weighted_shortest_path(
        source="arrow_lake:entity:1",
        target="arrow_lake:entity:42",
    )
)
print(f"路径：{result.get('path')}")
print(f"权重：{result.get('weight')}")
```

### 单源最短路径

`Lake.kg_single_source_shortest_path()` 从一个源点计算到所有可达顶点的最短路径：

```python
result = asyncio.run(
    lake.kg_single_source_shortest_path(
        source="arrow_lake:entity:1",
    )
)
for target, info in result.get("paths", {}).items():
    print(f"  -> {target}：距离={info.get('distance')}")
```

***

## 6. 射线与环遍历

### 射线（Rays）

`Lake.kg_rays()` 从源顶点沿指定方向发射所有路径（射线）：

```python
import asyncio

rays = asyncio.run(
    lake.kg_rays(
        source="arrow_lake:entity:1",
        direction="out",
        max_depth=3,
    )
)
for ray in rays.get("rays", []):
    print(" -> ".join(str(v) for v in ray))
```

### 环（Rings）

`Lake.kg_rings()` 检测从源顶点出发的环形路径：

```python
rings = asyncio.run(
    lake.kg_rings(
        source="arrow_lake:entity:1",
        direction="out",
        max_depth=3,
    )
)
for ring in rings.get("rings", []):
    print("环路：" + " -> ".join(str(v) for v in ring))
```

### 交叉点（Crosspoints）

`Lake.kg_crosspoints()` 查找两个顶点路径的交汇点：

```python
crosspoints = asyncio.run(
    lake.kg_crosspoints(
        source="arrow_lake:entity:1",
        target="arrow_lake:entity:42",
    )
)
print(f"交叉点：{crosspoints.get('vertices', [])}")
```

***

## 7. 图分析

Arrow Lake 提供一套图分析算法，用于衡量中心性、检测社区和分析图结构。

### PageRank

```python
import asyncio

pr = asyncio.run(lake.kg_pagerank(iterations=20, damping=0.85))
for vertex, score in sorted(pr.get("scores", {}).items(), key=lambda x: -x[1])[:10]:
    print(f"  {vertex}: {score:.4f}")
```

### 社区检测（Louvain）

```python
communities = asyncio.run(lake.kg_louvain(resolution=1.0))
print(f"检测到社区数：{communities.get('community_count', 0)}")
```

### 中心性指标

```python
# 度中心性 — 每个顶点的直接连接数
degree = asyncio.run(lake.kg_degree_centrality())

# 接近中心性 — 到所有其他顶点的平均最短路径距离
closeness = asyncio.run(lake.kg_closeness_centrality())

# 中介中心性 — 顶点出现在最短路径上的频率
betweenness = asyncio.run(lake.kg_betweenness_centrality())
```

### 结构分析

```python
# 弱连通分量
wcc = asyncio.run(lake.kg_wcc())
print(f"连通分量数：{wcc.get('component_count', 0)}")

# 三角形计数
triangles = asyncio.run(lake.kg_triangle_count())
print(f"三角形数：{triangles.get('triangle_count', 0)}")

# K-Core 分解
kcore = asyncio.run(lake.kg_k_core(k=3))
print(f"3-Core 中的顶点数：{kcore.get('vertex_count', 0)}")
```

图分析 API 汇总：

| 方法                           | 说明               |
| ---------------------------- | ---------------- |
| `kg_pagerank(iterations, damping)` | PageRank 排名算法  |
| `kg_louvain(resolution)`     | Louvain 社区检测    |
| `kg_degree_centrality()`     | 度中心性            |
| `kg_closeness_centrality()`  | 接近中心性           |
| `kg_betweenness_centrality()`| 中介中心性           |
| `kg_wcc()`                   | 弱连通分量           |
| `kg_triangle_count()`        | 三角形计数           |
| `kg_k_core(k)`               | K-Core 分解        |

***

## 8. 导入与导出

### 导出图谱

`Lake.kg_export_graph()` 将所有顶点和边导出为字典，可选包含顶点/边属性：

```python
import asyncio

data = asyncio.run(lake.kg_export_graph(with_properties=True))
print(f"已导出 {len(data.get('vertices', []))} 个顶点，{len(data.get('edges', []))} 条边")

# 保存为 JSON 备份
import json
with open("graph_backup.json", "w") as f:
    json.dump(data, f, indent=2)
```

### 导入图谱

`Lake.kg_import_graph()` 从之前导出的字典恢复图谱：

```python
import json

with open("graph_backup.json") as f:
    data = json.load(f)

result = asyncio.run(lake.kg_import_graph(data))
print(f"导入结果：{result}")
```

***

## 9. GraphRAG 增强问答

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

## 10. 图谱清理

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

## 11. 完整工作流示例

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

## 12. 配置参考

`HugeGraphConfig` 关键配置项（v1.9.6）：

| 配置项                       | 类型      | 默认值               | 说明                    |
| ------------------------- | ------- | ----------------- | --------------------- |
| `enabled`                 | `bool`  | `False`           | 是否启用知识图谱功能            |
| `host` / `port`           | `str`/`int` | `localhost`/`8091` | HugeGraph REST 端点（生产常重写为 8089） |
| `graph_name`              | `str`   | `"hugegraph"`     | 基础图名；实际图按数据集派生 `kg_{dataset}`（per-dataset 隔离） |
| `backend`                 | `str`   | `"rocksdb"`       | 存储后端（rocksdb 单节点多图 / hstore PD 集群） |
| `build_concurrency` / `write_concurrency` | `int` | `3`/`2` | LLM 抽取并发 / HugeGraph 写入并发（写瓶颈，默认更低） |
| `extractor_backend`       | `str`   | `"he"`            | 抽取后端：`"he"`（hyper-extract，默认）/ `"legacy"` |
| `he_default_template`     | `str`   | `"entity_graph"`  | 默认抽取模板（通用实体+关系，strict 枚举） |
| `he_doc_type_templates`   | `dict`  | 见代码               | doc_type→模板映射（paper/report→entity_graph、medicine→medical_concept_graph、project→project_concept_graph 等） |
| `he_kg_granularity`       | `str`   | `"auto"`          | 抽取粒度：`auto`/`dataset`/`chunk`（dataset 走 MERGE_FIELD，稳定） |
| `he_strict_definition`     | `bool`  | `False`           | v1.9.6：丢弃空 definition 实体（降噪） |
| `he_extract_llm` / `he_qa_llm` | `LLMConfig\|None` | `None` | 两阶段独立 LLM（抽取/问答；None 回退全局 llm） |
| `he_ka_max_versions`      | `int`   | `5`               | 每数据集保留 KA 版本数（超出 prune，支持 rollback） |
| `he_ka_base_dir`          | `str`   | `"/data/ka"`      | KA dump 本地根（须本地路径，非 bucket） |
| `default_traversal_depth` / `max_traversal_depth` | `int` | `2`/`5` | 默认/最大遍历跳数 (1-10) |

配置约束：

* `max_traversal_depth` 取值范围为 1-10
* `build_batch_size` 必须大于等于 1
* `timeout_seconds` 必须大于等于 1.0

***

## v1.7–v1.9 KG 演进：抽取后端 + doc_type 路由 + per-dataset + 增量 + 质量/性能

v1.7.0 为 `kg_build` 增加了可插拔的抽取后端与按文档类型路由的模板选择，显著提升领域文档
（论文、合同、财报、病历等）的三元组抽取精度。

### 切换到 Hyper-Extract (`he`) 后端

通过 `HugeGraphConfig.extractor_backend` 切换到 hyper-extract 模板抽取：

```python
from arrow_lake.config import ArrowLakeConfig

config = ArrowLakeConfig()
config.hugegraph.enabled = True
config.hugegraph.extractor_backend = "he"            # "legacy" | "he"（默认 he，hyper-extract）
config.hugegraph.he_model = "qwen3:30b-a3b"          # 任意 OpenAI 兼容模型（或用 he_extract_llm/he_qa_llm 两阶段）
config.hugegraph.he_default_template = "entity_graph"  # 默认 entity_graph（通用实体+关系，strict 枚举+必填 definition）；concept_graph 留给 taxonomy 场景；project_concept_graph 等领域模板见 he_doc_type_templates。
```

需安装 `he` 扩展：`pip install "arrow-lake[he]"`。`he` 后端通过 langchain `ChatOpenAI`
驱动 hyper-extract 模板，三元组精度高于 legacy 通用抽取器。

### doc_type 三层路由

当 `extractor_backend="he"` 时，每个文档的 `doc_type` 通过三层路由（`doc_type_router.py`）
选择 hyper-extract 模板——首次命中即用：

1. **配置覆盖** — `HugeGraphConfig.he_doc_type_templates` 显式映射。
2. **TemplateGallery 元数据匹配** — 扫描每个 preset 的 `tags` / `category` / `name` /
   `description`；新增模板自动可用，无需改代码。
3. **默认兜底** — `HugeGraphConfig.he_default_template`。

```python
from arrow_lake.knowledge_graph.doc_type_router import (
    DocTypeRouter, TemplateGallery, normalize_doc_type, validate_taxonomy,
)

# 别名归一：论文 / research_paper / 白皮书 → 规范名 "paper"
print(normalize_doc_type("论文"))              # "paper"

# Gallery 按元数据索引所有 hyper-extract preset（自动发现新模板）
gallery = TemplateGallery.build()
hit = gallery.match("paper")                  # → 命中 preset（path/tags）或 None
print(hit.path if hit else "default")

# 三层路由：override > gallery > default
router = DocTypeRouter(
    doc_type_templates={"paper": "general/concept_graph"},   # 第 1 层：显式覆盖
    default_template="general/concept_graph",                # 第 3 层：兜底
)
path, source = router.resolve_with_source("论文")
print(path, source)                           # general/concept_graph 'override'
```

若完全未传 `doc_type`，`DocTypeClassifier` 会通过 LLM 从内容推断——**每文档仅一次**，
所有 chunk 共享匹配到的模板，节省 LLM 调用。

### 在摄入时传入 doc_type

`doc_type` 是摄入期属性，流向：上传 API → `Lake` facade → Ingestor → chunk 表 → KG builder：

```python
# Python SDK
lake.ingest_documents("papers", ["data/paper.pdf"], doc_type="paper")
```

> CLI `kg build` **没有** `--doc-type` 参数——请在摄入时设置 `doc_type`。

### A 方案实体双写

每个实体写入通用 `entity` 顶点**外加**细分 label（`person` / `organization` / `concept` / …）。
关系路由：端点类型有同义词时→细分边，否则降级为通用 `related_to` 边。原始类型保存在
`relation_type` 属性上，因此通用查询与类型专属查询都能工作：

```python
# 通用——所有实体
await lake.kg_query("g.V().hasLabel('entity').limit(10)")
# 类型专属——仅人物
await lake.kg_query("g.V().hasLabel('person').limit(10)")
```

### HugeGraph PD 集群模式

生产部署（`deploy/docker-compose.prod.yml`）以 PD 模式（`hg-pd` + `hg-store` + `hg-server`，
hstore 后端）替代 standalone rocksdb，支持**运行时创建多图**——每个文档可拥有独立隔离的 KG。
服务按 PD → Store → Server 顺序启动，由 healthcheck 保障。

### v1.8.8 per-dataset 动态图 + 增量 KA + 版本管理

- **per-dataset 隔离**：每个数据集一个独立图 `kg_{dataset}`（非单一全局图），rocksdb 后端真隔离。
- **`kg_build(incremental=True)`**：增量构建——仅喂新 chunk（`fed_chunks` sidecar），模板不匹配回退，KG 复用 `PRIMARY_KEY` 幂等 upsert。CLI `kg build --incremental`。REST `POST /api/v1/kg/build` body 加 `"incremental": true`。
- **KA 版本管理**：每次 `kg_build` 前归档 pre-build dump 到 `<base>/<ds>/ka/versions/v{ts}/`，`he_ka_max_versions`（默认 5）超出 prune 最旧。SDK：`lake.kg_list_ka_versions(ds)` / `kg_rollback_ka(ds, version)` / `kg_prune_ka_versions(ds)`；REST：`/api/v1/kg/ka-versions/{dataset}`、`/ka-rollback`、`/ka-prune`。
- **模板发现端点**：`GET /api/v1/kg/doc-types`、`/templates`、`/templates/{template_path}`（列规范 doc_type + 别名 + 模板，`is_high_risk` 标记 hypergraph）。

### v1.9.4 质量：MERGE_FIELD + 领域 strict 模板

- **MERGE_FIELD 合并**（替 BALANCED）：`dataset` 粒度下跨 chunk 字段合并改**非 LLM** 的 MERGE_FIELD（`he_extractor._create_ka`），消除 BALANCED grouped 的内存爆炸，任意规模稳定；`build_index` 已解耦，KG 入库可靠。grouped 分组档已移除。
- **领域 strict 模板**：`project_concept_graph`（22 类型 + 14 关系，项目方案书）、`medical_concept_graph`、`legal_concept_graph`、`finance_concept_graph`——tight 枚举 + 必填 definition，避免 `general/concept_graph` 的 0% 描述 + 80+ 自由类型噪声。

### v1.9.6 性能与降噪

- **snap 编辑距离归一化**：噪声类型（"架构组件"→"组件"）snap 到最近枚举值。
- **strict 过滤**：`he_strict_definition=true` 丢弃空 definition 实体（降噪）。
- **GraphRAG 三路并行**：`_graphrag_retrieve` 用 `asyncio.gather` 并行 vector / search_ka / neighbor，延迟 -40~50%；`QuestionEntityCache` monotonic 时钟防 TTL 批量失效；KA LRU 缓存按 dump mtime 失效。
- **traverser OOM 修复**：JVM 重复 `-Xmx` 末值生效曾致堆仅 2g → 遍历 OOM；修 `HG_SERVER_MEMORY_LIMIT=12288M` + `JAVA_OPTS -Xmx8g`（见 [12-部署](./12-deployment-zh.md)）。

> 另见：cookbook [示例 44](examples/44_kg_doctype_he.py)（路由，可离线运行）与
> [示例 45](examples/45_kg_doctype_api.py)（REST API 构建流程）。

***

## 13. 常见问题

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
