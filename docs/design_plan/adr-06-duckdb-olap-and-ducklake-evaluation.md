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

### 决策 2：DuckLake v1.0 **不作为主存储，但作为可写衍生层集成** (2026-04-20 修订)

**原始结论 (2026-04-17)**：DuckLake 不集成。理由：DuckLake 是存储格式不是 OLAP 加速器，与 Lance 竞争。

**修订结论 (2026-04-20)**：经实验验证，DuckLake 与 Lance 不是竞争关系，而是**分层互补关系**：

1. **DuckDB Lance 扩展消除架构分歧**。DuckDB 1.5.2 内置 `lance` 扩展，可直接在 Lance 文件上执行 SQL（`__lance_scan`），无需 PyArrow 中间层。这意味着 DuckDB 可以同时操作 Lance 和 DuckLake，两者在同一 SQL 会话中无缝协作。

2. **Lance vs DuckLake 定位完全不同**：
   - **Lance = 只读 SSOT**：原始数据存储，向量索引（IVF-PQ），全文搜索（Tantivy），版本管理
   - **DuckLake = 可写衍生层**：ETL 物化结果，工作区暂存，支持完整 DML（INSERT/UPDATE/DELETE），快照时间旅行
   - 两者通过 DuckDB SQL 联合查询（JOIN），不需要数据复制

3. **已验证的联合架构**：
   ```sql
   LOAD lance; LOAD ducklake;
   -- Lance 只读 SSOT
   CREATE VIEW lance_data AS SELECT * FROM __lance_scan('/path/to/lance', explain_verbose := false);
   -- DuckLake 可写工作区
   ATTACH '/path/to/ducklake' AS workspace (TYPE ducklake);
   -- 物化聚合到 DuckLake
   CREATE TABLE workspace.stats AS SELECT category, AVG(score) FROM lance_data GROUP BY category;
   -- DuckLake DML (Lance 不支持)
   INSERT INTO workspace.stats VALUES ('new', 99.9);
   UPDATE workspace.stats SET score = score * 1.1 WHERE category = 'A';
   -- 跨存储 JOIN
   SELECT l.*, s.avg FROM lance_data l JOIN workspace.stats s ON l.category = s.category;
   ```

4. **DuckLake 不替代 Lance 的向量/FTS 能力**。DuckLake 使用 Parquet 格式，不内置向量索引或全文搜索。向量搜索和 FTS 继续通过 Lance 扩展原生函数实现：`lance_vector_search()`, `lance_fts()`, `lance_hybrid_search()`。

**原决策 2 的第 2 点（竞争关系）已不成立。Lance 和 DuckLake 在 DuckDB 统一 SQL 引擎下是互补的。**

> **保留原决策第 1/3/4 点**：DuckLake 是存储格式而非 OLAP 加速器（正确），DuckLake 通过 SQL 扩展加载（正确），DuckLake 适合作为导出目标（仍然有效，但不再需要 `export_to_ducklake()` — DuckDB SQL 直接完成）。

### 决策 3：文档修正

以下文件已更新：

| 文件 | 修正内容 |
|------|---------|
| `project-context-zh.md` | Rule 6/10/11/31 + 扩展层表 + 反模式 |
| `v0.2.0-roadmap-design.md` | 架构图 DuckDB 标签 `(OLAP+Catalog)` |
| `v0.2.0-roadmap-design-zh.md` | 架构图 DuckDB 标签 `(OLAP+目录)` |
| `pyproject.toml` | `duckdb==1.5.1` → `duckdb==1.5.2` |

## DuckLake vs Lance 对比

| 维度 | Lance (只读 SSOT) | DuckLake v1.0 (可写衍生) |
|------|-------------------|------------------------|
| **存储格式** | Lance 列式 | Parquet |
| **读写模式** | 只读 (via DuckDB Lance 扩展) | 完整读写 (INSERT/UPDATE/DELETE) |
| **向量搜索** | 原生 IVF-PQ (`lance_vector_search`) | **不内置** |
| **全文搜索** | 原生 Tantivy (`lance_fts`) | **不内置** |
| **OLAP SQL** | DuckDB `__lance_scan` 直接读取 | DuckDB `ducklake_scan` 原生 SQL |
| **混合搜索** | 原生 RRF (`lance_hybrid_search`) | **不内置** |
| **ACID 事务** | 有限 (版本化写入) | 完整 (WAL + snapshot) |
| **Schema 演化** | add_columns | 完整 (ALTER TABLE) |
| **时间旅行** | 内建 (版本管理) | 快照式 (`ducklake_snapshots`) |
| **外部生态** | 较小 | Spark, Trino, DataFusion, Pandas |
| **适用场景** | 原始数据、向量、全文索引 | ETL 物化、工作区、BI 导出 |

## DuckDB Lance 扩展能力 (2026-04-20 新增)

DuckDB 1.5.2 内置 Lance 扩展 (`INSTALL lance; LOAD lance`)，提供以下原生 SQL 函数：

| 函数 | 类型 | 用途 |
|------|------|------|
| `__lance_scan(uri, explain_verbose)` | Table Function | 直接 SQL 查询 Lance 数据集 |
| `lance_vector_search(uri, col, vec, ...)` | Table Function | 向量相似度搜索 (IVF-PQ) |
| `lance_fts(uri, col, query, ...)` | Table Function | 全文搜索 (Tantivy) |
| `lance_hybrid_search(uri, vec_col, fts_col, ...)` | Table Function | 混合搜索 (RRF 融合) |
| `__lance_optimize_index(uri)` | Table Function | 优化向量索引 |
| `__lance_compact_files(uri)` | Table Function | 压缩 Lance 数据文件 |

**注意**: `__lance_scan` 是内部 API（双下划线），DuckDB 未来可能提供公开替代。`ATTACH ... TYPE lance` 在 DuckDB 1.5.2 中存在表发现问题，建议使用 `CREATE VIEW ... AS SELECT FROM __lance_scan(...)` 替代。

## 未来路径 (2026-04-20 修订)

```
              DuckDB (统一 SQL 引擎)
             ┌──────────────────────────┐
             │  lance 扩展               │
             │  · __lance_scan() → OLAP  │
             │  · lance_vector_search()  │
             │  · lance_fts()            │
             │  · lance_hybrid_search()  │
             ├──────────┬───────────────┤
        ┌────▼────┐  ┌──▼──────────┐
        │  Lance  │  │  DuckLake   │
        │ (只读   │  │  (可写      │
        │  SSOT)  │  │   衍生层)   │
        │ 向量/FTS│  │  Parquet    │
        │ 版本管理│  │  DML/快照   │
        └─────────┘  └─────────────┘
             ↓              ↓
        原始数据存储     ETL/物化/工作区
        向量+全文搜索    BI 工具导出
```

**修订说明**：
- 原 "export_to_ducklake()" 方案已不需要 — DuckDB SQL `CREATE TABLE ... AS SELECT` 直接完成物化
- DuckLake 在 v1.0 M0 阶段即开始集成，不需要等到 Phase 4
- MotherDuck 仍然是水平扩展路径（v1.1+），与 Lance/DuckLake 架构不冲突

## References

- DuckLake v1.0 发布博客：https://ducklake.select/2026/04/13/ducklake-10/
- ADR-05：DuckDB OLAP Deviation（`docs/adr-05-duckdb-olap-deviation.md`）
- project-context-zh.md Rule 6
- `arrow_lake/query/olap.py` Line 14-16
