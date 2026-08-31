# OLAP Analytics Queries

> Version: 1.11.4

Arrow Lake delivers high-performance OLAP analytics through DuckDB's zero-copy Arrow
integration, supporting GROUP BY aggregation, window functions, JOINs, and materialized
views.

> **Running dataset.** This chapter uses the `ontime` flight dataset (`datas/ontime/ontime_2022.parquet`, ~1.6M rows × 109 columns) for structured OLAP analysis: aggregating arrival delay `ArrDelay`, slicing by airline / airport / month, and ranking flights with window functions.

> Prerequisites: install the OLAP extra with `pip install arrow-lake[olap]` and ensure
> you have a Lance dataset with data already written.

***

## 1. Basic SQL Queries

`Lake.olap_query()` executes read-only SQL (SELECT statements) against a Lance dataset
and returns an `OlapQueryResult` whose `.table` is a PyArrow Table that can be directly
converted to Pandas.

> The examples in this chapter query the `ontime` flight dataset. Load it first with the code below (if you already have your own dataset, just replace `"ontime"` with its name):

```python
import pyarrow.parquet as pq
from arrow_lake import Lake

lake = Lake(base_uri="./data")

# Load the ontime flight data (OLAP needs no vector column)
ontime = pq.read_table("datas/ontime/ontime_2022.parquet")
lake.create_dataset("ontime", ontime)
print(f"ontime loaded: {ontime.num_rows} rows")
```

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data")

# Average arrival delay and flight count per airline
result = lake.olap_query(
    "ontime",
    "SELECT Reporting_Airline, AVG(ArrDelay) AS avg_delay, COUNT(*) AS cnt "
    "FROM ontime WHERE Cancelled = 0 GROUP BY Reporting_Airline ORDER BY avg_delay DESC",
)
print(f"Returned {result.row_count} rows, {result.column_count} columns")
print(result.table.to_pandas())
```

Use the `max_rows` parameter to cap the number of returned rows and prevent OOM:

```python
result = lake.olap_query(
    "ontime",
    "SELECT * FROM ontime",
    max_rows=500,  # Return at most 500 rows
)
```

> **Note**: `Lake.sql_query()` is a lower-level alternative that returns a `pa.Table`
> directly (without the `OlapQueryResult` wrapper). `Lake.query()` returns a
> `MetadataQueryResult` for metadata-oriented queries. Use `olap_query()` when you need
> row/column counts and metadata, and `sql_query()` when you just need the raw Arrow table.

***

## 2. Window Functions

DuckDB supports the full window function syntax, ideal for ranking, cumulative sums,
period-over-period comparisons, and more.

```python
result = lake.olap_query(
    "ontime",
    """
    SELECT
        Flight_Number_Reporting_Airline,
        Reporting_Airline,
        Origin,
        Dest,
        ArrDelay,
        ROW_NUMBER() OVER (
            PARTITION BY Reporting_Airline ORDER BY ArrDelay DESC
        ) AS airline_rank,
        SUM(ArrDelay) OVER (
            PARTITION BY Reporting_Airline
        ) AS airline_total,
        ArrDelay - LAG(ArrDelay, 1) OVER (
            PARTITION BY Reporting_Airline ORDER BY ArrDelay DESC
        ) AS diff_from_prev
    FROM ontime
    WHERE Cancelled = 0
    ORDER BY Reporting_Airline, airline_rank
    """,
)
print(result.table.to_pandas())
```

Common window functions at a glance:

| Function         | Purpose                      | Example                                                                     |
| ---------------- | ---------------------------- | --------------------------------------------------------------------------- |
| `ROW_NUMBER()`   | Row number (no ties)         | `ROW_NUMBER() OVER (ORDER BY ArrDelay DESC)`                                  |
| `RANK()`         | Rank with gaps on ties       | `RANK() OVER (ORDER BY ArrDelay DESC)`                                        |
| `SUM() OVER`     | Cumulative sum               | `SUM(ArrDelay) OVER (ORDER BY Month)`                                          |
| `AVG() OVER`     | Moving average               | `AVG(ArrDelay) OVER (ORDER BY Month ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)` |
| `LAG() / LEAD()` | Previous / next value offset | `LAG(ArrDelay, 1) OVER (ORDER BY Month)`                                       |

***

## 3. Multi-Table JOINs

When `enable_join=True` is set (enabled by default), you can pass additional Arrow
Tables for JOIN operations.

```python
import pyarrow as pa

