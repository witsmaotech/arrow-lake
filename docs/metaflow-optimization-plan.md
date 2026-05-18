# Arrow Lake Metaflow 高级特性落地方案

> 版本: v1.2 | 日期: 2026-05-18 | 状态: **Phase 1-4 已完成**，Phase 5 待部署环境

---

## 1. 现状审计

### 1.1 已有能力

| 模块 | 文件 | 行数 | 能力 |
|------|------|------|------|
| Quality Pipeline Flow | `flows/quality_pipeline_flow.py` | ~60 | start → apply_filters → end |
| Maya E2E Flow | `flows/maya_e2e_flow.py` | ~270 | start → ingest → quality_filter → embed → search → end |
| Scheduled Quality Flow | `flows/scheduled_quality_flow.py` | ~95 | start → check_quality → end (cron 08:00) |
| Base Mixin | `arrow_lake/workflow/base.py` | ~119 | ArrowLakeFlowSpec + FlowRegistry |
| Retry | `arrow_lake/workflow/retry.py` | ~87 | build_metaflow_retry + tenacity backoff |
| Error Handler | `arrow_lake/workflow/error_handler.py` | ~131 | classify_error + catch_handler |
| Rollback | `arrow_lake/workflow/rollback.py` | ~190 | StateRollback + CheckpointInfo |
| Schedule | `arrow_lake/workflow/schedule.py` | — | ScheduleConfig + build_schedule |
| Tags | `arrow_lake/workflow/tags.py` | — | RunTags + find_failed_runs |
| Audit | `arrow_lake/workflow/audit.py` | — | AuditTrail + HMAC integrity |
| Argo Bridge | `arrow_lake/workflow/argo.py` | ~331 | ArgoWorkflowBridge (generate/validate/deploy) |
| Workflow Config | `arrow_lake/config/workflow.py` | ~120 | WorkflowConfig + ArgoConfig + AutoscaleConfig |

### 1.2 核心问题

**3 个 Flow 全部是线性管道**（start → step → end），没有任何：
- `@foreach` — 并行处理
- `@resources` — GPU/CPU 资源声明
- `@retry` / `@catch` / `@timeout` — 步骤级容错（基础设施已有 `build_metaflow_retry` 和 `catch_handler`，但 Flow 层完全没用）
- `branch` / `join` — 条件分支与并行合并
- `@card` — 可视化报告
- `@checkpoint` — 断点续跑
- `Client API` — 运行结果追溯

**基础设施已完备但没接入 Flow 层** — retry、error_handler、rollback 都只作为独立模块存在，没有任何 Flow 的 `@step` 使用它们。

### 1.3 业务模块现状

| 业务模块 | 文件 | 关键方法 | 当前模式 |
|----------|------|----------|----------|
| Ingestor | `arrow_lake/ingest/ingestor.py` | `ingest_batch()` | 串行处理，无断点 |
| ApiEmbeddingEncoder | `arrow_lake/embed/encoder.py` | `encode()` | tenacity 重试，CircuitBreaker，API→Local 降级 |
| LocalEmbeddingEncoder | `arrow_lake/embed/encoder.py` | `encode_column()` | GPU 自动检测，CPU 降级 |
| NeMoCuratorFilter | `arrow_lake/quality/nemo_curator.py` | `filter()` | GPU 占用无管理，CPU 降级 |
| NeMoDeduplicator | `arrow_lake/quality/nemo_curator.py` | `deduplicate()` | 串行去重 |
| KGBuilder | `arrow_lake/knowledge_graph/builder.py` | `build()` | async + semaphore 并发，任务状态追踪 |
| RAGPipeline | `arrow_lake/rag/pipeline.py` | `batch_query()` | asyncio.Semaphore(5) 硬编码 |
| VectorSearchBridge | `arrow_lake/query/vector.py` | `create_index()` | 单线程，无进度追踪 |
| ExportBridge | `arrow_lake/query/export.py` | `export()` | 大文件无断点 |

**共同特征**：纯函数式设计，输入/输出清晰（适合 Metaflow step），大多数操作幂等可安全重试。

---

## 2. 优化映射表

### 2.1 业务场景 → Metaflow 特性

