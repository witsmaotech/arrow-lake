# OLAP 分析查询

> 版本：1.10.0

Arrow Lake 通过 DuckDB 零拷贝 Arrow 集成提供高性能 OLAP 分析能力，支持
GROUP BY 聚合、窗口函数、JOIN 以及物化视图。

> **贯穿数据集**：本章使用 `ontime` 航班数据集（`datas/ontime/ontime_2022.parquet`，约 160 万行 × 109 列）做结构化 OLAP 分析：聚合到达延误 `ArrDelay`、按航空公司 / 机场 / 月份切片、用窗口函数排名。

> 前置准备：确保已安装依赖 `pip install arrow-lake[olap]`，并有一个
> 已写入数据的 Lance 数据集。

***

## 1. 基础 SQL 查询

`Lake.olap_query()` 在 Lance 数据集上执行只读 SQL（SELECT 语句），返回
`OlapQueryResult`，其中 `.table` 是 PyArrow Table，可直接转 Pandas。

> 本章示例查询 `ontime` 航班数据集。先用下面代码载入（若你已有自己的数据集，把下文 `"ontime"` 换成你的数据集名即可）：

```python
import pyarrow.parquet as pq
from arrow_lake import Lake

lake = Lake(base_uri="./data")

# 载入 ontime 航班数据（OLAP 无需向量列）
ontime = pq.read_table("datas/ontime/ontime_2022.parquet")
lake.create_dataset("ontime", ontime)
print(f"ontime 已载入: {ontime.num_rows} 行")
```

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data")

# 按航空公司汇总平均到达延误与航班数
result = lake.olap_query(
    "ontime",
    "SELECT Reporting_Airline, AVG(ArrDelay) AS avg_delay, COUNT(*) AS cnt "
    "FROM ontime WHERE Cancelled = 0 GROUP BY Reporting_Airline ORDER BY avg_delay DESC",
)
print(f"返回 {result.row_count} 行，{result.column_count} 列")
print(result.table.to_pandas())
```

通过 `max_rows` 参数限制返回行数，防止内存溢出：

```python
result = lake.olap_query(
    "ontime",
    "SELECT * FROM ontime",
    max_rows=500,  # 最多返回 500 行
)
```

> **注意**：`Lake.sql_query()` 是更低层的替代方法，直接返回 `pa.Table`（不包装为
> `OlapQueryResult`）。`Lake.query()` 返回 `MetadataQueryResult`，用于元数据查询。
> 需要行/列计数和元数据时使用 `olap_query()`，只需原始 Arrow 表时使用 `sql_query()`。

***

## 2. 窗口函数

DuckDB 支持完整的窗口函数语法，适合排名、累计求和、环比等场景。

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

常见窗口函数速查：

| 函数               | 用途       | 示例                                                                          |
| ---------------- | -------- | --------------------------------------------------------------------------- |
| `ROW_NUMBER()`   | 行编号（不重复） | `ROW_NUMBER() OVER (ORDER BY ArrDelay DESC)`                                  |
| `RANK()`         | 排名（并列跳号） | `RANK() OVER (ORDER BY ArrDelay DESC)`                                        |
| `SUM() OVER`     | 累计求和     | `SUM(ArrDelay) OVER (ORDER BY Month)`                                          |
| `AVG() OVER`     | 移动平均     | `AVG(ArrDelay) OVER (ORDER BY Month ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)` |
| `LAG() / LEAD()` | 前/后值偏移   | `LAG(ArrDelay, 1) OVER (ORDER BY Month)`                                       |

***

## 3. JOIN 多表查询

当配置 `enable_join=True` 时（默认开启），可传入额外的 Arrow Table 进行
JOIN 操作。

```python
import pyarrow as pa