# Build an airline dimension table (code must match ontime.Reporting_Airline)
airline_info = pa.table({
    "Reporting_Airline": ["AA", "DL", "UA", "WN", "B6", "AS"],
    "airline_name": ["American Airlines", "Delta", "United", "Southwest", "JetBlue", "Alaska Airlines"],
})

result = lake.olap_query(
    "ontime",
    """
    SELECT s.Reporting_Airline, c.airline_name,
           AVG(s.ArrDelay) AS avg_delay, COUNT(*) AS cnt
    FROM ontime s
    INNER JOIN airline_info c ON s.Reporting_Airline = c.Reporting_Airline
    WHERE s.Cancelled = 0
    GROUP BY s.Reporting_Airline, c.airline_name
    ORDER BY avg_delay DESC
    """,
    tables={"airline_info": airline_info},  # Register as a temp table for JOIN
)
print(result.table.to_pandas())
```

> The keys in the `tables` dictionary must be valid identifiers (start with a letter
> or underscore, no special characters).

***

## 4. Materialized Views

`Lake.materialize()` persists query results as DuckLake tables with automatic TTL-based
expiration. It is designed for caching the results of frequently run aggregation queries.

**Configuration requirement**: enable `ducklake_enabled=True` in your config:

```python
from arrow_lake.config import ArrowLakeConfig

config = ArrowLakeConfig()
config.olap.ducklake_enabled = True
config.olap.ducklake_ttl_days = 7

lake = Lake(base_uri="./data", config=config)
```

**Creating a materialized view**:

```python
view_name = lake.materialize(
    "ontime",
    "SELECT Reporting_Airline, AVG(ArrDelay) AS avg_delay FROM ontime WHERE Cancelled = 0 GROUP BY Reporting_Airline",
    view_name="airline_delay_summary",
    ttl_days=7,
)
print(f"Materialized view created: {view_name}")
```

Parameter reference:

| Parameter       | Type          | Description                                                              |
| --------------- | ------------- | ------------------------------------------------------------------------ |
| `dataset_name`  | `str`         | Source Lance dataset name                                                |
| `sql`           | `str`         | SELECT query statement                                                   |
| `view_name`     | `str \| None` | Materialized table name; `None` auto-generates `_materialized_{dataset}` |
| `ttl_days`      | `int \| None` | Days until expiration; `None` uses the config default (default 7)        |
| `max_join_rows` | `int \| None` | Row budget ceiling; `None` uses the config default                       |

***

## 5. Cleaning Up Expired Materialized Views

Periodically remove materialized tables that have exceeded their TTL to free storage:

```python
dropped = lake.cleanup_materialized()
print(f"Cleaned up {len(dropped)} expired views: {dropped}")

