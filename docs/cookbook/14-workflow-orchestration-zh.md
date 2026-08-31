# 基于 Metaflow 的工作流编排

Arrow Lake 以 Metaflow 作为工作流编排层,提供并行执行(`@foreach`)、分支(`branch`)、容错(`@retry` / `@catch`)、资源管理(`@resources`)与运行历史追踪(Client API)。

> 前置准备:`pip install metaflow`(已包含在 Arrow Lake 依赖中)。

***

## 1. 快速上手 — 运行一个 Flow

所有 flow 位于 `flows/` 目录,通过 Metaflow CLI 执行:

```bash
# 列出可用 flow
python -m flows.ingest_flow --help

# 对一个文件目录运行 IngestFlow
python flows/ingest_flow.py run \
    --source-path ./data/input \
    --dataset-name documents \
    --base-uri ./data/lake

# 用本地编码器运行 EmbedFlow
python flows/embed_flow.py run \
    --dataset-name documents \
    --encoder-type local \
    --shard-size 500

# 用问题文件运行 BatchRAGFlow
python flows/batch_rag_flow.py run \
    --questions-file ./questions.json \
    --dataset-name documents \
    --top-k 5
```

可用 flow:

| Flow | 用途 | 关键装饰器 |
| ---- | ---- | --------- |
| `IngestFlow` | 并行文件摄取 + 死信队列 | `@foreach` try/except |
| `EmbedFlow` | 并行嵌入 + GPU 资源管理 | `@foreach` `@resources` try/except |
| `KGFlow` | 知识图谱构建 + 分支并行 | `branch` `@resources` try/except |
| `BatchRAGFlow` | 批量 RAG 查询 + 错误捕获 | `@foreach` try/except |
| `QualityPipelineFlow` | 质量过滤管线(线性) | `@step` `@project` |
| `MayaE2EFlow` | 端到端演示管线(线性) | `@step` `@project` |
| `ScheduledQualityFlow` | 定时每日质量检查 | `@schedule` `@project` |

***

## 2. IngestFlow — 并行文件摄取

`IngestFlow` 扫描源目录,通过 `@foreach` 并行处理每个文件,并把结果归入成功桶与死信桶。

```bash
# 摄取目录下所有 CSV/JSON/Parquet 文件
python flows/ingest_flow.py run \
    --source-path ./data/raw \
    --dataset-name my_dataset
```

输出示例:

```json
{
  "total_files": 10,
  "success": 9,
  "failed": 1,
  "total_rows_ingested": 45000,
  "dead_letter": [
    {"file": "/data/raw/broken.csv", "status": "failed", "error": "..."}
  ]
}
```

### 失败后恢复

Metaflow 支持从最近一次成功的步骤恢复:

```bash
# 若 flow 被中断,从断点恢复
python flows/ingest_flow.py resume
```

### 通过 FlowRegistry 编程式使用

```python
from arrow_lake.workflow.base import FlowRegistry

# 导入并触发惰性注册
import flows
flows._register_flows()

# 列出所有已注册 flow
print(FlowRegistry.list_flows())
# ['batch_rag', 'embed', 'ingest', 'kg', 'maya_e2e', ...]

# 按名称获取 flow 类
IngestFlow = FlowRegistry.get("ingest")
```

***

## 3. EmbedFlow — 带 GPU 的并行嵌入

`EmbedFlow` 把数据集切分为分片,并行编码每个分片,再合并写回。

```bash
# 本地编码器(CPU,或可用时 GPU)
python flows/embed_flow.py run \
    --dataset-name documents \
    --encoder-type local \
    --shard-size 500

# API 编码器(OpenAI 兼容端点)
python flows/embed_flow.py run \
    --dataset-name documents \
    --encoder-type api \
    --shard-size 200
```

关键参数:

| 参数 | 默认值 | 说明 |
| ---- | ------ | ---- |
| `--shard-size` | 500 | 每分片行数(控制并行度) |
| `--encoder-type` | `local` | `local`(SentenceTransformer)或 `api`(OpenAI 兼容) |
| `--vector-column` | `vector` | 输出向量列名 |
| `--text-column` | `text_content` | 待编码的文本列名 |

`@resources(gpu=1, memory=16000)` 装饰器仅在 Kubernetes/Argo 上运行时生效——本地运行时被静默忽略。