# 构造航空公司维度表（code 必须与 ontime.Reporting_Airline 一致）
airline_info = pa.table({
    "Reporting_Airline": ["AA", "DL", "UA", "WN", "B6", "AS"],
    "airline_name": ["美国航空", "达美航空", "美联航", "西南航空", "捷蓝航空", "阿拉斯加航空"],
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
    tables={"airline_info": airline_info},  # 注册临时表供 JOIN 使用
)
print(result.table.to_pandas())
```

> 传入的 `tables` 字典键名必须符合标识符规范（字母/下划线开头，不含特殊字符）。

***

## 4. 物化视图

`Lake.materialize()` 将查询结果持久化为 DuckLake 表，设置 TTL 自动过期。
适合缓存高频聚合查询的结果。

**配置要求**：需要在配置中启用 `ducklake_enabled=True`：

```python
from arrow_lake.config import ArrowLakeConfig

config = ArrowLakeConfig()
config.olap.ducklake_enabled = True
config.olap.ducklake_ttl_days = 7

lake = Lake(base_uri="./data", config=config)
```

**创建物化视图**：

```python
view_name = lake.materialize(
    "ontime",
    "SELECT Reporting_Airline, AVG(ArrDelay) AS avg_delay FROM ontime WHERE Cancelled = 0 GROUP BY Reporting_Airline",
    view_name="airline_delay_summary",
    ttl_days=7,
)
print(f"物化视图已创建：{view_name}")
```

参数说明：

| 参数              | 类型            | 说明                                          |
| --------------- | ------------- | ------------------------------------------- |
| `dataset_name`  | `str`         | 源 Lance 数据集名称                               |
| `sql`           | `str`         | SELECT 查询语句                                 |
| `view_name`     | `str \| None` | 物化表名，`None` 则自动生成 `_materialized_{dataset}` |
| `ttl_days`      | `int \| None` | 过期天数，`None` 使用配置默认值（默认 7 天）                 |
| `max_join_rows` | `int \| None` | 行数预算上限，`None` 使用配置默认值                       |

***

## 5. 清理过期物化视图

定期清理超过 TTL 的物化表，释放存储空间：

```python
dropped = lake.cleanup_materialized()
print(f"已清理 {len(dropped)} 个过期视图：{dropped}")

# 自定义 TTL 阈值（只清理超过 3 天的）
dropped = lake.cleanup_materialized(ttl_days=3)
```

建议在定时任务中调用此方法，或在应用启动时执行一次清理。

**REST 管理端点**：物化视图是全局资源，通过独立路由 `/api/v1/materialized` 管理（非 per-dataset，避免与 `datasets` 的 `GET /{name}` 冲突）。所有端点需 ADMIN 角色，且 `ducklake_enabled=True`（未启用返回 503）：

| 方法 + 路径                         | 说明                                   |
| ------------------------------- | ------------------------------------ |
| `GET /api/v1/materialized`      | 列出所有物化视图及生命周期元数据                     |
| `DELETE /api/v1/materialized/{view}` | 按名称删除单个物化视图                       |
| `POST /api/v1/materialized/cleanup` | 批量清理所有已过期的物化视图（TTL）               |

***

## 6. 查询计划分析

使用 `EXPLAIN` 查看查询执行计划，辅助性能调优：

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

> 当前 `OlapSearchBridge` 提供 `explain()` 方法用于更直接的 EXPLAIN 分析，
> 可通过底层 bridge 调用。

***

## 7. Daft DataFrame 查询

`Lake.daft_query()` 返回 `LazyDaftFrame`，支持链式延迟操作。Daft 不支持
SQL，但提供表达式风格的 DataFrame API。

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data")

# 加载为延迟 Daft DataFrame，支持列选择和过滤
df = lake.daft_query("ontime")
df_filtered = lake.daft_query(
    "ontime",
    columns=["Reporting_Airline", "Origin", "Dest", "ArrDelay", "Distance"],
    filter="ArrDelay > 60",
    limit=1000,
)

# 链式操作：选择列 -> 过滤 -> 排序 -> 收集
result = (
    df.select("Reporting_Airline", "ArrDelay", "Distance")
    .filter("ArrDelay > 120")
    .sort("ArrDelay", desc=True)
    .collect()  # 执行并返回 PyArrow Table
)
print(result.to_pandas())
```

**分组聚合**：

```python
import daft

grouped = df.select("Reporting_Airline", "ArrDelay").groupby("Reporting_Airline")
# 应用聚合表达式获得具体结果
agg_result = grouped.agg(
    daft.col("ArrDelay").mean().alias("avg_delay"),
    daft.col("ArrDelay").count().alias("count"),
)
print(agg_result.collect().to_pandas())
```

**多表 JOIN**：

```python
import daft
import pyarrow as pa

df1 = lake.daft_query("ontime")
# 一张小维度表（Origin 机场 → 枢纽城市）作为 Daft DataFrame
airport_dim = daft.from_arrow(pa.table({
    "Origin": ["JFK", "LAX", "ORD", "ATL", "DFW"],
    "hub": ["纽约", "洛杉矶", "芝加哥", "亚特兰大", "达拉斯"],
}))

joined = df1.join(airport_dim, on="Origin", how="inner")
result = joined.collect()
print(result.to_pandas())
```

`daft_query()` 参数说明：

| 参数       | 类型                  | 说明               |
| ---------- | ------------------- | ---------------- |
| `columns`  | `list[str] \| None` | 只选择这些列          |
| `filter`   | `str \| None`       | SQL 风格的过滤表达式    |
| `limit`    | `int \| None`       | 返回的最大行数         |

`LazyDaftFrame` 支持的操作：

| 方法                     | 说明                | 示例                                  |
| ---------------------- | ----------------- | ----------------------------------- |
| `select(*columns)`     | 选择列               | `df.select("Reporting_Airline", "ArrDelay")`       |
| `filter(predicate)`    | 过滤行               | `df.filter("ArrDelay > 120")`         |
| `sort(column, desc)`   | 排序                | `df.sort("Month", desc=True)`        |
| `groupby(*columns)`    | 分组                | `df.groupby("Reporting_Airline")`            |
| `join(other, on, how)` | 连接                | `df.join(df2, on="Origin", how="left")` |
| `pivot(group_by, pivot_col, value_col, agg_fn)` | 透视（长转宽，交叉表） | `df.pivot("Reporting_Airline", "Origin", "ArrDelay", "sum")` |
| `unpivot(ids, values)` | 逆透视（宽转长，melt）   | `df.unpivot("id", ["q1","q2"])`     |
| `collect()`            | 执行并返回 Arrow Table | `df.collect()`                      |

***

## 8. 导出数据

`Lake.export()` 将数据集导出为 Parquet 或 CSV 文件，支持列选择、版本指定
和压缩配置。返回 `ExportResult`。

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data")

# 导出为 Parquet（自动从后缀推断格式）
result = lake.export("ontime", "output/ontime_export.parquet")
print(f"导出完成：{result}")  # ExportResult 包含 path, format, row_count

# 导出指定列
result = lake.export(
    "ontime",
    "output/ontime_summary.csv",
    columns=["Reporting_Airline", "Origin", "Dest", "ArrDelay", "Distance"],
    format="csv",
)

# 导出特定版本 + 压缩
result = lake.export(
    "ontime",
    "output/ontime_v1.parquet",
    version=1,
    compression="snappy",
    overwrite=True,
)
```

参数说明：

| 参数             | 类型                  | 说明                                 |
| -------------- | ------------------- | ---------------------------------- |
| `dataset_name` | `str`               | 源数据集名称                             |
| `output_path`  | `str`               | 输出文件路径（.parquet 或 .csv）            |
| `format`       | `str \| None`       | 导出格式，`None` 自动从路径后缀推断              |
| `columns`      | `list[str] \| None` | 只导出指定列                             |
| `version`      | `int \| None`       | 数据集版本号，`None` 使用最新版                |
| `compression`  | `str \| None`       | Parquet 压缩编码（snappy, gzip, zstd 等） |
| `overwrite`    | `bool`              | 是否覆盖已有文件（默认 `False`）               |

**异步导出（REST）**：通过 `POST /api/v1/datasets/{name}/export` 异步导出大数据集，立即返回 `202` + `task_id`，随后用 `GET /{name}/export/{task_id}/status` 轮询状态、`GET /{name}/export/{task_id}/download` 下载结果。请求体 `ExportRequest` 必填 `output_path`（相对路径，禁止 `..` / 绝对路径 / 空字节）。

**多目标导出**：`POST /api/v1/datasets/{name}/export-to`（同步）通过 Daft 将数据集导出到外部目标，支持 `parquet` / `csv` / `json` / `iceberg` / `clickhouse` 五种格式，请求体 `target_uri` + `format` 必填。

***

## 9. OLAP 配置参考

通过 `ArrowLakeConfig.olap` 精调分析引擎行为：

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

| 配置项                         | 默认值       | 说明                        |
| --------------------------- | --------- | ------------------------- |
| `max_result_rows`           | `100,000` | 查询最大返回行数                  |
| `enable_predicate_pushdown` | `True`    | 是否将谓词下推到 Lance            |
| `enable_join`               | `True`    | 是否允许 JOIN 查询              |
| `enable_streaming`          | `True`    | 使用 RecordBatchReader 流式读取 |
| `lance_scan_mode`           | `"auto"`  | Lance 扫描模式                |
| `max_query_memory_mb`       | `512`     | 单查询内存上限（MB）               |
| `max_concurrent_queries`    | `4`       | 最大并发查询数                   |
| `query_timeout_seconds`     | `300`     | 查询超时时间（秒）                 |
| `ducklake_enabled`          | `False`   | 是否启用 DuckLake 物化视图        |
| `ducklake_ttl_days`         | `7`       | 物化视图默认 TTL（天）             |

***

## 10. 错误处理

```python
from arrow_lake import Lake, QueryError

lake = Lake(base_uri="./data")

try:
    result = lake.olap_query("ontime", "DELETE FROM ontime WHERE 1=1")
except QueryError as e:
    if e.error_code.name == "OLAP_QUERY_FAILED":
        print(f"查询失败：{e.message}")
    elif e.error_code.name == "QUERY_JOIN_NOT_ALLOWED":
        print("JOIN 查询未启用，请在配置中设置 enable_join=True")
    else:
        print(f"未知错误：{e}")
```

OLAP 桥接器只允许 SELECT 语句，会自动拦截 DML/DDL 以及包含分号的多语句输入。
