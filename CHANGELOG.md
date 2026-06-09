# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.6.1] - 2026-06-08

### Summary

v1.6.1 聚焦于 **消除 API 阻塞操作** — 将重量级同步任务转为 fire-and-forget 异步模式，附带进度追踪。

### Changed

- `kg_build` 不再阻塞 API：拆分为 `prepare_build()` + `execute_build()`，立即返回 task_id
- `kg_build` 数据准备阶段（LanceDB 加载 + Arrow 列规范化）改用 `run_in_executor` 避免 event loop 阻塞
- HugeGraph `build_concurrency` 默认值 1 → 3
- HugeGraph `build_batch_delay` 默认值 3.0s → 0.5s
- `TaskManager` 泛化为通用后台任务管理器（`ExportTask` 保留为别名）

### Fixed

- **CRITICAL**: `Lake._component_lock` 从 `threading.Lock` 改为 `threading.RLock`，修复嵌套 `_get_component` 调用导致的死锁（`_create_kg_builder` 内部调用 `_get_kg_client` + `_get_kg_extractor` 各自再获取同一把锁）

### Added

- `POST /api/v1/datasets/{name}/ingest/async` — 异步文件摄取 (HTTP 202)
- `POST /api/v1/backup/create/async` — 异步备份 (HTTP 202)
- `POST /api/v1/backup/restore/async` — 异步恢复 (HTTP 202)
- `GET /api/v1/tasks/{task_id}/status` — 统一任务状态查询
- `GET /api/v1/tasks` — 任务列表（支持过滤）
- `BackgroundTask` 数据类（替代 `ExportTask`，向后兼容）

### Performance

| 操作 | 旧模式 | 新模式 |
|------|--------|--------|
| kg_build (1000 rows) | 阻塞 100+ 分钟 | <1s 返回，后台执行 |
| ingest (大文件) | 阻塞 600s | <1s 返回，后台执行 |
| backup/restore | 阻塞 10-30 分钟 | <1s 返回，后台执行 |

## [1.6.0] - 2026-06-08

### Summary

v1.6.0 聚焦于 **修好已知问题，让现有能力更稳更健壮**。不追新特性，夯实基础。

### Deployment

- **Dockerfile**: 替换 ghcr.io/astral-sh/uv 远程拉取为本地二进制 COPY，解决网络受限环境构建问题
- **Dockerfile**: builder 阶段代理清除移至网络操作之后，确保 apt-get/uv 可通过代理访问外网
- **docker-compose.prod.yml**: 版本号对齐 `arrow-lake:1.6.0`，metrics 端口改为 `9091`（避免与 API 8000 冲突）
- **Helm Chart**: `appVersion` 和 `version` 对齐至 `1.6.0`
- **.env**: 新增 `REDIS_PASSWORD`，`METRICS_PORT` 修正为 `8001`，LLM 配置同步
- **构建产物**: `deploy/uv-local` / `deploy/uvx-local` 加入 `.gitignore`
- **清理**: 移除 `_bmad-output/` 过期规划文档（已被 `docs/` 下文档替代）

### Phase 1 — 安全加固 + Bug 修复 + E2E 搜索/RAG

#### Fixed — Bug 修复
- **C1**: 修复 API server(8000) 与 metrics server(8000) 端口冲突，metrics 默认改为 8001
- **C2**: JWT auth_mode 跨字段验证缺失，新增 `@model_validator` 确保密钥完整性
- **C3**: 统一所有 `*_timeout_seconds` 字段 `ge=1` 约束
- **Q1**: RRF 公式重构为论文标准写法 `enumerate(start=1)`，数学不变但可读性提升

#### Fixed — 安全加固
- **Q4**: SQL 注入防护补强 — 新增 COMMENT/RENAME/TRUNCATE/MERGE/GRANT 等 DDL 关键字拦截
- **R1**: Prompt 注入防护扩展 — context text 也执行 sanitize，扩大 injection regex
- **A2**: Rate limit X-Forwarded-For bypass 修复 — 从右往左跳过可信代理 IP
- **A3**: Daft SQL 管道调用前也执行 `validate_sql_safety()`
- **A6**: Chunked encoding 请求也执行请求大小限制

#### Fixed — E2E 搜索/RAG
- **ER1**: 空检索结果不再送 LLM（防幻觉），抛 `RAG_RETRIEVAL_FAILED`
- **ER2**: Hybrid search 单路失败时降级到单路搜索（不再全盘失败）
- **ER3**: SSE 流异常不再静默吞掉，记录并 break（防客户端挂起）

### Phase 2 — 异常体系 + 资源控制 + E2E 可靠性

#### Added — 异常层次
- 新增 `ConcurrencyError` / `TransientError` / `ConsistencyError` 异常类
- `ErrorCode` 枚举新增 CACHE/CONCURRENT/METADATA/RESOURCE/TRANSIENT 等分类
- `errors.py` 补全 DOCUMENT_*/TRANSFORM_*/QUALITY_* HTTP 状态码映射

#### Added — 资源控制
- 新增 `ResourceLimits` 配置（查询超时、并发限制、结果行数、扫描字节）
- 新增 `BackpressureConfig` 配置（摄取队列、拒绝阈值、重试次数）
- 超时级联验证 API > OLAP > LLM 递减

