# Workflow Orchestration with Metaflow

Arrow Lake uses Metaflow as its workflow orchestration layer, providing parallel
execution (`@foreach`), branching (`branch`), fault tolerance (`@retry` / `@catch`),
resource management (`@resources`), and run history tracking (Client API).

> Prerequisites: `pip install metaflow` (already included in Arrow Lake dependencies).

***

## 1. Quick Start — Run a Flow

All flows live in the `flows/` directory and are executed via the Metaflow CLI:

```bash
# List available flows
python -m flows.ingest_flow --help

# Run IngestFlow on a directory of files
python flows/ingest_flow.py run \
    --source-path ./data/input \
    --dataset-name documents \
    --base-uri ./data/lake

# Run EmbedFlow with local encoder
python flows/embed_flow.py run \
    --dataset-name documents \
    --encoder-type local \
    --shard-size 500

# Run BatchRAGFlow with a questions file
python flows/batch_rag_flow.py run \
    --questions-file ./questions.json \
    --dataset-name documents \
    --top-k 5
```

Available flows:

| Flow | Purpose | Key Decorators |
| ---- | ------- | -------------- |
| `IngestFlow` | Parallel file ingestion with dead-letter queue | `@foreach` try/except |
| `EmbedFlow` | Parallel embedding with GPU resource management | `@foreach` `@resources` try/except |
| `KGFlow` | Knowledge graph construction with branch parallelism | `branch` `@resources` try/except |
| `BatchRAGFlow` | Batch RAG queries with error capture | `@foreach` try/except |
| `QualityPipelineFlow` | Quality filter pipeline (linear) | `@step` `@project` |
| `MayaE2EFlow` | End-to-end demo pipeline (linear) | `@step` `@project` |
| `ScheduledQualityFlow` | Scheduled daily quality check | `@schedule` `@project` |

***

## 2. IngestFlow — Parallel File Ingestion

`IngestFlow` scans a source directory, processes each file in parallel via
`@foreach`, and collects results into success and dead-letter buckets.

```bash
# Ingest all CSV/JSON/Parquet files from a directory
python flows/ingest_flow.py run \
    --source-path ./data/raw \
    --dataset-name my_dataset
```

Output example:

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

### Resume after failure

Metaflow supports resuming from the last successful step:

```bash
# If the flow was interrupted, resume from where it left off
python flows/ingest_flow.py resume
```

### Programmatic usage via FlowRegistry

```python
from arrow_lake.workflow.base import FlowRegistry

# Import and trigger lazy registration
import flows
flows._register_flows()

# List all registered flows
print(FlowRegistry.list_flows())
# ['batch_rag', 'embed', 'ingest', 'kg', 'maya_e2e', ...]

# Get a flow class by name
IngestFlow = FlowRegistry.get("ingest")
```

***

## 3. EmbedFlow — Parallel Embedding with GPU

`EmbedFlow` splits a dataset into shards, encodes each shard in parallel,
and writes the merged result back.

```bash
# Local encoder (CPU or GPU if available)
python flows/embed_flow.py run \
    --dataset-name documents \
    --encoder-type local \
    --shard-size 500

# API encoder (OpenAI-compatible endpoint)
python flows/embed_flow.py run \
    --dataset-name documents \
    --encoder-type api \
    --shard-size 200
```

Key parameters:

| Parameter | Default | Description |
| --------- | ------- | ----------- |
| `--shard-size` | 500 | Rows per shard (controls parallelism) |
| `--encoder-type` | `local` | `local` (SentenceTransformer) or `api` (OpenAI-compatible) |
| `--vector-column` | `vector` | Name of the output vector column |
| `--text-column` | `text_content` | Name of the text column to encode |

The `@resources(gpu=1, memory=16000)` decorator takes effect when running
on Kubernetes/Argo — locally it is silently ignored.

***

## 4. KGFlow — Knowledge Graph Construction

`KGFlow` uses `branch` to run entity extraction and schema creation in parallel:

```bash
python flows/kg_flow.py run \
    --dataset-name documents \
    --chunk-size 20
```