| # | 业务场景 | 当前问题 | Metaflow 特性 | 收益 |
|---|----------|----------|---------------|------|
| 1 | 多文件批量 Ingest | `ingest_batch()` 串行，100 文件逐个处理 | `@foreach` + `@retry` + `@catch` | 并行化 + 容错 + 死信队列 |
| 2 | GPU 质量过滤 | NeMoCuratorFilter 直接占 GPU，OOM 全丢 | `@resources(gpu=1)` + `@retry` | GPU 隔离 + OOM 自动重试 |
| 3 | 批量 Embedding | API 限流手动处理，无进度 | `@foreach` 分片 + `@retry` + `@card` | 分片并行 + 进度可视 |
| 4 | KG 多源构建 | 实体提取 + schema 构建串行 | `branch` + `join` | 并行构建，缩短 2x |
| 5 | 批量 RAG 查询 | asyncio.Semaphore(5) 硬编码 | `@foreach` + `@timeout` + `@retry` | 动态并行 + 超时保护 |
| 6 | 向量索引构建 | 单线程耗时长 | `@resources(memory=32000)` + `@timeout` | 资源保证 + 超时保护 |
| 7 | 数据导出/备份 | 大文件失败从头 | `@checkpoint` + `@retry` | 断点续跑 |
| 8 | 混合搜索 | ThreadPoolExecutor(2) | `branch` 并行 + RRF join | 向量+FTS 真正并行 |
| 9 | 质量报告 | 结果只在日志 | `@card(type="html")` | 可视化 HTML 报告 |
| 10 | 运行追溯 | 无历史对比能力 | `Client API` (Flow/Run) | A/B 测试 + 结果对比 |

### 2.2 Metaflow 特性使用矩阵

```
                foreach  retry  catch  timeout  resources  branch  card  checkpoint  Client API
IngestFlow        ●       ●      ●                                        ●
EmbedFlow         ●       ●             ●        ●                   ●
QualityFlow                       ●               ●                           ●
KGFlow            ●       ●      ●                ●          ●
BatchRAGFlow      ●       ●      ●      ●                                          ●
ExportFlow                        ●      ●                                  ●       ●
```

---

## 3. 分阶段落地方案

### Phase 1: IngestFlow — 并行 Ingest + 死信队列 (2 天)

**目标**：将 `Ingestor.ingest_batch()` 的串行处理改为 Metaflow foreach 并行。

**涉及文件**：
- 新增: `flows/ingest_flow.py`
- 修改: `arrow_lake/workflow/__init__.py` (注册新 Flow)
- 新增: `tests/unit/flows/test_ingest_flow.py`

**Flow 结构**：

```python
# flows/ingest_flow.py
from __future__ import annotations

from arrow_lake.workflow.base import ArrowLakeFlowSpec
from metaflow import FlowSpec, Parameter, catch, project, retry, step


@project(name="arrow_lake")
class IngestFlow(ArrowLakeFlowSpec, FlowSpec):
    """并行 Ingest Flow: foreach 分文件处理 + 死信队列."""

    base_uri: str = Parameter("base-uri", default="./data/lake")
    config_path: str = Parameter("config-path", default="")
    dataset_name: str = Parameter("dataset-name", default="documents")
    source_path: str = Parameter("source-path", default="./data/input")
    chunk_size: int = Parameter("chunk-size", default=10)

    @step
    def start(self) -> None:
        """扫描源目录，生成文件列表."""
        import os
        from pathlib import Path

        self.config = self._load_config()
        self._auto_tag()

        source = Path(self.source_path)
        if not source.exists():
            self.files: list[str] = []
        else:
            self.files = sorted(
                str(f) for f in source.rglob("*") if f.is_file()
            )
        self.next(self.ingest, foreach="files")

    @retry(times=3, minutes_between_retries=1)
    @catch(var="ingest_error")
    @step
    def ingest(self) -> None:
        """处理单个文件，失败进入死信."""
        import structlog
        from arrow_lake.ingest.ingestor import Ingestor
        from arrow_lake.ingest.storage import LanceStorageManager

        logger = structlog.get_logger(__name__)

        if hasattr(self, "ingest_error"):
            self.result = {"file": self.input, "status": "skipped", "error": str(self.ingest_error)}
            logger.warning("ingest_file_skipped", file=self.input, error=str(self.ingest_error))
        else:
            storage = LanceStorageManager(base_uri=self.base_uri)
            ingestor = Ingestor(storage)
            report = ingestor.ingest(self.dataset_name, [self.input])
            self.result = {
                "file": self.input,
                "status": "success",
                "rows_ingested": getattr(report, "rows_ingested", 0),
            }
            logger.info("ingest_file_complete", file=self.input, rows=self.result["rows_ingested"])
        self.next(self.join)

    @step
    def join(self, inputs: list) -> None:
        """汇总所有并行结果，分离成功/失败."""
        self.successes = [i.result for i in inputs if i.result["status"] == "success"]
        self.failures = [i.result for i in inputs if i.result["status"] != "success"]
        self.total_success = sum(r["rows_ingested"] for r in self.successes)
        self.total_failure = len(self.failures)
        self.next(self.end)

    @step
    def end(self) -> None:
        """输出汇总报告."""
        import json
        print(json.dumps({
            "total_files": len(self.successes) + len(self.failures),
            "success": len(self.successes),
            "failed": len(self.failures),
            "total_rows_ingested": self.total_success,
            "dead_letter": self.failures,
        }, indent=2))
```

**验证标准**：
- [ ] `python flows/ingest_flow.py run` 单文件测试通过
- [ ] `python flows/ingest_flow.py run --source-path ./test_data` 多文件测试通过
- [ ] 故意放入一个损坏文件，验证 catch 死信机制
- [ ] `python flows/ingest_flow.py resume` 断点续跑测试
- [ ] 测试覆盖率 ≥ 80%