***

## 4. KGFlow — 知识图谱构建

`KGFlow` 用 `branch` 并行执行实体抽取与 schema 创建:

```bash
python flows/kg_flow.py run \
    --dataset-name documents \
    --chunk-size 20
```

Flow 拓扑:

```text
start → ┬─ extract_entities (try/except) ─→ join → insert_vertices → end
        └─ ensure_schema (@resources)         ─┘
```

***

## 5. BatchRAGFlow — 并行批量查询

`BatchRAGFlow` 为每个问题展开一个步骤,并带超时保护:

```bash
# 准备问题文件
echo '["What is Arrow Lake?", "How does vector search work?"]' > questions.json

python flows/batch_rag_flow.py run \
    --questions-file ./questions.json \
    --dataset-name documents \
    --top-k 5
```

每个查询步骤都有 try/except 错误处理——单条查询失败会被捕获,不会阻塞整批。

输出示例:

```json
{
  "total_questions": 2,
  "success": 2,
  "failed": 0
}
```

***

## 6. 用 RunTracker 查询运行历史

`RunTracker` 封装 Metaflow Client API,用于查询历史运行:

```python
from arrow_lake.workflow.run_tracker import RunTracker

# 获取任意 flow 最近一次成功的运行
latest = RunTracker.latest_run("IngestFlow")
if latest:
    print(f"运行 {latest.run_id} 于 {latest.created_at} — {latest.status}")
    print(f"标签: {latest.tags}")

# 获取近期运行历史
history = RunTracker.run_history("IngestFlow", limit=5)
for run in history:
    print(f"  {run.run_id}: {run.status} ({run.created_at})")

# 对比两次运行
comparison = RunTracker.compare_runs("IngestFlow", "42", "43")
print(f"行数差异: {comparison.diff.get('total_rows', 0)}")
```

***

## 7. Argo Workflows 部署

Kubernetes 部署时,用 `ArgoWorkflowBridge` 生成 Argo YAML:

```python
from arrow_lake.workflow.argo import ArgoWorkflowBridge
from flows.ingest_flow import IngestFlow

bridge = ArgoWorkflowBridge()

# 生成 Argo Workflow YAML(dry-run)
yaml_str = bridge.generate_workflow(IngestFlow)
print(yaml_str)

# 校验生成的 YAML
bridge.validate_workflow(yaml_str)

# 部署到 Kubernetes
bridge.deploy_workflow(IngestFlow)
```

或通过 CLI:

```bash
# 生成 Argo YAML
python flows/ingest_flow.py --with argo-workflows create --dry-run > argo/ingest.yaml

# 部署
kubectl apply -f argo/ingest.yaml -n arrow-lake
```

***

## 8. 基础设施模块

Arrow Lake 提供 10 个可复用的工作流基础设施模块:

```python
from arrow_lake.workflow import (
    ArrowLakeFlowSpec,    # 基础 mixin:_load_config() + _auto_tag()
    FlowRegistry,         # flow 发现与注册
    StateRollback,        # Lance 版本快照 + 回滚
    classify_error,       # 错误 → TRANSIENT/RESOURCE/VALIDATION/FATAL
    catch_handler,        # 带结构化日志的 @catch 处理器
    build_metaflow_retry, # 可配置的 @retry 装饰器构建器
    ScheduleConfig,       # cron/daily/hourly 调度配置
    RunTags,              # 自动生成的运行元数据标签
    AuditTrail,           # HMAC 校验的审计日志
    ArgoWorkflowBridge,   # Argo YAML 生成与部署
    RunTracker,           # Client API:运行历史与对比
)
```

### 错误分类

```python
from arrow_lake.workflow.error_handler import classify_error

try:
    # ... 某个操作 ...
    pass
except Exception as exc:
    classified = classify_error(exc)
    print(f"类别: {classified.category}")          # TRANSIENT / RESOURCE / ...
    print(f"是否重试: {classified.should_retry}")    # True / False
    print(f"最大重试次数: {classified.retry_max_attempts}")
```

### 状态回滚