#### Fixed — 基础设施健壮性
- **CO1**: 熔断器 half-open 竞态修复，失败后恢复计数器
- **CO2**: 熔断器新增 4 个 Prometheus 指标（state/failures/opens/recoveries）
- **CO3**: Structlog 添加 `format_exc_info` + `ExceptionRenderer`，JSON 日志异常栈可读
- **CO4**: HTTP client 工厂添加 timeout/limits/retries 默认值
- **Q2**: DuckDB session pool 泄漏修复 — 超时后正确释放 semaphore
- **Q3**: Query cache key 含 version，避免跨版本缓存冲突

#### Fixed — E2E 摄取可靠性
- **R2**: 嵌入 fallback 扩展 — Timeout/429/502/503/504 也触发 fallback 到本地 encoder
- **R3**: 失败图像用 null marker 替代零向量（cosine similarity 不再误判为 1.0）
- **EI1**: 嵌入批处理分片容错 — 大批次按 shard_size 分片，中间失败用 null 占位
- **EI6**: Lance dataset lock 添加 acquire timeout（30s），防死锁

#### Fixed — 数据安全
- **W1**: Backup restore 先恢复后删除（原数据安全网）
- **W2**: Workflow rollback 使用临时数据集（非原子操作安全网）
- **W3**: Audit HMAC 未配置时 verify() 返回 False（不再静默绕过）

### Phase 3 — 代码质量 + Protocol + E2E 基础设施

#### Changed — 代码质量
- **3.1**: `self._storage` 类型从 `Any` 改为 `StorageProtocol`，query bridges 依赖注入
- **3.2**: `_trace_span` 去重到共享基类，`_lake_rag.py` 改用 `self.config` 公开属性
- **3.3**: API 中间件管道显式声明 `MIDDLEWARE_PIPELINE`，Correlation ID 移至首位

#### Fixed — E2E 基础设施
- **EF1**: 熔断器集成到 Gravitino/HugeGraph/Redis（`circuit_protected()` 上下文管理器）
- **EF3**: Error mapping 补齐 — 100% ErrorCode 覆盖 HTTP 状态码
- **EF5**: 后台线程关闭有 `is_alive()` 验证 + Prometheus 指标
- **EF7**: HTTP async client 资源泄漏修复 — async shutdown 中正确调 `aclose()`

#### Fixed — 基础设施批量修复
- Gravitino client init flag 成功后才设 `_initialized=True`
- Gravitino sync 新增冲突检测（注册前检查是否存在）
- HugeGraph retry 扩展 — 新增 5xx 和 connection reset
- Entity extraction confidence 默认从 1.0 改为 0.5
- Kafka 连接器新增 retry decorator
- Quality gate 无 schema 时至少执行基本验证
- LLM 空响应新增 retry 或明确错误
- DuckDB 连接归还前健康检查
- Session pool 半创建连接清理

### Testing

- 全量测试 **4818 passed, 0 failed**
- 测试覆盖率 ≥ 80%

## [1.5.2] - 2026-06-01

### Fixed — Security Hardening & Code Quality
- **S1**: JWT 空密钥改为 raise ValueError 阻止启动（HS256 必须配置 secret_key）
- **S2**: Kerberos SPNEGO 改用 gssapi Python API，消除 principal 命令注入
- **S3**: gravitino_stats SQL 查询改为参数化，消除 table name SQL 注入
- **S4**: Redis 移除默认密码 `redisprod`，强制 `.env` 配置 `REDIS_PASSWORD`
- **S5**: Admin bypass 改用 `Role.ADMIN.value`，不再硬编码字符串
- **S6**: Refresh token 旋转后自动撤销旧 token jti
- **S7**: OAuth2 token_url 强制 HTTPS scheme
- **S8**: 异常日志移除 client_secret 泄露风险
- **S9**: `--forwarded-allow-ips` 从 `*` 改为 Docker 子网 `172.30.0.0/16`
- **S10-S11**: Docker Compose 所有非 API 端口加 `127.0.0.1` 绑定（MinIO/Redis/Ray/监控 8 个服务）
- **S12**: urlopen 前校验 URL scheme 防 SSRF
- **S13**: MD5 缓存键改为字符串直接做 dict key
- **S14**: SQL blocklist 增加 ATTACH/DETACH/PRAGMA/LOAD/CALL/SET
- **S15**: Gremlin 白名单移除 `union`（可嵌套绕过）
- **S16**: OLAP SQL 端点增加 `validate_sql_safety` 校验
- **S17**: JWT 空 key 在 `app.py` 路径也阻止启动

### Changed — Code Quality
- `@staticmethod` 中 `self` 引用修复（lineage.py）
- 4 个 F821 undefined name 修复（lineage.py/search.py/schema.py/storage.py）
- `create_task` 返回值存储 + done callback 防止 GC 回收
- 闭包循环变量默认参数绑定（rbac.py）
- `ClassVar` 注解补全（rbac.py/federated_engine.py）
- `raise ... from None` 补全（rag.py）
- `except: pass` 改为 logger 调用（storage.py/ingest_embed.py）
- Gravitino 失败日志级别 debug→warning（lineage.py）
- LineageStore 实例缓存（lineage_hooks.py）
- ruff --fix 清理 53 项 lint 问题（F401/I001）