**收益量化**：100 文件从 ~100T 串行 → ~10T 并行（chunk_size=10），失败记录不丢失。

---

### Phase 2: EmbedFlow — GPU 资源管理 + 分片并行 (2 天)

**目标**：将 `ApiEmbeddingEncoder` / `LocalEmbeddingEncoder` 的批量编码改为 Metaflow foreach 分片 + 资源管理。

**涉及文件**：
- 新增: `flows/embed_flow.py`
- 新增: `tests/unit/flows/test_embed_flow.py`

**Flow 结构**：

```python
# flows/embed_flow.py
from __future__ import annotations

from arrow_lake.workflow.base import ArrowLakeFlowSpec
from metaflow import FlowSpec, Parameter, catch, project, resources, retry, step, card


@project(name="arrow_lake")
class EmbedFlow(ArrowLakeFlowSpec, FlowSpec):
    """并行 Embedding Flow: 分片编码 + GPU 资源管理."""

    base_uri: str = Parameter("base-uri", default="./data/lake")
    config_path: str = Parameter("config-path", default="")
    dataset_name: str = Parameter("dataset-name", default="documents")
    encoder_type: str = Parameter("encoder-type", default="api")  # api | local
    shard_size: int = Parameter("shard-size", default=500)
    vector_column: str = Parameter("vector-column", default="vector")

    @step
    def start(self) -> None:
        """加载数据并分片."""
        from arrow_lake.ingest.storage import LanceStorageManager

        self.config = self._load_config()
        self._auto_tag()

        storage = LanceStorageManager(base_uri=self.base_uri)
        table = storage.read_table(self.dataset_name)
        self.total_rows = table.num_rows

        # 分片：每个 shard 记录 (offset, length) 元组
        self.shards: list[tuple[int, int]] = []
        for offset in range(0, self.total_rows, self.shard_size):
            length = min(self.shard_size, self.total_rows - offset)
            self.shards.append((offset, length))

        self.next(self.encode, foreach="shards")

    @resources(gpu=1, memory=16000)  # 声明资源需求（Argo/K8s 环境生效）
    @retry(times=2, minutes_between_retries=2)
    @catch(var="encode_error")
    @step
    def encode(self) -> None:
        """编码单个分片."""
        import pyarrow as pa
        import structlog
        from arrow_lake.ingest.storage import LanceStorageManager
        from arrow_lake.embed.encoder import (
            ApiEmbeddingEncoder,
            LocalEmbeddingEncoder,
        )

        logger = structlog.get_logger(__name__)
        offset, length = self.input

        if hasattr(self, "encode_error"):
            self.result = {"shard": self.input, "status": "failed", "error": str(self.encode_error)}
            return self._finish_shard()

        storage = LanceStorageManager(base_uri=self.base_uri)
        table = storage.read_table(self.dataset_name)
        shard_table = table.slice(offset, length)

        if self.encoder_type == "local":
            encoder = LocalEmbeddingEncoder()
            result = encoder.encode_column(shard_table, column="text_content")
            embedded = shard_table.append_column(self.vector_column, result.vectors)
        else:
            texts = shard_table.column("text_content").to_pylist()
            encoder = ApiEmbeddingEncoder()
            batch = encoder.encode(texts)
            vec_col = pa.FixedSizeListArray.from_arrays(
                batch.vectors.ravel(), batch.dimension
            )
            embedded = shard_table.append_column(self.vector_column, vec_col)

        self.result = {
            "shard": self.input,
            "status": "success",
            "rows": length,
        }
        self._embedded_table = embedded
        logger.info("embed_shard_complete", offset=offset, rows=length)
        self.next(self.join)

    @step
    def join(self, inputs: list) -> None:
        """合并所有分片结果."""
        import pyarrow as pa
        from arrow_lake.ingest.storage import LanceStorageManager

        tables = []
        for inp in inputs:
            if hasattr(inp, "_embedded_table"):
                tables.append(inp._embedded_table)

        if tables:
            merged = pa.concat_tables(tables)
            storage = LanceStorageManager(base_uri=self.base_uri)
            storage.create_dataset(self.dataset_name, merged, mode="overwrite")

        self.total_embedded = sum(
            1 for i in inputs if i.result["status"] == "success"
        )
        self.total_failed = sum(
            1 for i in inputs if i.result["status"] == "failed"
        )
        self.next(self.report)

    @card(type="html")
    @step
    def report(self) -> None:
        """生成 HTML 可视化报告."""
        self.report_html = f"""
        <h2>Embedding Report</h2>
        <table>
            <tr><td>Total Rows</td><td>{self.total_rows}</td></tr>
            <tr><td>Embedded</td><td>{self.total_embedded}</td></tr>
            <tr><td>Failed</td><td>{self.total_failed}</td></tr>
            <tr><td>Shard Size</td><td>{self.shard_size}</td></tr>
            <tr><td>Encoder</td><td>{self.encoder_type}</td></tr>
        </table>
        """
        self.next(self.end)

    @step
    def end(self) -> None:
        """完成."""
        import structlog
        logger = structlog.get_logger(__name__)
        logger.info("embed_flow_complete", embedded=self.total_embedded, failed=self.total_failed)
```