Flow topology:

```text
start → ┬─ extract_entities (try/except) ─→ join → insert_vertices → end
        └─ ensure_schema (@resources)         ─┘
```

***

## 5. BatchRAGFlow — Parallel Batch Queries

`BatchRAGFlow` fans out one step per question with timeout protection:

```bash
# Prepare a questions file
echo '["What is Arrow Lake?", "How does vector search work?"]' > questions.json

python flows/batch_rag_flow.py run \
    --questions-file ./questions.json \
    --dataset-name documents \
    --top-k 5
```

Each query step has try/except error handling — individual
query failures are captured and do not block the entire batch.

Output example:

```json
{
  "total_questions": 2,
  "success": 2,
  "failed": 0
}
```

***

## 6. Run History with RunTracker

`RunTracker` wraps the Metaflow Client API for querying past runs:

```python
from arrow_lake.workflow.run_tracker import RunTracker

# Get the latest successful run of any flow
latest = RunTracker.latest_run("IngestFlow")
if latest:
    print(f"Run {latest.run_id} at {latest.created_at} — {latest.status}")
    print(f"Tags: {latest.tags}")

# Get recent run history
history = RunTracker.run_history("IngestFlow", limit=5)
for run in history:
    print(f"  {run.run_id}: {run.status} ({run.created_at})")

# Compare two runs
comparison = RunTracker.compare_runs("IngestFlow", "42", "43")
print(f"Row diff: {comparison.diff.get('total_rows', 0)}")
```

***

## 7. Argo Workflows Deployment

For Kubernetes deployment, use `ArgoWorkflowBridge` to generate Argo YAML:

```python
from arrow_lake.workflow.argo import ArgoWorkflowBridge
from flows.ingest_flow import IngestFlow

bridge = ArgoWorkflowBridge()

# Generate Argo Workflow YAML (dry-run)
yaml_str = bridge.generate_workflow(IngestFlow)
print(yaml_str)

# Validate the generated YAML
bridge.validate_workflow(yaml_str)

# Deploy to Kubernetes
bridge.deploy_workflow(IngestFlow)
```

Or via CLI:

```bash
# Generate Argo YAML
python flows/ingest_flow.py --with argo-workflows create --dry-run > argo/ingest.yaml

# Deploy
kubectl apply -f argo/ingest.yaml -n arrow-lake
```

***

## 8. Infrastructure Modules

Arrow Lake provides 10 reusable workflow infrastructure modules:

```python
from arrow_lake.workflow import (
    ArrowLakeFlowSpec,    # Base mixin: _load_config() + _auto_tag()
    FlowRegistry,         # Flow discovery and registration
    StateRollback,        # Lance version checkpoint + rollback
    classify_error,       # Error → TRANSIENT/RESOURCE/VALIDATION/FATAL
    catch_handler,        # @catch handler with structured logging
    build_metaflow_retry, # Configurable @retry decorator builder
    ScheduleConfig,       # cron/daily/hourly schedule configuration
    RunTags,              # Auto-generated run metadata tags
    AuditTrail,           # HMAC-verified audit logging
    ArgoWorkflowBridge,   # Argo YAML generation and deployment
    RunTracker,           # Client API: run history and comparison
)
```

### Error classification

```python
from arrow_lake.workflow.error_handler import classify_error

try:
    # ... some operation ...
    pass
except Exception as exc:
    classified = classify_error(exc)
    print(f"Category: {classified.category}")      # TRANSIENT / RESOURCE / ...
    print(f"Should retry: {classified.should_retry}")  # True / False
    print(f"Max attempts: {classified.retry_max_attempts}")
```

### State rollback

```python
from arrow_lake.workflow.rollback import StateRollback
from arrow_lake.ingest.storage import LanceStorageManager

storage = LanceStorageManager(base_uri="./data/lake")
rb = StateRollback(storage)

# Checkpoint before risky operation
rb.checkpoint("documents", tag_prefix="pre-quality-filter")

# ... run quality filter ...

# If something goes wrong, roll back
rb.rollback("documents")
```