```python
from arrow_lake.workflow.rollback import StateRollback
from arrow_lake.ingest.storage import LanceStorageManager

storage = LanceStorageManager(base_uri="./data/lake")
rb = StateRollback(storage)

# 在高风险操作前打快照
rb.checkpoint("documents", tag_prefix="pre-quality-filter")

# ... 运行质量过滤 ...

# 出问题时回滚
rb.rollback("documents")
```

***

## 9. 编写自定义 Flow

按此模板创建新的 Arrow Lake flow:

```python
from arrow_lake.workflow.base import ArrowLakeFlowSpec
from metaflow import FlowSpec, Parameter, catch, project, retry, step


@project(name="arrow_lake")
class MyCustomFlow(ArrowLakeFlowSpec, FlowSpec):
    """一句话描述这个 flow 做什么。"""

    base_uri: str = Parameter("base-uri", default="./data/lake")
    config_path: str = Parameter("config-path", default="")

    @step
    def start(self) -> None:
        self.config = self._load_config()
        self._auto_tag()
        # ... 准备数据 ...
        self.next(self.end)

    @step
    def end(self) -> None:
        import json
        print(json.dumps({"status": "done"}))


if __name__ == "__main__":
    MyCustomFlow()
```

错误处理模式——优先在 step 内用 try/except,而非 `@catch`:

```python
@resources(gpu=1, memory=16000)   # 1. 资源声明
@step                              # 2. Metaflow step
def my_step(self) -> None:
    try:
        # 业务逻辑
        pass
    except Exception as exc:
        # 错误处理——记日志、设状态等
        logger.warning("step_failed", error=str(exc))
```

注意:Metaflow 的 `@catch` / `@retry` 装饰器在 v2.19+ 可能产生 "Internal error" 并绕过 catch 机制。try/except 更可靠。

在 `flows/__init__.py` 中注册新 flow:

```python
_flow_map: dict[str, str] = {
    # ... 已有 flow ...
    "my_custom": "flows.my_custom_flow.MyCustomFlow",
}
```

***

## 10. 配置参考

> 下方字段名已对照 `arrow_lake/config/workflow.py` 核实。传入不存在的字段会抛 pydantic `ValidationError`。

### WorkflowConfig

`WorkflowConfig`(`config/workflow.py:8-28`)控制通用工作流行为:

```python
from arrow_lake.config import WorkflowConfig

wf_config = WorkflowConfig(
    max_retry_attempts=3,        # 每步最大重试次数(>=0)
    min_backoff_seconds=1.0,     # 重试间最小退避(指数)
    max_backoff_seconds=60.0,    # 重试间最大退避
    checkpoint_enabled=True,     # 步前启用 Lance 版本快照
    ray_execution_enabled=False, # 启用 Ray 集群执行(--with ray)
    auto_tag_runs=True,          # 从运行元数据自动生成标签
    artifact_retention_days=30,  # Argo workflow artifact 保留天数
    schedule_cron=None,          # 可选的定时运行 cron 表达式
)
```

### ArgoConfig

`ArgoConfig`(`config/workflow.py:45-62`)控制 Argo Workflows 集成:

```python
from arrow_lake.config import ArgoConfig

argo_config = ArgoConfig(
    namespace="arrow-lake",       # Argo workflow 的 K8s 命名空间
    service_account="arrow-lake", # workflow pod 的服务账号
    workflow_timeout=3600,        # workflow 执行超时(秒,>=60)
    image="arrow-lake:1.11.4",    # workflow pod 的容器镜像
    image_pull_policy="IfNotPresent",  # 镜像拉取策略
    artifact_storage="",          # artifact 存储后端(s3:// 或 minio://)
)
```

### AutoscaleConfig

`AutoscaleConfig`(`config/workflow.py:72-95`)控制 GPU 动态伸缩:

```python
from arrow_lake.config import AutoscaleConfig

autoscale_config = AutoscaleConfig(
    enabled=True,                 # 是否启用 GPU 自动伸缩
    min_workers=0,                # 最小 GPU worker 数(0 = 缩到零)
    max_workers=8,                # 最大 GPU worker 数(>=1)
    scale_up_timeout_seconds=300, # 扩容最大等待时间(>=60)
    idle_timeout_seconds=600,     # 多少秒空闲后缩容(>=60)
    spot_preference=0.8,          # 偏好竞价实例(0.0=按量,1.0=仅竞价)
    gpu_increment=0.5,            # GPU 增量粒度(必须 0.5 或 1.0)
    cooldown_period=60.0,         # 连续伸缩决策间隔(秒)
    scale_down_protection=True,   # 缩容前等待所有任务完成
)
```