**验证标准**：
- [ ] `python flows/embed_flow.py run` 本地 CPU 模式测试
- [ ] `python flows/embed_flow.py run --encoder-type api` API 模式测试
- [ ] GPU 环境下 `@resources(gpu=1)` 生效验证
- [ ] 故意注入 API 错误，验证 catch + retry 机制
- [ ] `@card` 报告生成验证（`python flows/embed_flow.py card view`）

**收益量化**：10 万行数据分 200 个 shard × 500 行，并行编码，GPU OOM 自动重试。

---

### Phase 3: KGFlow — 多源并行构建 + 实体提取 foreach (3 天)

**目标**：将 `KGBuilder.build()` 的串行过程改为 branch 并行 + foreach 实体提取。

**涉及文件**：
- 新增: `flows/kg_flow.py`
- 新增: `tests/unit/flows/test_kg_flow.py`

**Flow 结构**：

```python
# flows/kg_flow.py
from __future__ import annotations

from arrow_lake.workflow.base import ArrowLakeFlowSpec
from metaflow import FlowSpec, Parameter, catch, project, resources, retry, step


@project(name="arrow_lake")
class KGFlow(ArrowLakeFlowSpec, FlowSpec):
    """知识图谱构建 Flow: 并行 schema 构建 + foreach 实体提取."""

    base_uri: str = Parameter("base-uri", default="./data/lake")
    config_path: str = Parameter("config-path", default="")
    dataset_name: str = Parameter("dataset-name", default="documents")
    chunk_size: int = Parameter("chunk-size", default=20)

    @step
    def start(self) -> None:
        """加载数据，准备 chunk 列表."""
        from arrow_lake.ingest.storage import LanceStorageManager
        import pyarrow as pa

        self.config = self._load_config()
        self._auto_tag()

        storage = LanceStorageManager(base_uri=self.base_uri)
        table = storage.read_table(self.dataset_name)
        self.total_chunks = table.num_rows

        # 生成分片索引
        self.chunk_indices: list[tuple[int, int]] = []
        for offset in range(0, self.total_chunks, self.chunk_size):
            length = min(self.chunk_size, self.total_chunks - offset)
            self.chunk_indices.append((offset, length))

        # 两个分支并行: 提取实体 + 确保 schema
        self.next(self.extract_entities, self.ensure_schema)

    @retry(times=3, minutes_between_retries=1)
    @catch(var="extract_error")
    @step
    def extract_entities(self) -> None:
        """从文本中提取实体和关系 (foreach 并行)."""
        # 注意: 此 step 的 foreach 将在 start 的 chunk_indices 上展开
        # 实际实现中需要在 start 中使用 self.next(self.extract_entities, foreach='chunk_indices')
        # 此处为简化展示，实际需拆分为 foreach step
        import structlog

        logger = structlog.get_logger(__name__)
        # 实体提取逻辑...
        self.entities = []  # type: ignore[assignment]
        self.next(self.join_kg)

    @resources(memory=8000)
    @step
    def ensure_schema(self) -> None:
        """确保 KG schema 就绪 (与实体提取并行)."""
        import structlog
        from arrow_lake.knowledge_graph.builder import KGBuilder

        logger = structlog.get_logger(__name__)
        builder = KGBuilder()
        self.schema_ready = True
        self.next(self.join_kg)

    @step
    def join_kg(self, inputs: list) -> None:
        """合并实体提取和 schema 构建结果."""
        # 从两个分支收集结果
        self.next(self.insert_vertices)

    @resources(memory=16000)
    @retry(times=2)
    @step
    def insert_vertices(self) -> None:
        """批量插入顶点和边."""
        import structlog

        logger = structlog.get_logger(__name__)
        # 批量插入逻辑...
        self.vertex_count = 0
        self.edge_count = 0
        self.next(self.end)

    @step
    def end(self) -> None:
        """输出 KG 构建报告."""
        import json
        print(json.dumps({
            "total_chunks": self.total_chunks,
            "vertices": self.vertex_count,
            "edges": self.edge_count,
        }, indent=2))
```

**实际落地的完整 foreach 分支方案**：

```
start
  ├─→ extract_entities_start
  │     └─→ extract_one (foreach=chunk_indices)
  │           └─→ extract_join
  │                 └─→ join_kg
  └─→ ensure_schema ──→ join_kg
                          └─→ insert_vertices
                                └─→ end
```

**验证标准**：
- [ ] branch 并行验证：两个分支同时开始执行
- [ ] foreach 实体提取：每个 chunk 独立处理
- [ ] schema 构建失败时 join 优雅处理
- [ ] StateRollback 集成：步骤前 checkpoint，失败时 rollback

---