***

## 9. Writing Custom Flows

Follow this template to create a new Arrow Lake flow:

```python
from arrow_lake.workflow.base import ArrowLakeFlowSpec
from metaflow import FlowSpec, Parameter, catch, project, retry, step


@project(name="arrow_lake")
class MyCustomFlow(ArrowLakeFlowSpec, FlowSpec):
    """One-line description of what this flow does."""

    base_uri: str = Parameter("base-uri", default="./data/lake")
    config_path: str = Parameter("config-path", default="")

    @step
    def start(self) -> None:
        self.config = self._load_config()
        self._auto_tag()
        # ... prepare data ...
        self.next(self.end)

    @step
    def end(self) -> None:
        import json
        print(json.dumps({"status": "done"}))


if __name__ == "__main__":
    MyCustomFlow()
```

Error handling pattern — prefer try/except inside steps over `@catch`:

```python
@resources(gpu=1, memory=16000)   # 1. Resource declaration
@step                              # 2. Metaflow step
def my_step(self) -> None:
    try:
        # business logic
        pass
    except Exception as exc:
        # error handling — log, set status, etc.
        logger.warning("step_failed", error=str(exc))
```

Note: Metaflow `@catch` / `@retry` decorators can produce "Internal error"
in v2.19+ and bypass the catch mechanism. try/except is more reliable.

Register the new flow in `flows/__init__.py`:

```python
_flow_map: dict[str, str] = {
    # ... existing flows ...
    "my_custom": "flows.my_custom_flow.MyCustomFlow",
}
```

***

## 10. Configuration Reference

> Field names below are verified against `arrow_lake/config/workflow.py`. Passing a field that does
> not exist raises a pydantic `ValidationError`.

### WorkflowConfig

`WorkflowConfig` (`config/workflow.py:8-28`) controls general workflow behavior:

```python
from arrow_lake.config import WorkflowConfig

wf_config = WorkflowConfig(
    max_retry_attempts=3,        # Maximum retry attempts per step (>=0)
    min_backoff_seconds=1.0,     # Minimum backoff between retries (exponential)
    max_backoff_seconds=60.0,    # Maximum backoff between retries
    checkpoint_enabled=True,     # Enable Lance version checkpointing before steps
    ray_execution_enabled=False, # Enable Ray cluster execution (--with ray)
    auto_tag_runs=True,          # Auto-generate tags from run metadata
    artifact_retention_days=30,  # Days to retain Argo workflow artifacts
    schedule_cron=None,          # Optional cron expression for scheduled runs
)
```

### ArgoConfig

`ArgoConfig` (`config/workflow.py:45-62`) controls Argo Workflows integration:

```python
from arrow_lake.config import ArgoConfig

argo_config = ArgoConfig(
    namespace="arrow-lake",       # Kubernetes namespace for Argo workflows
    service_account="arrow-lake", # Service account for workflow pods
    workflow_timeout=3600,        # Workflow execution timeout in seconds (>=60)
    image="arrow-lake:latest",    # Container image for workflow pods
    image_pull_policy="IfNotPresent",  # Image pull policy
    artifact_storage="",          # Storage backend for artifacts (s3:// or minio://)
)
```

### AutoscaleConfig

`AutoscaleConfig` (`config/workflow.py:72-95`) controls dynamic GPU scaling:

```python
from arrow_lake.config import AutoscaleConfig

autoscale_config = AutoscaleConfig(
    enabled=True,                 # Whether GPU autoscaling is active
    min_workers=0,                # Minimum GPU workers (0 = scale to zero)
    max_workers=8,                # Maximum GPU workers (>=1)
    scale_up_timeout_seconds=300, # Max wait time for scale-up (>=60)
    idle_timeout_seconds=600,     # Seconds of inactivity before scale-down (>=60)
    spot_preference=0.8,          # Prefer spot instances (0.0=on-demand, 1.0=spot-only)
    gpu_increment=0.5,            # Fractional GPU increment (must be 0.5 or 1.0)
    cooldown_period=60.0,         # Seconds between consecutive scaling decisions
    scale_down_protection=True,   # Wait for all tasks to finish before scaling down
)
```