### Metrics
- Bandit HIGH: 2 → 0
- Ruff F821: 6 → 0
- Ruff F401: 18 → 0
- Tests: 506/507 passed（1 pre-existing failure）

## [1.5.1] - 2026-05-29

### Added — Security Governance + Lineage v2
- **Gravitino Auth Providers**: Simple/OAuth2/Kerberos/Null 四种认证策略
- **Lineage Hooks**: 摄入/搜索/查询自动记录血缘事件
- **Expanded RBAC**: Schema-level ACL + Deny-first 权限模型 + GravitinoRBACBridge 扩展映射
- **Federated Pushdown**: 跨 Catalog 联邦查询下推优化

### Changed
- 权限映射扩展至 15 个 action→privilege 对
- Lineage 自动通知集成到 record_event 流程

## [1.5.0] - 2026-05-28

### Added — Platform Systematization
- **Architecture Visualization**: 完整架构图 + 依赖地图 + 术语表
- **Security Audit**: 全面安全审计报告 + 加固建议
- **CLI 场景别名**: `knowledge`/`connect`/`search`/`manage`/`explore` 五大场景入口
- **Documentation v2**: docs_v2/ 三层文档体系（Data/Knowledge/Compute Plane）
- **BMAD Agent System**: 20+ 产品/架构/开发/设计 Agent 集成
- **v1.4.5 Security Fixes**: 4 项安全漏洞修复

## [1.4.4] - 2026-05-25

### Added — RAG Quality Leap + SDK/CLI High Performance
- **RAG Reranking Pipeline**: CrossEncoder / LLM / Noop 三种重排策略，`RerankerFactory` 自动选择
- **Query Transformation**: HyDE (Hypothetical Document Embedding) / MultiQuery / Identity 三种查询改写策略
- **Multi-turn Conversation**: 对话历史注入 RAG context window，Token 预算管理
- **Context Window 升级**: Score-based 排序 + `finalize()` 语义化 API
- **GraphRAG RRF Fusion**: 知识图谱检索结果与向量/全文三路 RRF 融合
- **CLI Lake Instance Caching**: 命令间复用 Lake 实例，避免重复初始化
- **CLI Embedding Model Caching**: 搜索命令复用 encoder，消除重复加载延迟
- **Rich Progress Bars**: 批量文件摄入 (>3 files) 显示进度条 + SIGINT 信号保护
- **Shared HTTP Connection Pools**: httpx 连接池复用，减少 TCP 握手开销
- **Lake Context Manager**: `with Lake(...) as lake:` 异步资源清理
- **Anthropic Circuit Breaker**: LLM 调用熔断保护，避免级联故障
- **Latency Breakdown Tracking**: RAG 各阶段耗时分解 (retrieval/reranking/generation)
- **LRU Cache with Limits**: 缓存上限保护，防止内存无限增长
- **DuckDB Auto-warmup**: 启动时预热连接池 + 内存校验
- **Structured Error Output**: CLI 错误结构化输出，便于脚本解析

### Changed
- `pyproject.toml` 版本同步到 1.4.4
- Thread lock 管理增加 LRU 上限 (max 1024)，防止 dataset locks 内存泄漏
- RAG context window 行为变更为 score-based 排序

### Tests
- 更新 RAG context 测试匹配新的 `finalize()` API 语义
- 更新 score-based ordering 测试期望

## [1.4.3] - 2026-05-23

### Added — Production Readiness: Observability + Auto-Maintenance + Quality Gates
- **OpenTelemetry 集成**: 分布式链路追踪，gRPC OTLP exporter
- **Alertmanager 告警**: Prometheus AlertManager 集成，多渠道通知
- **Auto-Maintenance Scheduler**: 后台定时维护任务（compaction、cleanup、index rebuild）
- **Quality Gates**: 数据摄入质量门控，验证 schema / null ratio / dedup threshold
- **docker-compose.prod.yml 升级**: OTel Collector + Alertmanager sidecar
- **Latency SLO Dashboard**: Grafana SLO 看板，P50/P95/P99 延迟追踪

### Changed
- Docker Compose prod profile 增加 OTel + Alertmanager 服务
- 维护任务接入 FastAPI lifespan 管理

### Tests
- Quality gate 单元测试 + 集成测试
- Auto-maintenance scheduler 测试

## [1.4.2] - 2026-05-22

### Added — 安全加固 + Gravitino 配置化
- **FQN 注入防护**: `ValidationMixin` 全局限名验证，拒绝非法字符和路径穿越
- **SQL 注入防护增强**: 分号 + 多语句检查升级，DDL/DML 关键字黑名单扩展
- **JSON 反序列化验证**: 外部 JSON payload 深度限制 + 类型白名单
- **Thread Zombie Detection**: 线程僵尸检测，回收泄漏的工作线程
- **Gravitino 深度治理**: 配置化 Gravitino 连接参数，支持环境变量覆盖