### Phase 4: BatchRAGFlow — 并行查询 + Client API 追溯 (2 天)

**目标**：将 `RAGPipeline.batch_query()` 的 asyncio.Semaphore(5) 改为 Metaflow foreach + 超时保护 + 运行追溯。

**涉及文件**：
- 新增: `flows/batch_rag_flow.py`
- 新增: `tests/unit/flows/test_batch_rag_flow.py`
- 新增: `arrow_lake/workflow/run_tracker.py` (Client API 封装)

**Flow 结构**：

```python
# flows/batch_rag_flow.py
from __future__ import annotations

from arrow_lake.workflow.base import ArrowLakeFlowSpec
from metaflow import FlowSpec, Parameter, catch, project, retry, step, timeout


@project(name="arrow_lake")
class BatchRAGFlow(ArrowLakeFlowSpec, FlowSpec):
    """批量 RAG 查询 Flow: foreach 并行 + 超时保护."""

    base_uri: str = Parameter("base-uri", default="./data/lake")
    config_path: str = Parameter("config-path", default="")
    dataset_name: str = Parameter("dataset-name", default="documents")
    questions_file: str = Parameter("questions-file", default="./questions.json")
    top_k: int = Parameter("top-k", default=5)
    concurrency: int = Parameter("concurrency", default=10)

    @step
    def start(self) -> None:
        """加载问题列表."""
        import json
        from pathlib import Path

        self.config = self._load_config()
        self._auto_tag()

        qfile = Path(self.questions_file)
        if qfile.exists():
            self.questions = json.loads(qfile.read_text())
        else:
            self.questions = ["demo question"]

        self.next(self.query, foreach="questions")

    @retry(times=3, minutes_between_retries=1)
    @timeout(seconds=60)
    @catch(var="query_error")
    @step
    def query(self) -> None:
        """查询单个问题."""
        import structlog

        logger = structlog.get_logger(__name__)

        if hasattr(self, "query_error"):
            self.result = {"question": self.input, "status": "timeout_or_error", "error": str(self.query_error)}
            logger.warning("rag_query_failed", question=self.input[:50])
        else:
            # 同步调用 RAG pipeline
            from arrow_lake.rag.pipeline import RAGPipeline
            pipeline = RAGPipeline()
            response = pipeline.query(self.input, self.dataset_name, top_k=self.top_k)
            self.result = {
                "question": self.input,
                "status": "success",
                "answer": response.answer,
                "sources": len(response.sources),
            }
            logger.info("rag_query_complete", question=self.input[:50])
        self.next(self.join)

    @step
    def join(self, inputs: list) -> None:
        """汇总查询结果."""
        self.results = [i.result for i in inputs]
        self.total_success = sum(1 for r in self.results if r["status"] == "success")
        self.total_failed = sum(1 for r in self.results if r["status"] != "success")
        self.next(self.end)

    @step
    def end(self) -> None:
        """输出汇总."""
        import json
        print(json.dumps({
            "total_questions": len(self.results),
            "success": self.total_success,
            "failed": self.total_failed,
        }, indent=2))
```

**Run Tracker（Client API 封装）**：

```python
# arrow_lake/workflow/run_tracker.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RunComparison:
    """两次运行的结果对比."""
    run_a_id: str
    run_b_id: str
    metrics_a: dict[str, Any]
    metrics_b: dict[str, Any]
    diff: dict[str, float]


class RunTracker:
    """封装 Metaflow Client API，提供运行追溯和 A/B 测试."""

    @staticmethod
    def latest_run(flow_name: str) -> dict[str, Any] | None:
        """获取指定 Flow 的最近一次成功运行."""
        from metaflow import Flow

        for run in Flow(flow_name):
            if run.successful:
                return {
                    "run_id": run.id,
                    "created_at": str(run.created_at),
                    "tags": list(run.tags),
                }
        return None

    @staticmethod
    def compare_runs(flow_name: str, run_a_id: str, run_b_id: str) -> RunComparison:
        """对比两次运行的指标."""
        from metaflow import Run

        run_a = Run(f"{flow_name}/{run_a_id}")
        run_b = Run(f"{flow_name}/{run_b_id}")

        def extract_metrics(run: Any) -> dict[str, Any]:
            return {
                "total_rows": getattr(run.data, "total_rows", 0),
                "success_count": getattr(run.data, "total_success", 0),
                "failure_count": getattr(run.data, "total_failure", 0),
            }

        metrics_a = extract_metrics(run_a)
        metrics_b = extract_metrics(run_b)

        diff = {
            k: metrics_b.get(k, 0) - metrics_a.get(k, 0)
            for k in set(metrics_a) | set(metrics_b)
        }

        return RunComparison(
            run_a_id=run_a_id,
            run_b_id=run_b_id,
            metrics_a=metrics_a,
            metrics_b=metrics_b,
            diff=diff,
        )

    @staticmethod
    def run_history(flow_name: str, limit: int = 10) -> list[dict[str, Any]]:
        """获取指定 Flow 的运行历史."""
        from metaflow import Flow

        history = []
        for i, run in enumerate(Flow(flow_name)):
            if i >= limit:
                break
            history.append({
                "run_id": run.id,
                "status": "success" if run.successful else "failed",
                "created_at": str(run.created_at),
                "tags": list(run.tags),
            })
        return history
```

