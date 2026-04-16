---
project_name: 'arrow-lake'
user_name: 'Witshine'
date: '2026-04-16'
sections_completed:
  ['technology_stack', 'language_rules', 'framework_rules', 'testing_rules', 'quality_rules', 'workflow_rules', 'anti_patterns']
status: 'complete'
rule_count: 45
optimized_for_llm: true
language: 'zh'
---

# AI 代理项目上下文

_本文件包含 AI 代理在项目中实现代码时必须遵循的关键规则和模式。重点列出了代理容易忽略的非显而易见的细节。_

**文档语言：** 英文为主，中文（`*-zh.md`）为补充参考。

**规划文档：**
- PRD: `_bmad-output/planning-artifacts/prd.md`
- 架构: `_bmad-output/planning-artifacts/architecture.md`
- 系统设计: `_bmad-output/planning-artifacts/system_design.md`
- Epic 与 Story（80 个 Story）: `_bmad-output/planning-artifacts/epics.md`
- 实施就绪报告: `_bmad-output/planning-artifacts/implementation-readiness-report-2026-04-11.md`

---

## 技术栈与版本

### 核心栈 (DARMU)

| 组件 | 技术 | 版本 | 角色 |
|------|------|------|------|
| D | Daft | >= 0.7.8 | 多模态 DataFrame 引擎，表达式式查询（Rust 内核） |
| A | Argo Workflows | >= 3.5 | K8s 工作流引擎（生产环境） |
| R | Ray | >= 2.54.1 | 分布式计算（Data/Serve/Actor/ObjectStore） |
| M | Metaflow | >= 2.19.22 | 用户侧工作流编排 |
| U | uv | latest | Python 依赖管理 |

### 扩展层

| 组件 | 版本 | 角色 |
|------|------|------|
| Lance | >= 4.0.0 | 多模态列式存储，向量索引，版本管理 |
| DuckDB | >= 1.5.2 | **OLAP + Catalog** — SQL 查询引擎（OLAP、分面、元数据） |
| NeMo Curator | >= 1.1.0 | 数据质量评分，去重，GPU 加速 |
| Ray Serve | latest | 模型服务，自动伸缩，GPU 管理 |

### 关键依赖

| 依赖 | 版本 | 用途 |
|------|------|------|
| Pydantic | v2 | Schema 定义，Settings，API 模型 |
| structlog | latest | JSON 结构化日志，含 correlation_id |
| tenacity | latest | 指数退避重试逻辑 |
| boto3 | latest | S3/MinIO 交互 |
| prometheus-client | latest | /metrics 端点 |
| Tantivy | via Lance | 全文搜索 |
| PyAV | latest | 视频关键帧提取 |
| SentenceTransformers | latest | 文本嵌入（HuggingFace 本地） |
| torch | latest | 张量运算，pin_memory，CUDA |

### 基础设施

| 环境 | 对象存储 | 编排 | GPU | 监控 |
|------|---------|------|-----|------|
| 开发 | MinIO (Docker) | Docker Compose | 本地（可选） | 仅 CLI |
| 预发布 | MinIO (SSH) | Ray SSH（3-4 节点） | Spot GPU（1-2x） | Prometheus + Grafana |
| 生产 | AWS S3 | K8s + KubeRay | KubeRay GPU 节点 | Prometheus + Grafana |

### 版本锁定策略

- **核心栈（DARMU + Lance + DuckDB + NeMo Curator）：** `>=` 表示已验证的最低版本。Story 1.2 Spike 产出**精确版本锁定文档**（`docs/tech-compatibility.md`），包含固定版本号（如 `daft==0.7.8`、`lancedb==4.0.0`）。Spike 后 `pyproject.toml` 必须对所有核心组件使用精确版本号。
- **辅助库（structlog、tenacity、boto3 等）：** `latest` 或 `>=` 可接受。仅在特定 bugfix 或兼容性问题需要时才锁定。
- **PyArrow：** 必须锁定为 Daft 捆绑的精确版本（在 Story 1.2 中验证）。**不要**使用版本范围——Arrow ABI 变更会导致静默的零拷贝失效。

---

## 关键实施规则

### 语言特定规则（Python）

1. **Python 3.11+** — 由 `uv` 管理，版本锁定在 `.python-version`
2. **所有公共函数和类方法必须添加类型注解**
3. **禁止 `print()` 或裸 `logging`** — 仅使用 `structlog`，JSON 格式输出
4. **Pydantic v2** 用于所有数据模型 — 使用 `model_validate()`，而非 `parse_obj()`
5. **Arrow 类型通过 PyArrow** — `pa.string()`、`pa.float32()`、`pa.list_(pa.float32(), dim)`