### Changed
- Gravitino 配置从硬编码改为 pydantic-settings 管理
- 安全验证层统一到 `ValidationMixin`，消除分散的校验逻辑

### Fixed
- Gravitino Docker 初始化脚本 MinIO 凭证参数化
- 测试套件中环境隔离改进，消除跨测试状态泄漏

### Tests
- FQN 注入测试 (18 cases)
- SQL 注入增强测试
- Thread zombie 检测测试

## [1.4.1] - 2026-05-22

### Added — Gravitino 元数据治理集成
- **Gravitino Server + Lance REST Catalog**: Docker 部署 (docker-compose profile: gravitino)
- **GravitinoBridge**: DuckDB ↔ Gravitino 双向同步 (catalog/schema/table 元数据)
- **GravitinoSyncScheduler**: 后台定时同步任务，接入 FastAPI lifespan 管理
- **GravitinoRBACBridge**: 权限委托 Gravitino 决策 + 本地 RBAC 降级
- **GravitinoTagService**: 数据分类标签管理 (sensitive/PII/financial)
- **GravitinoPolicyService**: 数据保留策略 + 脱敏策略
- **GravitinoStatsCollector**: DuckDB 统计信息收集 + Gravitino 属性注册
- **GravitinoModelRegistry**: ML 模型版本化管理 + 热切换
- **ArrowLakeGravitinoClient**: Python SDK 统一封装
- **`/metadata/*` API 代理端点**: catalogs / tables / tags / policies / statistics / models
- **Lineage 自动通知**: Lance 版本变更自动通知 Gravitino
- **Daft GravitinoCatalog**: 联邦查询支持
- **健康检查**: gravitino + lance_rest 状态纳入 `/health` 端点
- **优雅降级**: Gravitino 不可用时 Arrow Lake 核心功能正常运行

### Tests
- 43 Gravitino 测试 (27 unit + 16 API e2e)

### Fixed
- Dockerfile runtime 阶段移除不必要的 protobuf-compiler
- init-gravitino.sh MinIO 凭证参数化 (不再硬编码)
- gravitino.py Request 命名冲突 (urllib vs fastapi)

## [1.4.0] - 2026-05-20

### Added — Phase 0: 补债拆分
- **DuckDB Profiling**: `enable_profiling` 配置，`explain_analyze()` 附加 profiling 输出
- **DuckDB Relational API**: `metadata.py` 新增 `_relational_query()` 类型安全查询
- **大文件拆分**: `ingestor.py` (870→200行) 拆为 `_ingest_files/media/sources.py`；`client.py` (838→300行) 拆为 `_traversers/import_export.py`
- **小修**: `__all__` 重复条目修复，DuckDB 版本兼容检查

### Added — Phase 1: 性能与规模
- **DuckDB 水平扩展**: 多实例连接池路由 (round-robin)，Redis 信号量协调
- **GPU Autoscaling**: 冷却期 + 缩容保护 + 扩缩容事件持久化
- **Schema 演进**: `SchemaCompatibilityChecker` 兼容性检查 + `POST /{name}/schema/migrate` 端点
- **Daft 原生媒体**: 批量图像/视频处理迁移到 Daft Rust 实现，感知哈希迁移到 `daft.functions.image_hash()`

### Added — Phase 2: 数据治理
- **血缘可视化 API**: `GET /lineage/graph/{name}`、`POST /lineage/impact`、`GET /lineage/stats`
- **质量规则引擎**: 声明式 `QualityRuleEngine`，支持 length/range/regex/duplicate 检查 + reject/flag/remove 动作
- **行级/列级 ACL**: `DatasetACL` 数据类，`PUT/GET/DELETE /admin/acl/{dataset}` 管理端点，查询和搜索结果自动裁剪

### Added — Phase 3: 收尾加固
- **FTS 真正分页**: `offset` 参数支持全链路（API → facade → FTS bridge → LanceDB）
- **查询结果流式**: OLAP 端点 `stream=True` 返回 SSE，每事件为 Arrow IPC batch (base64)

### Tests
- 3296+ tests passing, 0 failures
- 新增 80 个测试（质量规则 45 + ACL 35 + 流式验证）
- bandit 安全扫描: 0 高危

## [1.3.4] - 2026-05-19

### Fixed
- **代理泄漏全面修复**: 宿主机 HTTP_PROXY 通过 Docker Compose `${HTTP_PROXY:-}` 插值泄漏到容器内，导致所有 httpx 客户端 (embedding/LLM/KG) Connection refused
- **Embedding 500 错误**: router 使用请求参数默认模型名 (Qwen/Qwen3-Embedding-0.6B) 而非配置模型名，Ollama 返回 404