**验证标准**：
- [ ] `python flows/batch_rag_flow.py run` 测试通过
- [ ] `@timeout(seconds=60)` 生效：故意构造慢查询验证超时
- [ ] `RunTracker.latest_run("BatchRAGFlow")` 能获取最近运行
- [ ] `RunTracker.compare_runs()` 对比两次运行指标
- [ ] 历史记录追溯功能正常

---

### Phase 5: 现有 Flow 升级 + Argo 部署 (2 天)

**目标**：升级现有 3 个 Flow 接入高级特性，完成 Argo 部署。

#### 5.1 QualityPipelineFlow 升级

```python
# 升级要点：
# 1. 加入 @catch + @retry — 质量过滤失败不中断
# 2. 加入 StateRollback — checkpoint 在过滤前，失败可回滚
# 3. 加入 @card — 生成 HTML 质量报告

@project(name="arrow_lake")
class QualityPipelineFlow(ArrowLakeFlowSpec, FlowSpec):
    # ... (保留现有参数)

    @step
    def start(self) -> None:
        self.config = self._load_config()
        self._auto_tag()
        self.next(self.checkpoint)

    @step
    def checkpoint(self) -> None:
        """创建 Lance 版本 checkpoint."""
        from arrow_lake.ingest.storage import LanceStorageManager
        from arrow_lake.workflow.rollback import StateRollback

        storage = LanceStorageManager(base_uri=self.base_uri)
        self._rollback = StateRollback(storage)
        self._rollback.checkpoint(self.dataset_name, tag_prefix="pre-quality-filter")
        self.next(self.apply_filters)

    @retry(times=2, minutes_between_retries=1)
    @catch(var="filter_error", exception=Exception)
    @step
    def apply_filters(self) -> None:
        """应用质量过滤（带重试和错误捕获）."""
        if hasattr(self, "filter_error"):
            # 过滤失败 → 回滚
            self._rollback.rollback(self.dataset_name)
            self.report = None
            self.filter_status = "rolled_back"
        else:
            from arrow_lake import Lake
            lake = Lake(base_uri=self.base_uri, config=self.config)
            self.report = lake.quality_filter(self.dataset_name, self.active_filters)
            self.filter_status = "passed"
        self.next(self.end)

    @card(type="html")
    @step
    def end(self) -> None:
        """输出报告 + HTML card."""
        import json
        if self.report:
            report_json = self.report.to_json()
            self.report_html = f"<h2>Quality Report</h2><pre>{json.dumps(report_json, indent=2)}</pre>"
```

#### 5.2 MayaE2EFlow 升级

```python
# 升级要点：
# 1. embed 步骤加入 @resources + @retry
# 2. quality_filter 步骤加入 @catch + StateRollback
# 3. search 步骤加入 @timeout
# 4. end 步骤加入 @card
```

#### 5.3 ScheduledQualityFlow 升级

```python
# 升级要点：
# 1. check_quality 加入 @retry + @catch
# 2. end 步骤加入 @card 生成每日质量报告
# 3. 加入 Client API 查询昨日结果进行环比对比
```

#### 5.4 Argo 部署清单

```bash
# 生成 Argo Workflow YAML
python flows/quality_pipeline_flow.py --with argo-workflows create --dry-run > argo/quality-pipeline.yaml
python flows/ingest_flow.py --with argo-workflows create --dry-run > argo/ingest.yaml
python flows/embed_flow.py --with argo-workflows create --dry-run > argo/embed.yaml

# 生成 CronWorkflow (每日质量检查)
python flows/scheduled_quality_flow.py --with argo-workflows create > argo/scheduled-quality.yaml

# 部署
kubectl apply -f argo/ -n arrow-lake
```

---

## 4. 改造后的 Flow 目录结构 (实际落地)

```
flows/
├── __init__.py                    # 注册所有 7 个 Flow
├── quality_pipeline_flow.py       # 已有: start → apply_filters → end
├── maya_e2e_flow.py               # 已有: start → ingest → quality → embed → search → end
├── scheduled_quality_flow.py      # 已有: cron 08:00 质量检查
├── ingest_flow.py                 # ✅ Phase 1 已完成: @foreach + @retry + @catch
├── embed_flow.py                  # ✅ Phase 2 已完成: @foreach + @resources + @retry + @catch
├── kg_flow.py                     # ✅ Phase 3 已完成: branch + @retry + @catch + @resources
└── batch_rag_flow.py              # ✅ Phase 4 已完成: @foreach + @timeout + @retry + @catch

arrow_lake/workflow/
├── __init__.py
├── base.py                        # ArrowLakeFlowSpec + FlowRegistry
├── retry.py                       # build_metaflow_retry + tenacity backoff
├── error_handler.py               # classify_error + catch_handler
├── rollback.py                    # StateRollback + CheckpointInfo
├── schedule.py                    # ScheduleConfig + build_schedule
├── tags.py                        # RunTags + find_failed_runs
├── audit.py                       # AuditTrail + HMAC integrity
├── argo.py                        # ArgoWorkflowBridge
└── run_tracker.py                 # ✅ Phase 4 已完成: RunSummary + RunComparison + run_history

tests/unit/flows/
├── __init__.py
├── test_ingest_flow.py            # 17 tests
├── test_embed_flow.py             # 16 tests
├── test_kg_flow.py                # 13 tests
└── test_batch_rag_flow.py         # 12 tests
```

