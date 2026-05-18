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

```
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