### Added
- **httpx 客户端工厂** (`core/http.py`): `create_http_client()` / `create_async_http_client()`，默认 `trust_env=False`，统一管理出站 HTTP 代理策略
- **DuckDB 查询缓存** (`query/_cache.py`): LRU 缓存层，支持 TTL + max_entries + 线程安全，命中时跳过 SQL 编译与执行
- **OLAP 性能调优配置**: `query_cache_enabled/ttl/max_entries`、`preserve_insertion_order`、`parquet_row_group_size`、`enable_progress_bar`
- **Loki + Promtail 日志聚合**: `deploy/monitoring/loki/` + `deploy/monitoring/promtail/`，容器日志集中收集
- **Prometheus 告警规则**: `deploy/monitoring/prometheus/rules/arrow_lake.yml`，服务健康/API 延迟/错误率告警
- **nginx TLS 反向代理**: `deploy/nginx/nginx.conf`，安全头 + 速率限制 + 请求大小限制
- **MinIO 定时备份**: `deploy/scripts/backup-minio.sh`，保留策略 + CronJob 集成
- **Grafana 多数据源**: Loki datasource 自动配置

### Changed
- **Docker Compose 代理清空**: `docker-compose.prod.yml` api/ray-head/ray-worker 显式设置 `HTTP_PROXY=""`
- **NO_PROXY 扩展**: 增加 `172.19.0.0/16`、`loki`、`promtail`
- **`.env.example` 重构**: 按类别分组 (Docker/存储/计算/监控/安全)，新增 Docker Compose 必需变量
- **FTS 搜索**: 增加 replace 支持（自动 drop 旧 segmented column 后重建）
- **会话管理器**: 增加连接池监控指标 (pool_size/active_sessions/queued_requests)

## [1.3.3] - 2026-05-18

### Added
- **Daft DataFrame 查询引擎**: LazyDaftFrame + DaftQueryEngine 完整链式操作 (sort/filter/groupby/join/sql/pivot/explode/sample/distinct/offset)
- **Daft 安全加固**: 全方法标识符验证 (`_SAFE_IDENTIFIER_RE`)、SQL DDL/DML 黑名单、collect 行数上限 (max_rows)、错误消息脱敏
- **Daft API pipeline**: POST `/query/daft` 支持链式操作 pipeline，DaftQueryRequest 扩展 8 种操作步骤
- **行数预检**: `check_feasibility()` 预检数据量，>500K 警告推荐 DuckDB，>1M 拒绝 (422)
- **DaftQueryResponse.warnings**: API 响应返回大数建议和截断警告
- **Daft API 示例**: 4 个 cookbook 示例脚本 (29-32)
- **__repr__**: LazyDaftFrame / LazyGroupedFrame / DaftQueryEngine 调试友好
- **架构文档更新**: DuckDB vs Daft 三方分工、瓶颈分析、选型矩阵、6E 查询流程图

### Changed
- **Daft 定位升级**: 从 Ingest/ETL 辅助角色升级为 DataFrame 查询引擎层
- **fill_null()**: 修复 `with_columns` 参数形式 (展开参数 → dict)
- **import inspect**: 提升到模块级，符合 PEP 8

### Tests
- 98 测试覆盖 (72 单元 + 26 API)，ruff/bandit 0 issues

## [1.3.2] - 2026-05-14

### Fixed
- **SSRF 防护**: 修复 except ValueError 吞没私有 IP 检查的 bug，HTTP 连接器添加连接后 IP 校验
- **FTS 索引**: 修复分片覆写丢失非 FTS 列数据（>50K 行时触发）
- **DuckDB session**: _env_backup 在 try 之前保存，异常时正确恢复 S3 环境变量
- **RAG batch stream**: task_to_stream 映射在创建时填充
- **KG builder**: 修复列标准化顺序 bug（content rename 覆盖 id 列）+ datetime 序列化
- **Circuit breaker**: allow_request 持锁到状态转换完成
- **DuckDB 连接池**: _close_conn 恢复 S3 环境变量
- **SQL 注入防护**: lineage 查询添加分号和多语句检查
- **JWT auth bypass**: 精确匹配替代宽泛 startswith

### Changed
- **代理配置**: 容器内改用 host.docker.internal:7888 (proxy-forward)，修复外部 API 不可达
- **备份**: 远程 blob 改用 server-side copy 替代 download→upload
- **配置合并**: _deep_merge 替代 dict.update 浅合并
- **限流**: 惰性清理过期计数器

### Added
- 20 个 API 示例脚本 (docs/cookbook/examples_api/)
- 46 个 upload/ingest 单元测试
- Docker compose: AWS env vars + hg-net 外部网络 + Ollama/DeepSeek 可配置

### Verified
- 3404 测试通过（14 失败均为 LLM 外部服务不可达）
- KG 构建端到端: 4055 vertices, 8454 edges
- HugeGraph + DeepSeek LLM + Ollama Embedding 全链路连通

## [1.3.1] - 2026-05-12

### Changed
- **pylance 升级**: 4.0.1 → 6.0.0，解锁 Lance 文件格式 v2.1/v2.2 双层编码、io_uring 高性能 I/O
- **lance-namespace 升级**: 0.6.1 → 0.7.6（pylance 6.0.0 依赖）
- **FTS 中文分词**: tantivy 后端移除后自动切换 lance-index + jieba 预分词，无需代码改动
- **文档更新**: 产品介绍 (中/英)、CLI Reference、Tech Compatibility Report 版本矩阵同步

### Verified
- 2610 单元测试通过，242 集成测试通过，覆盖率 76.61%（与升级前一致）
- Storage / Search / Vector / FTS / DuckDB lance_scan / Daft 零拷贝集成正常