---

## 5. 测试策略

### 5.1 测试分层

```
Unit Tests (每个 Flow 的 step 逻辑)
  ├── test_ingest_flow.py       — foreach 分片逻辑、死信收集 (17 tests)
  ├── test_embed_flow.py        — 分片编码、GPU 资源声明 (16 tests)
  ├── test_kg_flow.py           — branch 并行、join 合并 (13 tests)
  └── test_batch_rag_flow.py    — 超时、重试、结果汇总 + RunTracker (12 tests)

Integration Tests (Flow 端到端, 待 K8s 环境实施)
  ├── test_flow_e2e_ingest.py   — 完整 IngestFlow 运行
  ├── test_flow_e2e_embed.py    — 完整 EmbedFlow 运行
  └── test_flow_resume.py       — 断点续跑测试
```

### 5.2 单元测试模式

```python
# tests/unit/flows/test_ingest_flow.py
"""IngestFlow step 逻辑单元测试."""

import pytest
from unittest.mock import patch, MagicMock


class TestIngestFlowStart:
    """start step: 扫描文件列表."""

    def test_empty_directory(self, tmp_path):
        """空目录 → files 为空列表."""
        # 直接调用 step 方法，不启动 Metaflow runtime
        flow = IngestFlow()
        flow.source_path = str(tmp_path)
        flow._load_config = MagicMock(return_value={})
        flow._auto_tag = MagicMock()
        flow.start()
        assert flow.files == []

    def test_finds_all_files(self, tmp_path):
        """递归扫描所有文件."""
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.json").write_text("{}")
        flow = IngestFlow()
        flow.source_path = str(tmp_path)
        flow._load_config = MagicMock(return_value={})
        flow._auto_tag = MagicMock()
        flow.start()
        assert len(flow.files) == 2


class TestIngestFlowJoin:
    """join step: 汇总成功/失败."""

    def test_separates_success_and_failure(self):
        inputs = [
            MagicMock(result={"file": "a.txt", "status": "success", "rows_ingested": 10}),
            MagicMock(result={"file": "b.txt", "status": "skipped", "error": "corrupt"}),
        ]
        flow = IngestFlow()
        flow.join(inputs)
        assert flow.total_success == 10
        assert flow.total_failure == 1
```

### 5.3 集成测试模式

```python
# tests/integration/test_flow_e2e_ingest.py
"""IngestFlow 端到端集成测试."""

import pytest


@pytest.mark.integration
def test_ingest_flow_e2e(tmp_path):
    """完整的 IngestFlow 运行: 创建文件 → 运行 Flow → 验证结果."""
    # 准备测试数据
    src = tmp_path / "source"
    src.mkdir()
    (src / "doc1.txt").write_text("Hello world")
    (src / "doc2.txt").write_text("Another document")

    lake = tmp_path / "lake"

    # 运行 Flow (通过 subprocess 调用 Metaflow CLI)
    import subprocess
    result = subprocess.run(
        ["python", "flows/ingest_flow.py", "run",
         "--source-path", str(src),
         "--base-uri", str(lake),
         "--dataset-name", "test_e2e"],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0
    assert "success" in result.stdout
```

---

## 6. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| `@foreach` 分片数过多，Metaflow 调度开销大 | 并行度受限 | 限制最大分片数（默认 100），超大文件分批 |
| `@resources(gpu=1)` 在本地无 K8s 时不生效 | 本地开发无法测试资源限制 | 本地忽略 resources，CI 环境验证 |
| `@card` 依赖 Metaflow 元数据服务 | 本地运行无法查看 card | 使用 `metaflow card view` CLI 查看 |
| `@catch` + `@retry` 组合顺序影响行为 | 重试次数可能不符合预期 | 固定装饰器顺序：retry → catch → step |
| Client API 查询历史需要 Metaflow 元数据存储 | 本地文件存储性能差 | 生产环境使用 S3/DynamoDB 后端 |
| Lance 表在 foreach 并行写入时可能冲突 | 数据损坏 | 每个 shard 只读，最终 join 步骤统一写入 |

---

## 7. 装饰器最佳实践

### 7.1 装饰器顺序规范