# Custom TTL threshold (only clean up views older than 3 days)
dropped = lake.cleanup_materialized(ttl_days=3)
```

It is recommended to call this method in a scheduled job or at application startup.

**REST management endpoints**: Materialized views are global resources managed via a dedicated `/api/v1/materialized` router (not per-dataset, to avoid clashing with the `datasets` `GET /{name}` route). All endpoints require the ADMIN role and `ducklake_enabled=True` (returns 503 otherwise):

| Method + Path                       | Description                                          |
| ----------------------------------- | ---------------------------------------------------- |
| `GET /api/v1/materialized`          | List all materialized views with lifecycle metadata  |
| `DELETE /api/v1/materialized/{view}` | Drop a single materialized view by name             |
| `POST /api/v1/materialized/cleanup` | Drop all expired materialized views (TTL-based)      |

***

## 6. Query Plan Analysis

Use `EXPLAIN` to inspect the query execution plan for performance tuning:

```python
explain_output = lake.olap_query(
    "ontime",
    """
    EXPLAIN
    SELECT Reporting_Airline, AVG(ArrDelay) AS avg_delay
    FROM ontime
    WHERE Cancelled = 0
    GROUP BY Reporting_Airline
    """,
)
```

> The `OlapSearchBridge` also exposes an `explain()` method for more direct EXPLAIN
> analysis, accessible through the underlying bridge.

***

## 7. Daft DataFrame Queries

`Lake.daft_query()` returns a `LazyDaftFrame` that supports chained lazy operations.
Daft does not support SQL but provides an expression-style DataFrame API.

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data")

# Load as a lazy Daft DataFrame with optional column selection and filtering
df = lake.daft_query("ontime")
df_filtered = lake.daft_query(
    "ontime",
    columns=["Reporting_Airline", "Origin", "Dest", "ArrDelay", "Distance"],
    filter="ArrDelay > 60",
    limit=1000,
)

# Chained operations: select -> filter -> sort -> collect
result = (
    df.select("Reporting_Airline", "ArrDelay", "Distance")
    .filter("ArrDelay > 120")
    .sort("ArrDelay", desc=True)
    .collect()  # Execute and return a PyArrow Table
)
print(result.to_pandas())
```

**Grouped aggregation**:

```python
import daft

grouped = df.select("Reporting_Airline", "ArrDelay").groupby("Reporting_Airline")
# Apply aggregation expressions to get a concrete result
agg_result = grouped.agg(
    daft.col("ArrDelay").mean().alias("avg_delay"),
    daft.col("ArrDelay").count().alias("count"),
)
print(agg_result.collect().to_pandas())
```

**Multi-table JOIN**:

```python
import daft
import pyarrow as pa

df1 = lake.daft_query("ontime")
# A small dimension table (Origin airport → hub city) as a Daft DataFrame
airport_dim = daft.from_arrow(pa.table({
    "Origin": ["JFK", "LAX", "ORD", "ATL", "DFW"],
    "hub": ["New York", "Los Angeles", "Chicago", "Atlanta", "Dallas"],
}))

joined = df1.join(airport_dim, on="Origin", how="inner")
result = joined.collect()
print(result.to_pandas())
```

`daft_query()` parameter reference:

| Parameter  | Type                | Description                       |
| ---------- | ------------------- | --------------------------------- |
| `columns`  | `list[str] \| None` | Select only these columns         |
| `filter`   | `str \| None`       | SQL-style filter expression       |
| `limit`    | `int \| None`       | Maximum number of rows to return  |

Available `LazyDaftFrame` operations:

| Method                 | Description                    | Example                             |
| ---------------------- | ------------------------------ | ----------------------------------- |
| `select(*columns)`     | Select columns                 | `df.select("Reporting_Airline", "ArrDelay")`       |
| `filter(predicate)`    | Filter rows                    | `df.filter("ArrDelay > 120")`         |
| `sort(column, desc)`   | Sort                           | `df.sort("Month", desc=True)`             |
| `groupby(*columns)`    | Group                          | `df.groupby("Reporting_Airline")`                 |
| `join(other, on, how)` | Join                           | `df.join(df2, on="Origin", how="left")`   |
| `pivot(group_by, pivot_col, value_col, agg_fn)` | Pivot (long-to-wide, cross-tab) | `df.pivot("Reporting_Airline", "Origin", "ArrDelay", "sum")` |
| `unpivot(ids, values)` | Unpivot (wide-to-long, melt)   | `df.unpivot("id", ["q1","q2"])`     |
| `collect()`            | Execute and return Arrow Table | `df.collect()`                      |

***

## 8. Exporting Data

`Lake.export()` exports a dataset to Parquet or CSV files with support for column
selection, version pinning, and compression configuration. Returns an `ExportResult`.

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data")

# Export to Parquet (format auto-detected from extension)
result = lake.export("ontime", "output/ontime_export.parquet")
print(f"Export complete: {result}")  # ExportResult with path, format, row_count