## [1.3.0] - 2026-05-09

### Added
- **Redis 分布式 Session**: `RedisConfig` + `RedisSemaphore` 适配器，DuckDB Session 池支持水平扩展
- **QueryEngine Protocol**: `arrow_lake/query/engine.py` 定义 acquire/release/get_stats/shutdown 接口
- **RBAC 路由守卫**: 10 个路由文件添加 `Depends(require_role(...))`，覆盖 VIEWER/EDITOR/ADMIN 三级权限
- **JWT 黑名单 LRU**: `OrderedDict` 替换 O(n) dict rebuild，防 DoS 内存耗尽
- **Gremlin 注入防护增强**: 正则匹配裸 mutation step、闭包语法 `{}` 拒绝、`//` 行注释剥离
- **SQL 注入防护增强**: lineage SQL 验证剥离 `--` 和 `/* */` 注释
- **路径穿越防护**: export 路由 `resolve()` + `startswith()` 防止 `../` 逃逸
- **Helm HPA 模板**: `deploy/helm/arrow-lake/templates/hpa.yaml`（基于 CPU + 自定义指标）
- **Helm CronJob 备份模板**: `deploy/helm/arrow-lake/templates/cronjob-backup.yaml`（每日 02:00）
- **Helm Redis 环境变量**: Deployment 模板条件注入 Redis 配置
- **Gremlin 安全测试**: 17 个测试覆盖闭包绕过、裸 mutation、注释剥离、合法查询
- **Redis 信号量测试**: acquire/release、超时、回退、重连测试
- Cookbook examples: Redis Session (40)、RBAC Roles (41)、Gremlin Security (42)、JWT Blacklist (43)
- `sdk/` 模块: `LakeClient` 别名导出

### Changed
- **版本号**: pyproject.toml / _version.py / Chart.yaml → 1.3.0
- **Ingestor 并发修复**: ThreadPoolExecutor → 顺序执行，消除 Daft 读取竞争
- **lancedb API 兼容**: `open_dataset()` → `open_table()` (v0.30+)
- **备份测试**: `StorageBackend.MINIO` → `LOCAL`，消除 MinIO 环境污染
- **全量测试隔离**: 所有 Lake() 构造添加 `StorageConfig(backend="local")`
- **prod.yaml**: OLAP 配置完善、Redis 段、rate_limit 段、audit HMAC 注释
- **dev.yaml**: Redis 默认禁用
- `respx` 从生产依赖移至开发依赖
- 新增生产依赖 `redis[hiredis]>=5.0,<6.0`，开发依赖 `fakeredis>=2.0`

### Fixed
- Gremlin 注入绕过: `map`/`flatMap` 从白名单移除（闭包执行风险）
- Redis 信号量双释放: thread-local 后端跟踪防止 Redis→fallback 双减
- Redis TTL 幽灵许可: 仅首次 acquire 时设置 EXPIRE
- JWT 黑名单 O(n) 逐出: `OrderedDict.popitem(last=False)` O(1) 替换
- `/api/v1/version` 信息泄露: 添加 VIEWER RBAC 守卫
- `auth_service.py` coverage: 38% → 补充测试覆盖
- Gate B4 embedding 测试: HF model 下载依赖测试标记 skip
- RAG E2E 测试: auth header + `text_content` 列名修复
- KG E2E 测试: env var 隔离 + auth header 修复

### Removed
- `arrow_lake/query/_async.py`: 死代码删除（零外部引用）
- `tests/unit/duckdb/test_async_query.py`: 对应测试删除

## [1.2.2] - 2026-05-08

### Added
- `Lake.embed_and_add()`: 向量化管线 — 使用配置的 embedding 后端（HuggingFace/Ollama API）将文本列编码为向量，通过 `add_columns_table` 原位写入，无需全量重写
- `Lake.add_columns_table()`: Facade 暴露 Lance 原位列添加能力
- `Lake.config` 属性: 公开当前 ArrowLakeConfig 供外部读取
- `StorageAdvancedMixin.add_columns_table()`: Lance 原生 `add_columns` 避免全量 rewrite
- S3 远程备份/恢复: `BackupManager` 和 `BackupRestore` 支持 S3 server-side copy 路径（不再依赖本地 Path 操作）
- `ExportBridge` 自动检测 `/app/exports` 不可写时回退到 cwd
- 6 个行业分块测试数据文件: finance/tech/medical/business/education/literature

### Changed
- **chonkie 兼容性**: `TokenChunker` 参数 `token_chunk_size` → `chunk_size`，`SemanticChunker` 参数 `min_chunk_size` → `chunk_size`；`SDPMChunker` 自动 fallback（chonkie ≥1.6 移除）
- **HugeGraph 默认配置**: 端口 `8089` → `8091`，graph 名 `arrow_lake_kg` → `hugegraph`（匹配 docker-compose 部署）
- **docker-compose healthcheck**: graph 名 `arrow_lake_kg` → `hugegraph`
- Cookbook examples (34 files): `_add_vectors` 统一使用 `lake.embed_and_add()` + random fallback，不再走 `to_arrow()+restore_dataset` 全量重写路径
- `arrow_lake/query/olap.py`: 添加缺失的 `import contextlib`
- Deployment REST API 示例 (13/14/15): 修复 API Key 认证、容器内路径映射、`_post` 参数名、JSON 解析容错

