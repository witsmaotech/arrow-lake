# ADR-07: DuckDB High Availability — Unified Session Management

**Status**: Accepted
**Date**: 2026-04-21
**Supersedes**: ADR-05, ADR-06

---

## Context

Arrow Lake uses DuckDB as its OLAP engine for SQL analytics, vector search (native lance functions), faceted search (CUBE queries), full-text search, and metadata queries. Prior to v1.1, DuckDB connection management was fragmented across three independent mechanisms:

1. **`DuckDBSession`** (`query/_db.py`): Per-query ephemeral sessions with resource governance (memory, timeout). Used by all query bridges.
2. **`ThreadPoolExecutor` + Semaphore** (`query/_async.py`): Async bridge with hardcoded concurrency limit of 4.
3. **`DuckDBConnectionPool`** (`catalog/connection_pool.py`): Connection pool for catalog persistence (max=5).

Key problems:
- `OlapConfig.max_concurrent_queries` was defined but **never enforced**
- Async semaphore (4) and catalog pool (5) had **hardcoded limits**, disconnected from config
- No global concurrency control — each query bridge created sessions independently
- No query queuing — pool exhaustion resulted in immediate `TimeoutError`
- No metrics or observability for connection health

## Decision

### Primary: Unified `DuckDBSessionManager`

Create `arrow_lake/query/session_manager.py` with a `DuckDBSessionManager` class that:

1. **Enforces `max_concurrent_queries`** from `OlapConfig` via `threading.Semaphore`
2. **Applies per-connection resource governance** (memory limit, statement timeout) via existing `DuckDBSession`
3. **Tracks query statistics**: total queries, errors, timeouts, slow queries, avg wait time
4. **Exports Prometheus metrics** via `arrow_lake/core/metrics.py` (6 new gauges/counters)
5. **Supports graceful shutdown**: active sessions complete, new acquisitions rejected

### Secondary: Configurable async executor

Update `query/_async.py` with `configure_query_executor(max_workers)` to replace the hardcoded semaphore, allowing the concurrency limit to match `OlapConfig.max_concurrent_queries`.

### Preserved: Catalog connection pool

`DuckDBConnectionPool` (`catalog/connection_pool.py`) serves a different purpose (persistent catalog database with schema sharing). It remains unchanged as a separate pool with its own lifecycle.

### Deferred: config.py package split

The 1,190-line `config.py` is well-organized with clear domain sections. A full package split (`config/` with 14 sub-modules) is deferred to v1.2 as it carries refactoring risk with minimal functional benefit.

## Architecture

```
Query Bridges (olap, vector, fts, hybrid, faceted, metadata)
        │
        ▼
DuckDBSessionManager  ←─ Semaphore(max_concurrent_queries)
  │     │
  │     ├── _ManagedSession (wraps DuckDBSession)
  │     │     └── DuckDBSession → duckdb.connect()
  │     │           ├── LOAD lance
  │     │           ├── SET memory_limit, threads, statement_timeout
  │     │           └── SET s3_* (if S3 backend)
  │     │
  │     └── SessionPoolStats → Prometheus metrics
  │
  ▼
DuckDB (in-process, embedded)
```

## Consequences

### Positive
- **Global concurrency enforcement**: `max_concurrent_queries` is now actually enforced
- **Observable**: 6 new Prometheus metrics expose pool health
- **No breaking changes**: Query bridges continue using `create_duckdb_session()` as before; SessionManager is opt-in for new code
- **Clean separation**: Catalog pool and query sessions have independent lifecycles

### Negative
- **No connection pooling yet**: Each query still creates/destroys a DuckDB connection. Connection reuse within the pool is a v1.2 enhancement.
- **No query priority**: All queries are treated equally. Priority queuing (e.g., health checks before analytics) is deferred.

### Migration Path
- v1.1: SessionManager available as opt-in; existing bridges continue working unchanged
- v1.2: Query bridges migrate to SessionManager; connection reuse added
- v2.0: Consider external OLAP engine abstraction (ClickHouse/StarRocks) if DuckDB limits are hit

## Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `arrow_lake_duckdb_pool_active_sessions` | Gauge | — | Currently active sessions |
| `arrow_lake_duckdb_pool_queued_requests` | Gauge | — | Waiting requests |
| `arrow_lake_duckdb_pool_total_queries` | Counter | — | Total queries executed |
| `arrow_lake_duckdb_pool_total_errors` | Counter | — | Total query errors |
| `arrow_lake_duckdb_pool_total_timeouts` | Counter | — | Session acquisition timeouts |
| `arrow_lake_duckdb_pool_slow_queries` | Counter | — | Queries exceeding threshold |