### 架构特定规则

6. **DuckDB 作为 OLAP + Catalog 引擎** — DuckDB 负责所有 SQL 查询：OLAP 分析、分面搜索、元数据 CRUD。Daft 用于非 SQL 的 DataFrame 表达式式查询（read_lance、select、filter、sort、groupby）。注意：Daft 0.7.8 不支持 SQL，因此 OLAP 查询统一由 DuckDB 处理。
7. **Arrow 零拷贝是铁律** — 所有组件边界（Lance→Daft、Lance→DuckDB、Lance→PyTorch）必须共享 Arrow 内存缓冲区。中间序列化是 Bug。
8. **Ray Placement Group 必须** — CPU/GPU Worker 必须共置于同一节点。跨节点 Object Store 访问性能下降 100-500 倍。
9. **Catalog Actor 是单例** — Ray Named Actor，带 `resources={"catalog": 1}`。表元数据操作的唯一路由。
10. **QueryEngine 不是 Ray Actor** — 同步类。OLAP 通过 DuckDB SQL（`conn.register()` + `conn.execute()`），Daft 用于表达式式 DataFrame 查询。
11. **连接池：6 读 + 2 写** — 同时支撑 OLAP 分析查询和 Catalog 元数据操作。OLAP 查询以只读方式注册 Lance 表并执行 SQL。
12. **Lance Fragment 大小：128-512MB** — 监控并自动 compact 超出范围的 Fragment。
13. **定时版本清理** — 使用 Metaflow `@schedule` 进行定期版本清理。`production` 标签永久保留。
14. **GPU 成本硬上限** — namespace `ResourceQuota` + Prometheus 预算告警。
15. **Schema 演化：优先 `add_columns`** — 零成本，对比 `alter_columns`（需要重写）。新列必须可空。

### 命名规范

16. **Ray Actor 类** — PascalCase + `Actor` 后缀：`CatalogActor`
17. **Metaflow Flow 类** — PascalCase + `Flow` 后缀：`IngestFlow`
18. **Pydantic 模型** — PascalCase + 语义后缀：`TableSchema`、`IngestConfig`、`QualityReport`
19. **SDK 公共方法** — snake_case：`create_table()`、`list_tables()`
20. **Lance 表名** — snake_case 复数：`user_documents`、`embedding_models`
21. **Lance 列名** — snake_case：`text_content`、`embedding_vector`
22. **常量** — UPPER_SNAKE_CASE：`DEFAULT_CACHE_TTL`、`MAX_FRAGMENT_SIZE_MB`
23. **私有方法** — 单下划线前缀：`_validate_schema()`
24. **Prometheus 指标** — `arrow_lake_{domain}_{metric}_{unit}`：`arrow_lake_ingestion_rows_total`
25. **元数据列** — 下划线前缀：`_source_url`、`_ingested_at`、`_quality_score`

### 包组织结构

```
arrow_lake/
├── __init__.py           # SDK 入口: ArrowLakeClient
├── config.py             # Pydantic Settings（4 层覆盖）
├── exceptions.py         # ArrowLakeError 层次结构
├── metrics.py            # Prometheus 指标定义
├── catalog/              # Catalog 模块
├── ingest/               # 摄入模块（pipeline、sources、validators）
├── quality/              # 质量过滤（filters、dead_letter）
├── embedding/            # 嵌入（encoder、manager）
├── query/                # 查询引擎，vector、fts、hybrid
├── ray_runtime/          # Placement group、cache、health
└── sdk/                  # 公共 API（client、table、search）

flows/                    # Metaflow Flow 定义（在主包外部）
tests/
├── unit/
├── integration/          # Arrow 零拷贝边界测试
├── e2e/
└── conftest.py
configs/                  # YAML 配置（dev.yaml、staging.yaml、prod.yaml）
deploy/                   # Docker、Compose、Helm
```

### 错误处理规则

26. **自定义异常层次** — 所有异常继承自 `ArrowLakeError`。子类：`IngestionError`、`QueryError`、`CatalogError`、`RayRuntimeError`。永远不要抛出裸 `Exception`。
27. **使用 tenacity 重试** — Spot Worker：3 次尝试，指数退避 1-30s。瞬时网络错误：5 次尝试，指数退避 0.5-10s。不可重试（Schema 验证）：不重试，立即抛出。
28. **禁止裸 `except:`** — 始终指定异常类型。永远不要静默吞掉错误。
29. **死信协议** — 被拒绝的行写入 `{table_name}_dead_letter` Lance 表，含 `_rejection_reason` 列。

### 测试规则