***

## 11. FlowRegistry API Reference

`FlowRegistry` (`workflow/base.py:65-118`) maps flow names to `FlowSpec` classes via class-level
storage. The full API surface is intentionally small:

```python
from arrow_lake.workflow.base import FlowRegistry

# List all registered flow names (sorted)
flow_names = FlowRegistry.list_flows()
# ['batch_rag', 'embed', 'ingest', 'kg', 'maya_e2e', ...]

# Get the flow class for instantiation (raises KeyError if unknown)
IngestFlow = FlowRegistry.get("ingest")

# Register / clear (used by flows/__init__.py at import time)
FlowRegistry.register("my_flow", MyFlow)   # raises ValueError on duplicate name
FlowRegistry.clear()                       # remove all registrations (mainly for tests)
```

| Method | Returns | Description |
| ------ | ------- | ----------- |
| `register(name, flow_cls)` | `None` | Register a flow class (raises `ValueError` on duplicate) |
| `get(name)` | `type[ArrowLakeFlowSpec]` | Flow class for instantiation (raises `KeyError` if absent) |
| `list_flows()` | `list[str]` | All registered flow names, sorted |
| `clear()` | `None` | Remove all registrations |

> There is no `get_flow_info()` / `FlowInfo` on `FlowRegistry` — to inspect a flow's parameters or
> description, instantiate the class returned by `get(name)` and read its Metaflow `Parameter`
> declarations directly.

***

## 12. Fire-and-Forget Async Tasks (TaskManager)

Metaflow flows run out-of-band via the CLI. For long-running operations triggered **from the API**
(KG builds, exports, large cleans), Arrow Lake uses an in-process `TaskManager`
(`arrow_lake/api/tasks.py:115`) that runs coroutines in the background and exposes a polling status
endpoint. This is the same mechanism the console drives for progress bars.

### Lifecycle

```text
Client POSTs a long operation  →  TaskManager.run_background(...) returns a task_id immediately
                                  ↓ (asyncio task, holds a strong ref to avoid GC)
Client polls  GET /api/v1/tasks/{task_id}/status  until status == "completed" | "failed"
```

- **Fire-and-forget, GC-safe**: `run_background()` stores a strong reference to the asyncio task so
  the runtime's garbage collector cannot silently kill it mid-flight (v1.6.1 generalized this from
  the original export-only tracker).
- **Status polling**: `GET /api/v1/tasks/{task_id}/status` (`routers/async_tasks.py:57`) returns
  `AsyncTaskStatusResponse` with `status`, `progress` (0.0–1.0), `result`, `detail`, and `error`.
- **Cross-worker visibility (v1.6.2)**: when Redis is configured, task state is written to a shared
  Redis HASH so any API worker can answer a status poll (init via `TaskManager.init_redis_store()`).
- **Durable history (v1.9.0)**: when the libSQL `TaskHistoryStore` is wired
  (`TaskManager.init_history_store()`), completed/failed tasks are recorded beyond the Redis TTL, so
  the `/tasks` list survives a Redis flush or restart.
- **Owner notifications (v1.9.3)**: `TaskManager.init_user_state_store()` wires the my-workspace
  notification store; task completion writes a notification for the owning user.

### Programmatic Usage

```python
from arrow_lake.api.tasks import TaskManager

# Fire-and-forget a coroutine; returns immediately with a task_id
task_id = await TaskManager.run_background(
    owner="user@example.com",
    name="kg_build_documents",
    coro=my_async_work(),
)
# Poll elsewhere:
task = TaskManager.get_task(task_id)
print(task.status, task.progress)   # "running" 0.42  →  "completed" 1.0
```

> **When to use which**: Metaflow (`flows/*.py`) is for multi-step, resumable, artifact-tracked
> pipelines run via CLI/Argo. `TaskManager` is for single in-API async operations that need a
> pollable `task_id` and a progress bar in the console.