```python
# 推荐的装饰器顺序（从外到内）:
@resources(gpu=1, memory=16000)   # 1. 资源声明（最外层，调度时生效）
@retry(times=3)                    # 2. 重试策略
@timeout(seconds=300)              # 3. 超时控制
@catch(var="error")                # 4. 异常捕获（最内层，兜底）
@step                              # 5. Metaflow step（最内层）
def my_step(self):
    ...
```

### 7.2 已有基础设施的接入方式

```python
# retry.py 的 build_metaflow_retry → 直接用 Metaflow 原生 @retry
# error_handler.py 的 classify_error → 在 @catch handler 中调用
# rollback.py 的 StateRollback → 在 checkpoint step 中显式调用

from arrow_lake.workflow.error_handler import catch_handler
from arrow_lake.workflow.rollback import StateRollback

@retry(times=3)
@catch(exception=Exception, var="error")
@step
def my_step(self):
    if hasattr(self, "error"):
        catch_handler(self.error)  # 分类并记录
        self._rollback.rollback("my_dataset")  # 回滚
    else:
        # 正常处理...
```

---

## 8. 路线图总览

```
Week 1                          Week 2
┌──────────────────────┐       ┌──────────────────────┐
│  Phase 1: IngestFlow │       │  Phase 4: BatchRAG   │
│  foreach + retry     │  →→→  │  foreach + timeout   │
│  + catch + dead      │       │  + Client API        │
│  letter              │       │                      │
├──────────────────────┤       ├──────────────────────┤
│  Phase 2: EmbedFlow  │       │  Phase 5: 现有 Flow  │
│  foreach + resources │  →→→  │  升级 + Argo 部署    │
│  + card 报告         │       │  + 监控告警           │
├──────────────────────┤       └──────────────────────┘
│  Phase 3: KGFlow     │
│  branch + foreach    │
│  + checkpoint        │
└──────────────────────┘

累计: 11 天 → 5 个新 Flow + 3 个升级 Flow
```

### 时间表

| 阶段 | 内容 | 天数 | 交付物 | 状态 |
|------|------|------|--------|------|
| Phase 1 | IngestFlow: foreach + retry + catch | 2 | `flows/ingest_flow.py` + 测试 | **已完成** |
| Phase 2 | EmbedFlow: foreach + resources + card | 2 | `flows/embed_flow.py` + 测试 | **已完成** |
| Phase 3 | KGFlow: branch + foreach + checkpoint | 3 | `flows/kg_flow.py` + 测试 | **已完成** |
| Phase 4 | BatchRAGFlow: foreach + timeout + Client API | 2 | `flows/batch_rag_flow.py` + `run_tracker.py` + 测试 | **已完成** |
| Phase 5 | 现有 Flow 升级 + Argo 部署 | 2 | 3 个升级 Flow + Argo YAML | 待部署时实施 |

---

## 9. 验收标准

### 9.1 功能验收

- [x] IngestFlow: `@foreach` + `@retry` + `@catch` 已实现
- [x] EmbedFlow: `@foreach` + `@resources` + `@retry` + `@catch` 已实现
- [x] KGFlow: `branch` + `@retry` + `@catch` + `@resources` 已实现
- [x] BatchRAGFlow: `@foreach` + `@timeout` + `@retry` + `@catch` 已实现
- [x] RunTracker: Client API 封装 (`RunSummary`, `RunComparison`, `run_history`)
- [x] 4 个新 Flow 均可通过 `python flows/<name>.py run` 本地运行
- [ ] 3 个现有 Flow 升级后保持向后兼容
- [x] `@foreach` 并行正确展开和 join
- [x] `@retry` + try/except 组合正确处理临时故障（`@catch` 在 v2.19 中不可靠，改用 step 内 try/except）
- [ ] `@resources` 在 K8s 环境正确调度 GPU/CPU
- [ ] `@card` 生成可查看的 HTML 报告
- [ ] `Client API` 可追溯历史运行

### 9.2 质量验收

- [x] IngestFlow: 17/17 单元测试通过
- [x] EmbedFlow: 16/16 单元测试通过
- [x] KGFlow: 13/13 单元测试通过
- [x] BatchRAGFlow + RunTracker: 12/12 单元测试通过
- [x] `bandit -r flows/ingest_flow.py flows/embed_flow.py` 无发现
- [x] `ruff check flows/` 通过
- [x] 2901/2901 全量测试通过（58 flows + 19 audit + 其余 workflow）
- [x] 所有 Flow 的 step 函数 < 50 行（最大 48 行 EmbedFlow.encode_shard）
- [x] 所有文件 < 800 行（最大 184 行 embed_flow.py）

### 9.3 性能验收

- [ ] IngestFlow: 100 文件并行 < 串行时间的 20%
- [ ] EmbedFlow: 分片编码不显著增加总耗时（开销 < 10%）
- [ ] KGFlow: branch 并行实体提取 + schema 构建 < 串行时间 60%
- [ ] BatchRAGFlow: 并行查询总耗时 < (问题数 / 并发度) × 单查询时间 × 1.2