30. **3 层测试目录** — `tests/unit/`、`tests/integration/`、`tests/e2e/`
31. **零拷贝边界测试** — 6 个集成测试覆盖所有 Arrow 边界：`test_boundary_lance_daft.py`、`test_boundary_lance_duckdb.py`（OLAP + Catalog 路径）、`test_boundary_duckdb_pytorch.py`（仅 Catalog 路径）、`test_boundary_cpu_gpu.py`、`test_boundary_ray_object_store.py`、`test_boundary_cudf_arrow.py`
32. **测试命名** — 与模块名匹配：`tests/unit/test_catalog_actor.py`
33. **CI 门控（两级）** — **基础 CI**（Story 1.1）：Ruff lint + MyPy strict + `pytest tests/unit/`（仅 CPU），每次 push/PR 执行。**高级 CI**（Story 7.14）：GPU 测试 nightly，部署 PR 执行 Helm chart 验证。基础 CI 必须在 Sprint 1 结束前就绪。
34. **最低 80% 覆盖率** — 通过 CI 强制执行。

### 配置规则

35. **4 层配置覆盖** — 代码默认值 → `.env` → 环境变量 → Metaflow Config YAML
36. **Pydantic Settings** — 自动合并所有 4 层。缺少必需值时快速失败。
37. **仅 YAML 配置** — 禁止 `.json` 配置文件。键使用 snake_case，值带单位后缀（`_mb`、`_seconds`）。
38. **密钥管理** — MVP：`.env` + `.gitignore`。生产：环境变量。禁止硬编码凭据。

### 代码质量规则

39. **文件大小限制** — 每文件最多 800 行。超出则拆分模块。
40. **函数大小限制** — 每函数最多 50 行。超出则拆分。
41. **禁止深层嵌套** — 最多 4 层。使用提前返回。
42. **Actor 返回值** — 始终返回 `pa.Table` 或 Pydantic 模型。永远不要返回裸 `dict`。
43. **Schema 演化：破坏性变更需要迁移** — `add_columns`（零成本，可空）用于增量变更。`alter_columns`（重写）用于类型变更时需要显式迁移步骤，前后加版本标签。永远不要在没有迁移计划的情况下修改非空列。
44. **Catalog 限流** — Catalog Actor 必须限制最大 100 个并发元数据操作。超出的请求以 `CatalogError(error_code=CatalogError.RATE_LIMITED)` 拒绝。拒绝时记录 `arrow_lake_catalog_rate_limited_total`。
45. **连接池死锁预防** — `DuckDBConnectionPool` 必须使用 `asyncio.Semaphore`，连接获取超时 30s。读操作期间不要持有写连接。如果写锁超时触发，记录 CRITICAL 并中止操作——不要无限排队。

### 反模式（禁止）

- ❌ 将 OLAP 查询结果在 Python 层做大表 join — 应在 DuckDB SQL 内完成 join
- ❌ 读取 Lance 数据时不检查边界的零拷贝
- ❌ 提前创建所有数据库表 — 每个 Story 只创建所需的表
- ❌ 使用 `print()` 或 `logging.info()` — 使用 `structlog`
- ❌ Actor 方法返回 `dict` — 返回 Pydantic 模型或 `pa.Table`
- ❌ 使用 `.json` 配置文件 — 使用 YAML
- ❌ 硬编码密钥 — 使用环境变量
- ❌ 跳过零拷贝边界测试
- ❌ 前向 Story 依赖 — 每个 Story 只依赖前面的 Story
- ❌ 在模块作用域初始化重量级资源（Ray、GPU） — 使用懒加载

---

## 使用指南

### 给 AI 代理

1. 实现代码前先阅读本文件
2. 遵循以上 45 条规则 — 这些规则源自架构决策和专家评审
3. 交叉参考 `architecture.md` 获取 ADR 详情，`system_design.md` 获取组件规格
4. 有疑问时，检查 `epics.md` 中的具体 Story 验收标准
5. 所有 Arrow 边界必须在集成测试中通过 `assert_zero_copy()` 验证

### 给开发者

1. 架构决策变更或新模式确立时更新本文件
2. 每季度审查，保持规则与实现同步
3. 规则总数应与 frontmatter 中的 `rule_count` 匹配
4. 交叉参考 architecture.md 的 ADR 章节了解决策理由

### 最后更新

2026-04-13 — 专家评审后同步。DuckDB 降级为仅 Catalog，Daft SQL 升级为主 OLAP，连接池 8→4，8 个 Epic 共 80 个 Story。
2026-04-13 — 版本锁定策略文档化。ArrowCopyDetector 作为 Story 1.5 AC 加入。规则 43-45 新增（Schema 迁移、限流、死锁预防）。架构文档新增 ADR-03（Object Store 规模）和 ADR-04（Embedding 服务）。