### Fixed
- `e2e_chunking_scenarios.py` 数据文件缺失时 `KeyError: 'strategy'`（返回完整结果 dict）
- `olap.py` 中 `contextlib` 未导入导致 `NameError`
- `export.py` 默认 `base_dir=/app/exports` 本地运行 `PermissionError`
- `jwt_auth.py` 空 refresh token 断言过严（接受 400/401）
- `24_ensemble_search.py` `_ensemble_score` 列名检查顺序
- `26_audit_trail.py` / `27_data_lineage.py` `AuditEntry`/`LineageEvent` 的 `.get()` 调用错误
- `28_backup_restore.py` 缺少 `overwrite=True` 导致恢复失败
- `32_kg_traversal.py` KG build 使用全量数据集导致超时（改用 10 行小样本）
- `graphrag_e2e_test.py` 硬编码 HugeGraph 端口 8089（改为 8091）
- `s3_minio/01,03,04` hybrid search `_rrf_score` 列不存在（优先 `_hybrid_score`）
- `07_e2e_pipeline.py` 残留数据集未清理导致重复运行失败

## [1.2.1] - 2026-04-27

### Added
- 9 new facade methods in `_LakeAdminMixin`: `restore_dataset`, `get_dataset_version`, `list_dataset_versions`, `add_column`, `alter_column`, `drop_column`, `compact_dataset`, `read_dataset`, `scan_dataset`
- `CONTRIBUTING.md` — development setup, code standards, architecture overview, testing guidelines
- `SECURITY.md` — vulnerability reporting, auth architecture, data protection, transport security

### Changed
- Cookbook examples (39 files) unified to argparse CLI pattern (`--base-uri`, `--no-cleanup`)
- `_get_storage()` eliminated from all cookbook examples and most root examples (0 in .py files except tag operations)
- Bare `except Exception` reduced from 73 to 38 in cookbook examples (context-specific types)
- `server.py` deprecation notice updated with v2.0 removal timeline
- Duplicate `VectorSearchResult` removed from `__all__`

### Fixed
- `config_changed` NameError in `session_manager.py` — restored computation block
- Idle connection health check reliability — removed unreliable `_health_skip_seconds` optimization, always runs `SELECT 1`
- `_fallback_cache` cache pollution causing intermittent `test_fallback_encode_import_error_raises` failure
- RUF001 lint warnings in `chunker.py` — suppressed for intentional CJK punctuation

## [1.2.0] - 2026-04-24

### Added
- Kreuzberg PDF parser (Rust-core, 91+ format support) replacing marker_pdf/pypdf
- TurboOCR GPU acceleration service with circuit breaker pattern and retry logic
- Ingest dead-letter queue (`IngestDeadLetterQueue`) for failed document tracking with retry/resolve/purge
- Performance benchmark suite (`examples/query/benchmark.py`) — DuckDB query, chunking, validation, token counting baselines
- Document processing E2E test (`examples/ingestion/e2e_document_pipeline.py`)
- Test fixture generator (`tests/fixtures/documents/`) — 10 synthetic documents (EN/ZH/markdown/CSV/JSONL/multilingual)
- GraphRAG E2E test script (`examples/knowledge_graph/graphrag_e2e_test.py`)

### Changed
- Default OCR backend changed from `tesseract` to `paddleocr` (Kreuzberg config)
- `backup.py` refactored from 617 to 375 lines — extracted `_manifest_to_info`, `_restore_item`, `_paginate_keys` helpers
- `except Exception` narrowed from 17 to 4 occurrences — replaced with specific exception types across 12 files

### Fixed
- **Security**: SSRF prevention in TurboOcrClient (`_validate_endpoint` blocks private IPs)
- **Security**: Gremlin injection prevention in HugeGraph client (`_BLOCKED_GREMLIN_PATTERNS`)
- **Security**: SQL injection hardening — `escape_sql_literal()` with type check and length limit in `validation.py`
- **Security**: JWT error message sanitization — non-expiry errors return generic message
- **Security**: Blob key path sanitization in ingestor (prevents path traversal)
- **Security**: Backup dataset name validation (rejects `..`, `/`, `\\`)
- **Security**: API key empty-config defense-in-depth (rejects protected endpoints when no key configured)
- Import-order bug in `hybrid.py` (`escape_sql_literal` used before import)
- Duplicate property key creation loop removed in `knowledge_graph/client.py`
- structlog-style logger call fixed in `ray_serve_encoder.py`
- SentenceTransformer API compatibility (both `get_sentence_embedding_dimension` and `get_embedding_dimension`)

### Removed
- 52+ stale unit test files (unmaintained, referencing deleted modules)

## [1.1.0] - 2026-04-22

### Added
- Production hardening: observability, metrics, and operational tooling

## [1.0.0] - 2026-04-21

### Added

v1.0 GA release — 2224 tests, 82.92% coverage, production-ready data lakehouse.

