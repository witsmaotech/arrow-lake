# ADR-06: DuckDB OLAP 定位 + DuckLake v1.0 评估

> **Date**: 2026-04-17
> **Author**: Winston (System Architect)
> **Status**: Accepted
> **Supersedes**: ADR-05 (DuckDB OLAP Deviation)

---

## Context

在 v0.1.0 开发过程中，`project-context-zh.md` Rule 6 规定 "DuckDB 仅用于 Catalog，Daft SQL 作为主 OLAP 引擎"。但实际代码实现中，Daft 0.7.8 **不支持 SQL**（`olap.py` 注释明确说明），因此 DuckDB 实际承担了所有 OLAP 查询职责。架构文档与实现存在严重不一致。

同时，DuckDB 1.5.2 发布了 DuckLake v1.0 扩展，需要评估其是否适合集成到 Arrow Lake 中。

## Decision

### 决策 1：DuckDB 正式成为 OLAP + Catalog 引擎

- DuckDB 升级到 1.5.2
- DuckDB 负责：OLAP SQL 分析、分面搜索（CUBE）、元数据 CRUD
- Daft 负责非 SQL 的表达式式 DataFrame 查询（`read_lance → select/filter/sort/groupby`）
- 连接池调整为 6 读 + 2 写（同时支撑 OLAP 和 Catalog 工作负载）

### 决策 2：DuckLake v1.0 **不集成**

**理由**：

1. **DuckLake 是存储格式，不是 OLAP 加速器**。DuckLake = Parquet 数据文件 + SQL 目录数据库。它不能增强 DuckDB 对 Lance 数据的查询能力。

2. **DuckLake 与 Lance 是竞争关系，不是互补关系**：
   - DuckLake 使用 Parquet 格式，Lance 使用 Lance 列式格式
   - 采用 DuckLake 需要放弃 Lance 的原生向量索引（IVF-PQ）
   - 采用 DuckLake 需要放弃 Lance 的原生全文搜索（Tantivy）
   - Arrow Lake 的核心价值（多模态数据湖库）依赖 Lance 的这些能力

3. **DuckLake 是 DuckDB 扩展，不是 Python 依赖**。无需修改 `pyproject.toml`，通过 SQL 加载：
   ```sql
   INSTALL ducklake;
   LOAD ducklake;
   ```

4. **DuckLake 适合作为未来导出目标**。在 Phase 4 "External Connectors" 中可评估 `Lake.export_to_ducklake()` 方法，将分析数据同步到 DuckLake 格式供 BI 工具使用。

### 决策 3：文档修正

以下文件已更新：

| 文件 | 修正内容 |
|------|---------|
| `project-context-zh.md` | Rule 6/10/11/31 + 扩展层表 + 反模式 |
| `v0.2.0-roadmap-design.md` | 架构图 DuckDB 标签 `(OLAP+Catalog)` |
| `v0.2.0-roadmap-design-zh.md` | 架构图 DuckDB 标签 `(OLAP+目录)` |
| `pyproject.toml` | `duckdb==1.5.1` → `duckdb==1.5.2` |

## DuckLake vs Lance 对比

| 维度 | Lance (当前) | DuckLake v1.0 |
|------|-------------|---------------|
| **存储格式** | Lance 列式 | Parquet |
| **向量搜索** | 原生 IVF-PQ | **不内置** |
| **全文搜索** | 原生 Tantivy | **不内置** |
| **OLAP SQL** | 需 DuckDB bridge | **原生 SQL** |
| **ACID 事务** | 有限 | 完整 |
| **Schema 演化** | add_columns | 完整 (ALTER TABLE) |
| **时间旅行** | 内建 | 快照式 |
| **Data Inlining** | 无 | 有 (≤10 行直接写目录) |
| **外部生态** | 较小 | Spark, Trino, DataFusion, Pandas |

## 未来路径

```
Lance (主存储) ──export_to_ducklake()──→ DuckLake (Parquet + SQL Catalog)
  ↕                                      ↕
向量搜索 / FTS / 版本管理               BI 工具 / Spark / Trino / DataFusion
```

建议在 v0.2.0 Phase 4 "External Connectors" 中评估 DuckLake 导出能力。

## References

- DuckLake v1.0 发布博客：https://ducklake.select/2026/04/13/ducklake-10/
- ADR-05：DuckDB OLAP Deviation（`docs/adr-05-duckdb-olap-deviation.md`）
- project-context-zh.md Rule 6
- `arrow_lake/query/olap.py` Line 14-16
