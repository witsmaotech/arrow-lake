# Plan: HTTP File Upload → MinIO → Ingest (v2 — Optimized)

## Context

All 6 ingest API endpoints only accept server-side filesystem paths.
When API runs in Docker, host paths are unreachable → scripts skip.
MinIO (`BlobStoreManager`) is fully integrated in Docker Compose but was not exposed to clients.

**Phase 1 (DONE)**: Multipart proxy upload → MinIO → blob_keys ingest
**Phase 2 (Current)**: Eliminate bottlenecks — presigned URL direct upload, S3-native ingest, small file inline

## Phase 1 — Complete

- Models: `UploadResponse`, `UploadedBlob`, `blob_keys` on ingest requests
- Endpoint: `POST /{name}/upload` multipart proxy → MinIO
- Client: auto-upload in conftest `ingest_*` methods
- Tests: 23 unit tests, 2663 total pass, 0 regressions
- Verification: scripts 11/16/19 now PASS (were SKIP)

## Performance Bottleneck Analysis

### Problem 1: Double network hop (P0)

```
Current:  client → API (full file in memory) → MinIO → API temp dir → Ingestor
Optimal:  client → MinIO (direct, presigned URL) → Ingestor reads S3 path
```

API server buffers entire file in memory via `await f.read()`.
20 concurrent × 10MB = 200MB RAM consumed by proxy.
Presigned PUT URL eliminates API from data path entirely.

### Problem 2: Temp directory disk usage (P1)

Every blob_key ingest downloads to `/tmp` before processing.
100 large files concurrent = 2× disk usage (MinIO copy + temp copy).
Daft natively reads S3 URIs with `StorageConfig.to_storage_options()` credentials.
Eliminating temp download = zero local disk footprint.

### Problem 3: Small file latency (P2)

1KB JSON record still goes through full upload→MinIO→download cycle (~100ms overhead).
Inline small files (<1MB) as base64 in JSON body, bypass MinIO entirely.

### Problem 4: Serial blob resolution (P3)

`_resolve_blob_keys` downloads blobs one-by-one.
ThreadPoolExecutor with 4 workers = 2-4× throughput for batch uploads.

## Phase 2 Implementation

### P0: Presigned URL Direct Upload

**New endpoint** in `datasets.py`:
```
POST /{name}/upload/presign
Body: {"filenames": ["data.csv", "report.pdf"]}
Response: {"presigned_uploads": [{"key": "uploads/ds/data.csv", "upload_url": "http://minio:9000/...", "size_bytes": 0}]}
```

Logic:
1. Validate filenames (`_sanitize_filename`)
2. Build blob keys `uploads/{name}/{filename}`
3. Call `blob_store.presigned_url(key, operation="put_object", expires_in=3600)` for each
4. Return presigned PUT URLs

**Client change** in `conftest.py`:
1. `GET /presign` → get PUT URLs
2. `PUT` each file directly to MinIO via presigned URL (urllib, no auth header needed)
3. `POST /ingest` with `blob_keys`

Fallback to multipart proxy upload if presign fails (backward compat).

### P1: S3-Native Ingest (skip temp download)

**New endpoint helper** or modify `_resolve_blob_keys`:

For files (CSV/JSON/Parquet) — use S3 URI directly:
```python
def _blob_keys_to_s3_uris(blob_keys: list[str], lake) -> list[str]:
    sc = lake._config.storage
    return [f"s3://{sc.s3_bucket}/{key}" for key in blob_keys]
```

Daft reads S3 URIs natively: `daft.read_csv("s3://bucket/uploads/ds/data.csv", storage_options=sc.to_storage_options())`.

For images/videos/documents — keep temp download (Pillow/av need local paths).

### P2: Small File Inline

In client `upload_files`:
```python
SMALL_FILE_THRESHOLD = 1_000_000  # 1MB

def _ingest_via_upload(self, name, file_paths, endpoint):
    small = [p for p in file_paths if Path(p).stat().st_size < SMALL_FILE_THRESHOLD]
    large = [p for p in file_paths if p not in small]

    # Small files: base64 inline in JSON
    # Large files: presigned URL → MinIO
```

New request model field: `inline_files: list[dict]` with `{"filename": str, "data_base64": str, "content_type": str}`.

### P3: Concurrent Blob Resolution

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def _resolve_blob_keys(blob_keys, lake, tmp_dir):
    blob_store = _get_blob_store(lake)
    paths = [None] * len(blob_keys)

    def _download(idx_key):
        idx, key = idx_key
        dest = os.path.join(tmp_dir, key.rsplit("/", 1)[-1])
        blob_store.download_file(key, dest)
        return idx, dest

    with ThreadPoolExecutor(max_workers=4) as pool:
        for idx, path in pool.map(_download, enumerate(blob_keys)):
            paths[idx] = path

    return [p for p in paths if p is not None]
```

## Priority & Effort

| Item | Effort | Impact | Files |
|------|--------|--------|-------|
| P0: Presigned URL | Low | API memory -90% | `datasets.py`, `dataset.py`, `conftest.py` |
| P1: S3-native ingest | Medium | Disk -50%, latency -30% | `datasets.py`, `ingestor.py` |
| P2: Small file inline | Low | Latency -80% for <1MB | `dataset.py`, `datasets.py`, `conftest.py` |
| P3: Concurrent download | Low | Batch throughput +2-4x | `datasets.py` |

## Verification

1. Unit tests for presign endpoint + S3 URI resolution + inline files
2. Run cookbook scripts against Docker — same PASS results
3. MinIO console shows files uploaded via presigned URL
4. Memory profiling: compare API RSS before/after 20 concurrent uploads