***

## 11. FlowRegistry API 参考

`FlowRegistry`(`workflow/base.py:65-118`)通过类级存储把 flow 名映射到 `FlowSpec` 类。完整 API 刻意保持精简:

```python
from arrow_lake.workflow.base import FlowRegistry

# 列出所有已注册 flow 名(已排序)
flow_names = FlowRegistry.list_flows()
# ['batch_rag', 'embed', 'ingest', 'kg', 'maya_e2e', ...]

# 获取 flow 类用于实例化(未知则抛 KeyError)
IngestFlow = FlowRegistry.get("ingest")

# 注册 / 清空(由 flows/__init__.py 在导入时使用)
FlowRegistry.register("my_flow", MyFlow)   # 重名抛 ValueError
FlowRegistry.clear()                       # 移除所有注册(主要用于测试)
```

| 方法 | 返回 | 说明 |
| ---- | ---- | ---- |
| `register(name, flow_cls)` | `None` | 注册一个 flow 类(重名抛 `ValueError`) |
| `get(name)` | `type[ArrowLakeFlowSpec]` | 用于实例化的 flow 类(缺失抛 `KeyError`) |
| `list_flows()` | `list[str]` | 所有已注册 flow 名,已排序 |
| `clear()` | `None` | 移除所有注册 |

> `FlowRegistry` 没有 `get_flow_info()` / `FlowInfo`——若要查看 flow 的参数或描述,请实例化 `get(name)` 返回的类,直接读取其 Metaflow `Parameter` 声明。

***

## 12. Fire-and-Forget 异步任务(TaskManager)

Metaflow flow 通过 CLI 在带外运行。对于**从 API 触发**的长时间运行操作(KG 构建、导出、大规模清洗),Arrow Lake 使用进程内 `TaskManager`(`arrow_lake/api/tasks.py:115`),在后台运行协程并暴露轮询状态端点。这也是 console 进度条驱动的同一机制。

### 生命周期

```text
客户端 POST 长操作  →  TaskManager.run_background(...) 立即返回 task_id
                                  ↓(asyncio 任务,持有强引用以防 GC)
客户端轮询  GET /api/v1/tasks/{task_id}/status  直到 status == "completed" | "failed"
```

- **Fire-and-forget,防 GC**:`run_background()` 持有 asyncio 任务的强引用,运行时垃圾回收器不会在运行途中静默杀掉它(v1.6.1 从最初的仅导出追踪器泛化而来)。
- **状态轮询**:`GET /api/v1/tasks/{task_id}/status`(`routers/async_tasks.py:57`)返回 `AsyncTaskStatusResponse`,含 `status`、`progress`(0.0–1.0)、`result`、`detail`、`error`。
- **跨 worker 可见(v1.6.2)**:配置 Redis 时,任务状态写入共享 Redis HASH,任意 API worker 都能应答状态轮询(经 `TaskManager.init_redis_store()` 初始化)。
- **持久化历史(v1.9.0)**:接入 libSQL `TaskHistoryStore`(`TaskManager.init_history_store()`)后,完成/失败的任务会记录到 Redis TTL 之外,`/tasks` 列表因此在 Redis 刷新或重启后仍存活。
- **属主通知(v1.9.3)**:`TaskManager.init_user_state_store()` 接入 my-workspace 通知存储;任务完成时为属主用户写入一条通知。

### 编程式使用

```python
from arrow_lake.api.tasks import TaskManager

# Fire-and-forget 一个协程;立即返回 task_id
task_id = await TaskManager.run_background(
    owner="user@example.com",
    name="kg_build_documents",
    coro=my_async_work(),
)
# 在别处轮询:
task = TaskManager.get_task(task_id)
print(task.status, task.progress)   # "running" 0.42  →  "completed" 1.0
```

> **何时用哪个**:Metaflow(`flows/*.py`)用于经 CLI/Argo 运行的多步、可恢复、带 artifact 追踪的管线;`TaskManager` 用于 API 内单次异步操作——需要一个可轮询的 `task_id` 和 console 中的进度条。