**M0: Infrastructure & Query Migration**
- DuckDB Lance extension abstraction layer (`_base.py`, `_db.py`, `lance_adapter.py`)
- Native `__lance_scan()` with PyArrow streaming fallback
- DuckLake workspace management (materialize, TTL cleanup, metadata tracking)
- S3 storage_options schema and dual-path integration (Lance SDK + DuckDB SET)
- Lake Facade decomposed into 9 focused mixins (ingest, search, query, admin, lineage, audit, rag, kg)

**M1: Production Storage**
- S3/MinIO blob storage with BlobStoreManager (multipart upload, presigned URLs)
- Backup/restore manager (Lance + MinIO + DuckLake)
- REST API backup endpoints

**M2: RAG Pipeline**
- LLM provider abstraction (Anthropic Claude, OpenAI-compatible)
- RAG pipeline with citation support (retrieval → context assembly → generation)
- Session management for multi-turn conversations
- SSE streaming for real-time generation
- Entity extraction endpoints
- REST API: `/api/v2/rag/*`

**M3: Knowledge Graph + GraphRAG**
- HugeGraph REST client with schema management
- Entity/relation extraction from unstructured text
- Knowledge graph builder with task management
- GraphRAG retrieval with 3-way RRF fusion
- REST API: `/api/v2/kg/*`

**M4: Production Readiness**
- JWT authentication with access + refresh token flow
- RBAC with ADMIN/EDITOR/VIEWER role hierarchy
- API key authentication with rotation (90-day default)
- OpenTelemetry distributed tracing
- Separated liveness/readiness health probes
- 15 REST API routers (system, datasets, search, query, quality, embedding, export, lineage, audit, backup, rag, kg, auth, admin)
- Performance benchmark framework with baseline tracking
- 6 Grafana dashboards (system, ingestion, processing, query, OMTM, SLO)

**M5: Operations & Governance**
- Rate limiting middleware (slowapi, disabled by default, per-endpoint config)
- HTTP security response headers (HSTS, X-Content-Type-Options, X-Frame-Options, Referrer-Policy, CSP)
- 11 Prometheus alert rules (HTTP errors, auth failures, ingestion stalled, rate limit, memory, latency)
- SLO thresholds configuration in Helm values

**Security Hardening**
- SQL injection prevention centralized in `validation.py`
- Path traversal protection in export
- HMAC integrity verification on audit trail
- JWT state propagation fallback in `get_current_user()`

**Deploy Artifacts**
- Multi-stage Docker build (CPU + GPU variants)
- Docker Compose profiles (core, dev, gpu, monitoring, kg)
- Helm chart with PrometheusRule, NetworkPolicy
- Init scripts, TLS cert generation, bucket setup

### Changed

- Lake class decomposed from 1049-line monolith to 9 mixin modules
- Query layer migrated from direct LanceDB SDK to DuckDB-native SQL with Lance extension
- Auth middleware migrated from class-based BaseHTTPMiddleware to function-based with `@app.middleware("http")`
- All config sections registered in `_build_merged_update()` and `from_yaml()` constructor

## [0.1.0] - 2026-04-15

### Added

Initial release — 80 stories across 9 Sprints, 1414 tests.

**Core Infrastructure (Sprint 1)**
- LanceStorageManager: create, read, append, delete, version, tag, compact, schema migration
- Pydantic-based configuration system (YAML + environment variables)
- Unified exception hierarchy with error codes
- Prometheus metrics integration
- CLI via Click (`arrow-lake` command)
- HTTP API server

**Data Ingestion (Sprint 2-3)**
- Batch ingestion from Parquet, JSON, CSV, images, audio, video
- MinIO/S3-compatible object storage integration
- Multi-process distributed ingestion via Ray
- Streaming ingestion pipeline

**Embedding & Vector Search (Sprint 3-5)**
- Multi-model embedding generation (sentence-transformers)
- Semantic vector search via LanceDB
- Hybrid search (BM25 + vector scoring)
- Multi-vector index support (multi-modal)
- Faceted search with drill-down

**Data Quality (Sprint 4-5)**
- Schema validation framework
- Null value detection and statistics
- Quality filter pipeline
- Content deduplication: SHA-256 exact match + pHash perceptual hash
- Incremental cross-batch dedup with seen-hash accumulation

**Data Catalog (Sprint 6-7)**
- Dataset catalog with metadata management
- Data lineage tracking with SQL query interface (DuckDB)
- Actor-based access management
- Audit logging with HMAC integrity verification

**Data Export (Sprint 5)**
- Export to Parquet (with compression: snappy, gzip, brotli, zstd, lz4)
- Export to CSV (binary columns excluded with warnings)
- Format auto-detection from file suffix
- Column selection and version selection

**Workflow & Orchestration (Sprint 8-9)**
- Metaflow workflow integration
- Ray distributed runtime
- Pipeline orchestration and scheduling

**Testing**
- 1414 tests (unit + integration)
- 82%+ code coverage
- Comprehensive test utilities and fixtures

**Security**
- SQL injection prevention (parameterized queries, keyword validation)
- Path traversal protection
- Input validation on all public APIs
- No hardcoded credentials
