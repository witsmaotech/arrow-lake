# Maya E2E Pipeline Runbook

## Overview

The Maya E2E pipeline is a 4-step Metaflow flow that processes data through ingestion, quality filtering, embedding, and search. It validates the full Arrow Lake platform capability.

## Prerequisites

- Python >= 3.11
- `uv` package manager
- Dependencies installed: `uv sync`
- LanceDB, PyArrow, NumPy, structlog

## Running Locally

### Synthetic Mode (no external data)

```bash
cd /path/to/wits-infra-dintellihub
python flows/maya_e2e_flow.py run --data-path ""
```

This generates 100 synthetic documents and runs the full pipeline.

### With Real Data

```bash
python flows/maya_e2e_flow.py run --data-path ./test_data --dataset-name my_data
```

### With Custom Config

```bash
python flows/maya_e2e_flow.py run --config-path configs/dev.yaml
```

## Running Other Flows

### Quality Pipeline

```bash
python flows/quality_pipeline_flow.py run --dataset-name documents --active-filters text_length
```

### Scheduled Quality Check (daily at 8 AM)

```bash
python flows/scheduled_quality_flow.py run --dataset-name documents
```

## Pipeline Steps

| Step | Description | Dependencies |
|------|-------------|-------------|
| `start` | Load config, validate inputs | None |
| `ingest` | Create/load Lance dataset | start |
| `quality_filter` | Filter low-quality records | ingest |
| `embed` | Generate vector embeddings | quality_filter |
| `search` | Validate vector search | embed |
| `end` | Output pipeline summary | search |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ARROW_LAKE__STORAGE__BACKEND` | `local` | Storage backend (local, minio) |
| `ARROW_LAKE__STORAGE__BASE_URI` | `./data/lake` | Base URI for Lance datasets |
| `ARROW_LAKE__STORAGE__S3_ENDPOINT` | — | S3 endpoint for MinIO |
| `ARROW_LAKE__OBSERVABILITY__LOG_LEVEL` | `INFO` | Structlog log level |
| `ARROW_LAKE__WORKFLOW__CHECKPOINT_ENABLED` | `true` | Enable Lance version checkpointing |

## Output

The pipeline outputs a JSON summary to stdout:

```json
{
  "pipeline": "maya_e2e",
  "ingested": 100,
  "quality_filter": {"total": 100, "passed": 97, "rejected": 3},
  "embedded": 97,
  "embedding_dim": 128,
  "search": {"status": "success", "top_k": 10, "elapsed_seconds": 0.0023}
}
```

## Troubleshooting

### `ModuleNotFoundError: No module named 'lancedb'`

```bash
uv sync
```

### `Dataset 'xxx' not found`

Ensure the dataset exists before running quality filter or embed steps. In synthetic mode, the dataset is created automatically.

### Ray not initialized warnings

The pipeline works without Ray. Ray is only required for distributed execution (`--with ray` mode).

### Quality filter rejects all records

Check the filter thresholds in your config. Default `text_min_chars=10` may filter out very short documents. Adjust via:

```bash
python flows/quality_pipeline_flow.py run --dataset-name documents
```

Or update `configs/dev.yaml`:

```yaml
quality:
  text_min_chars: 1
```

### Embedding step produces all-zero vectors

The pipeline uses deterministic pseudo-embeddings (numpy RandomState) for demo purposes. In production, use `LocalEmbeddingEncoder` or `RayServeEmbeddingEncoder` from `arrow_lake.embed`.