# Export specific columns
result = lake.export(
    "ontime",
    "output/ontime_summary.csv",
    columns=["Reporting_Airline", "Origin", "Dest", "ArrDelay", "Distance"],
    format="csv",
)

# Export a specific version with compression
result = lake.export(
    "ontime",
    "output/ontime_v1.parquet",
    version=1,
    compression="snappy",
    overwrite=True,
)
```

Parameter reference:

| Parameter      | Type                | Description                                                |
| -------------- | ------------------- | ---------------------------------------------------------- |
| `dataset_name` | `str`               | Source dataset name                                        |
| `output_path`  | `str`               | Output file path (.parquet or .csv)                        |
| `format`       | `str \| None`       | Export format; `None` auto-detects from the file extension |
| `columns`      | `list[str] \| None` | Export only the specified columns                          |
| `version`      | `int \| None`       | Dataset version number; `None` uses the latest             |
| `compression`  | `str \| None`       | Parquet compression codec (snappy, gzip, zstd, etc.)       |
| `overwrite`    | `bool`              | Whether to overwrite existing files (default `False`)      |

**Async export (REST)**: Export large datasets asynchronously via `POST /api/v1/datasets/{name}/export`, which returns `202` + `task_id` immediately; then poll status with `GET /{name}/export/{task_id}/status` and download the result with `GET /{name}/export/{task_id}/download`. The request body `ExportRequest` requires `output_path` (relative path; `..`, absolute paths, and null bytes are rejected).

**Multi-target export**: `POST /api/v1/datasets/{name}/export-to` (synchronous) exports a dataset to an external target via Daft, supporting five formats: `parquet` / `csv` / `json` / `iceberg` / `clickhouse`. The request body requires `target_uri` + `format`.

***

## 9. OLAP Configuration Reference

Fine-tune the analytics engine behavior via `ArrowLakeConfig.olap`:

```python
from arrow_lake.config import ArrowLakeConfig

config = ArrowLakeConfig()
config.olap.max_result_rows = 500_000
config.olap.enable_join = True
config.olap.enable_streaming = True
config.olap.lance_scan_mode = "auto"       # "auto" | "native" | "pyarrow_fallback"
config.olap.max_query_memory_mb = 1024
config.olap.query_timeout_seconds = 600
config.olap.ducklake_enabled = True
config.olap.ducklake_ttl_days = 7

lake = Lake(base_uri="./data", config=config)
```

| Setting                     | Default   | Description                               |
| --------------------------- | --------- | ----------------------------------------- |
| `max_result_rows`           | `100,000` | Maximum rows a query can return           |
| `enable_predicate_pushdown` | `True`    | Push predicates down to Lance             |
| `enable_join`               | `True`    | Allow JOIN queries                        |
| `enable_streaming`          | `True`    | Use RecordBatchReader for streaming reads |
| `lance_scan_mode`           | `"auto"`  | Lance scan mode                           |
| `max_query_memory_mb`       | `512`     | Per-query memory limit (MB)               |
| `max_concurrent_queries`    | `4`       | Maximum concurrent queries                |
| `query_timeout_seconds`     | `300`     | Query timeout (seconds)                   |
| `ducklake_enabled`          | `False`   | Enable DuckLake materialized views        |
| `ducklake_ttl_days`         | `7`       | Default TTL for materialized views (days) |

***

## 10. Error Handling

```python
from arrow_lake import Lake, QueryError

lake = Lake(base_uri="./data")

try:
    result = lake.olap_query("ontime", "DELETE FROM ontime WHERE 1=1")
except QueryError as e:
    if e.error_code.name == "OLAP_QUERY_FAILED":
        print(f"Query failed: {e.message}")
    elif e.error_code.name == "QUERY_JOIN_NOT_ALLOWED":
        print("JOINs not enabled -- set enable_join=True in your config")
    else:
        print(f"Unknown error: {e}")
```

The OLAP bridge only allows SELECT statements. It automatically intercepts DML/DDL
and multi-statement input containing semicolons.
