# ADR-05: DuckDB OLAP 偏差与迁移路线

**状态：** 已批准
**日期：** 2026-04-14
**决策者：** Winston (Architect)

## 背景

project-context Rule 6 原始定义：

> DuckDB is Catalog-only — DuckDB embedded in CatalogActor for metadata storage. Do NOT use DuckDB for OLAP queries. Use Daft SQL as the primary OLAP engine.

这一决策基于以下假设：
- Daft 提供 SQL 查询能力作为主 OLAP 引擎
- DuckDB 仅用于 Catalog 元数据存储

## 现实偏差

Tech Spike (Story 1.2) 验证发现：

| 验证项 | 结果 |
|--------|------|
| `daft.sql(...)` | 不存在 |
| `daft.read_sql(...)` | 从 SQL 数据库读取，不支持 Lance 文件 |
| `df.groupby().agg()` | 可用（编程 API） |

**Daft 0.7.8 无 SQL 透传能力。** 因此 Story 5.4 OLAP Analytics 实际使用 DuckDB 执行 SQL 查询：

```
Lance → Arrow Table → DuckDB register → SQL → Arrow result
```

这与 Rule 6 的指令直接矛盾。

## 优化（2026-04-15）

原始路径将整个 Lance 数据集物化为 Arrow Table 后注册到 DuckDB，对大内存场景不友好。

**已实现 `scan_dataset()` 流式读取：**

```
Lance → RecordBatchReader → DuckDB register → SQL → Arrow result
```

- `LanceStorageManager.scan_dataset()` 返回 `pa.RecordBatchReader`，支持列投影、过滤、自定义 batch_size
- `OlapSearchBridge` 自动检测 JOIN / 子查询场景（需多次扫描同一表），降级为全量读取
- 配置项：`OlapConfig.enable_streaming`（默认 True）+ `OlapConfig.scanner_batch_size`（默认 10,000）

**局限：** 当前仍返回完整 `pa.Table` 结果（非流式输出）。输入侧流式已实现，输出侧流式仍推迟到 Phase 2（见架构文档 H4）。

## 决策

**当前使用 DuckDB 执行 OLAP SQL 查询是已知妥协。**

理由：
1. DuckDB 的 `conn.register()` 支持 Arrow zero-copy，性能可接受
2. DuckDB 完整支持 GROUP BY、窗口函数、HAVING 等 OLAP 操作
3. MetadataSearchBridge (Story 3.9) 已建立相同模式，保持一致性
4. 数据规模在单节点 10M 行级别，DuckDB 单机足够

## 迁移路线

| 阶段 | 条件 | 行动 |
|------|------|------|
| 当前 | Daft 无 SQL 能力 | DuckDB 执行 OLAP |
| 近期 | Daft 新版本支持 `df.sql()` | 评估迁移可行性 |
| 中期 | 数据量超过单节点阈值 | 切换到 Daft 分布式 OLAP |
| 远期 | Daft SQL 成熟 | 完全移除 OLAP 路径中的 DuckDB |

## 影响范围

- `arrow_lake/query/olap.py` — 使用 DuckDB 执行 SQL
- `arrow_lake/query/metadata.py` — 使用 DuckDB 执行 SQL（Story 3.9，同样偏差）
- `project-context.md` Rule 6 — 需更新为反映当前现实
- `ArrowCopyDetector` — 未集成到 OLAP 查询路径（当前可接受）

## 替代方案

1. **纯 Daft 编程 API** — `df.groupby().agg()` 可用但无 SQL 字符串接口，用户体验差
2. **SQL 解析器转 Daft API** — 自建 SQL→Daft 翻译层，工程量大，维护成本高
3. **引入第三方 SQL 引擎** — 增加依赖，与 DARMU 栈原则冲突
