# Arrow Lake v1.3.0 Implementation Plan

> **Review Status**: 3-agent review completed. 2 CRITICAL architecture fixes + 6 HIGH security additions incorporated.

## Context

v1.2.2 生产就绪度评估（5 路 Agent 并行审计）得分 7.6/10。安全维度 5.8/10 是上生产的硬伤，DuckDB Session 无法水平扩展是架构瓶颈。v1.3.0 目标：**堵住安全漏洞 + 分布式 Session 基础设施 + 运维自动化**，使项目达到生产级。

---

## Phase 0: Code Quality Cleanup（前置，~1 天）

### 0.1 删除死代码 `_async.py` [S]
- 删除 `arrow_lake/query/_async.py`（102 行）
- 同时删除 `tests/unit/duckdb/test_async_query.py`（Review 发现的遗漏）
- 验证：`pytest` 全量通过

### 0.2 版本号同步 [S]
- `pyproject.toml:4` → `version = "1.3.0"`
- `arrow_lake/_version.py` → `__version__ = "1.3.0"`
- 新增回归测试：`assert __version__ == tomllib(pyproject)["version"]`

### 0.3 `sdk/` 模块处理 [S]
- `arrow_lake/sdk/__init__.py` 添加 `from arrow_lake import Lake as LakeClient` + `__all__`
- 新增 1 个测试验证导入

### 0.4 `respx` 依赖确认 [S]
- 确认 `respx` 在 API 测试中实际使用（已确认合理，无需移动）

---

## Phase 1: DuckDB 分布式 Session（核心，~5-6 天）

### 1.1 定义 `QueryEngine` Protocol [M]
- 新建 `arrow_lake/query/engine.py`（~60 行）
- 定义 Protocol 接口（匹配实际 `DuckDBSessionManager` API）：
  ```python
  class QueryEngine(Protocol):
      def acquire(self, *, timeout: float | None = None, load_ducklake: bool = False) -> Any: ...
      def get_stats(self) -> SessionPoolStats: ...
      def shutdown(self) -> None: ...
      @property
      def pool_size(self) -> int: ...
  ```
  > **Review fix**: 移除了 `release()`（实际由 `_ManagedSession` 回调管理，非公共 API）；`acquire` 使用 keyword-only 参数匹配实际签名；增加 `pool_size` 属性
- `DuckDBSessionManager` 隐式实现 Protocol（鸭子类型）
- 新建 `tests/unit/test_query_engine.py`：验证 `isinstance(mgr, QueryEngine)`

### 1.2 添加 `RedisConfig` [M]
- 新建 `arrow_lake/config/redis.py`（~40 行）
  ```python
  class RedisConfig(BaseModel):
      enabled: bool = False
      url: str = "redis://localhost:6379/0"
      password: str = ""           # Review: 新增认证字段
      ssl: bool = False            # Review: 新增 TLS 字段
      ssl_cert_reqs: str = "required"
      semaphore_key_prefix: str = "arrow_lake:semaphore:"
      semaphore_ttl_seconds: int = 300
      redis_pool_size: int = 10    # Review: 重命名避免与 DuckDB pool 混淆
  ```
  > **Review fix**: 新增 `password`、`ssl`、`ssl_cert_reqs` 字段（生产 Redis 必须认证+加密）；重命名 `connection_pool_size` → `redis_pool_size`
- 修改 `arrow_lake/config/main.py`：`ArrowLakeConfig` 加 `redis: RedisConfig`，`_SECTION_TYPES` 加映射
- `configs/prod.yaml` 加 `redis:` 段（含 password 引用 env var），`configs/dev.yaml` 加 `redis: {enabled: false}`
- 更新 `tests/regression/test_config_backward_compat.py`（Review: 必须加入 `RedisConfig` 到可独立构造列表）
- 新建 `tests/unit/config/test_redis_config.py`

### 1.3 实现 `RedisCountingSemaphore` [L] ⚠️ CRITICAL REDESIGN
- 新建 `arrow_lake/query/_redis_semaphore.py`（~300 行）
  > **Review fix**: `redis.asyncio.Semaphore` **不存在**（redis-py 5.x 只有 `Lock`，无信号量）。且 `DuckDBSessionManager.acquire()` 是同步方法，不能用 async。必须使用**同步 `redis.Redis` 客户端 + Lua 脚本**实现计数信号量。
  - 同步 Lua 脚本计数信号量：`INCR`/DECR 原子操作
  - `acquire(timeout)` 阻塞等待（同步循环 + `time.sleep`）
  - `release()` 归还许可
  - `RedisConfig.enabled == False` 时回退到 `threading.Semaphore`
  - 连接失败时回退到 `threading.Semaphore`（降级不阻塞）
- `pyproject.toml` 加 `redis[hiredis]>=5.0,<6.0`
- `pyproject.toml` dev deps 加 `fakeredis>=2.21`
- 新建 `tests/unit/test_redis_semaphore.py`（10 个测试，覆盖：acquire/release、超时、回退、连接失败、并发竞争、shutdown）

### 1.4 重构 `DuckDBSessionManager` 支持可插拔信号量 [L]
- 修改 `arrow_lake/query/session_manager.py`：
  - `__init__` 新增可选 `semaphore` 参数（构造器注入，不用 `from_config` 类方法）
  - 保留默认 `threading.Semaphore` 行为（向后兼容）
  > **Review fix**: 不用 `from_config` 类方法（项目无此先例），直接构造器 DI
- 修改 `arrow_lake/__init__.py:157-163`：
  ```python
  def _create_session_manager(self) -> Any:
      from arrow_lake.query.session_manager import DuckDBSessionManager
      from arrow_lake.query._redis_semaphore import create_semaphore
      semaphore = create_semaphore(self._config.redis, self._config.olap.max_concurrent_queries)
      return DuckDBSessionManager(self._config.olap, self._config.storage, semaphore=semaphore)
  ```
- 更新 `tests/unit/test_session_manager.py`（含 Redis 禁用/启用 mock）

### 1.5 修复 lineage bypass + 缓存 LineageQueryBridge [S]
- 修改 `arrow_lake/catalog/lineage.py`：
  - `LineageQueryBridge.__init__` 新增可选 `session_manager` 参数
  - `query()` 方法（line 231）优先用 `session_manager.acquire()`
  > **Review fix**: `_lake_lineage.py:81` 每次调用重建 bridge，应改为 `_get_component` 缓存
- 修改 `_lake_lineage.py`：bridge 通过 `_get_component` 缓存 + 传入 `session_manager`
- 更新 `tests/api/test_lineage.py`

### 1.6 `prod.yaml` OLAP 配置 + `/health` 暴露 SessionPoolStats [S]
- `configs/prod.yaml` 新增：
  ```yaml
  olap:
    max_result_rows: 50000
    max_query_memory_mb: 1024
    max_concurrent_queries: 8
    query_timeout_seconds: 120
    enable_predicate_pushdown: true
    enable_join: true
    enable_streaming: true
    ducklake_enabled: false
  ```
- 修改 `arrow_lake/api/routers/system.py` health 端点：附加 `duckdb_pool` 统计信息
- 更新 `tests/api/test_health_probes.py`

### 1.7 弃用 `DuckDBConnectionPool` [S] — Review 新增
- 在 `arrow_lake/catalog/connection_pool.py` 加 `DeprecationWarning`
- 修改 `CatalogActor` 支持可选 `session_manager` 注入
- v1.4.0 完全移除 `DuckDBConnectionPool`

---

## Phase 2: 安全加固（~4-5 天）

### 2.1 Gremlin 注入防护 — 白名单方案 [M] ⚠️ CRITICAL
> **Review fix**: 黑名单/正则方案被否决。无论正则多复杂都能绕过（注释注入 `/* */`、`\n` 换行、`.iterate()` 终端步骤、Unicode 变体）。改为**白名单**。
- 修改 `arrow_lake/api/routers/knowledge_graph.py:31`：
  ```python
  _ALLOWED_GREMLIN_STEPS = frozenset({
      "V", "E", "has", "hasLabel", "hasId", "hasNot",
      "out", "in", "both", "outE", "inE", "bothE", "outV", "inV",
      "values", "valueMap", "elementMap", "properties",
      "count", "limit", "range", "order", "by",
      "select", "as", "where", "path", "dedup",
      "group", "groupCount", "project", "union", "fold",
      "sum", "mean", "max", "min",
      "id", "label", "constant", "map", "flatMap",
  })

  def _validate_gremlin(query: str) -> None:
      # 提取所有 .stepName( 模式，检查是否在白名单中
      for match in re.finditer(r"\.(\w+)\s*\(", query):
          if match.group(1) not in _ALLOWED_GREMLIN_STEPS:
              raise HTTPException(status_code=400, detail=f"Forbidden Gremlin step: {match.group(1)}")
  ```
- line 125-127 改用 `_validate_gremlin(req.gremlin)`
- 新建 `tests/api/test_kg_security.py`（8 个测试：.drop()、.drop ()、.\ndrop()、./* */drop()、.iterate()、正常查询放行、Unicode）

### 2.2 RBAC 补全 [L]
> **Review fix**: 调整了部分角色分配（embed/RAG 用 LLM 有成本 → EDITOR；backup list 含基础设施信息 → ADMIN；SQL 查询可 DoS → EDITOR）
- 10 个路由文件添加 `Depends(require_role(...))`：
  | 路由 | 角色 | 端点数 |
  |------|------|--------|
  | `audit.py` | record→EDITOR, query/verify→VIEWER, export→ADMIN | 4 |
  | `search.py` | 全部 VIEWER | 5 |
  | `query.py` | olap/metadata→EDITOR, daft→VIEWER | 3 |
  | `export.py` | create→EDITOR, download→VIEWER | 2 |
  | `lineage.py` | record→EDITOR, history/query→VIEWER | 3 |
  | `quality.py` | filter/dedup→EDITOR, report→VIEWER | 3 |
  | `embedding.py` | create index→EDITOR, embed→EDITOR (LLM 有成本) | 4 |
  | `knowledge_graph.py` | schema/stats/neighbors→VIEWER | 4 |
  | `rag.py` | query/stream/extract→EDITOR (LLM 有成本) | 3 |
  | `backup.py` | list→ADMIN | 1 |
- **Actor 防伪造**: audit.py 和 lineage.py 的 `req.actor` 必须从 `request.state.user.sub` 填充，不从请求体
- RBAC 测试用参数化：1 个 `tests/api/test_rbac.py` 覆盖全部 32 端点（Review 建议）

### 2.3 修复 export 路径穿越 [S] ⚠️ CRITICAL
- 修改 `arrow_lake/api/routers/export.py:89-93`：
  ```python
  output = FilePath(task.output_path)
  if output.is_absolute():
      raise HTTPException(status_code=400, detail="Absolute paths not allowed")
  base_resolved = FilePath(base_dir).resolve()
  file_path = (base_resolved / output).resolve()
  if not file_path.is_relative_to(base_resolved):  # Python 3.9+
      raise HTTPException(status_code=403, detail="Path escapes base directory")
  ```
  > **Review fix**: 用 `is_relative_to()` 替代 `startswith()`（正确惯用法）
- 新建 `tests/api/test_export_security.py`：`../etc/passwd`、绝对路径、`%2e%2e%2f` URL 编码

### 2.4 修复 SQL 分号注入 [S] — Review 新增
- 修改 `arrow_lake/api/models/common.py` 的 `_BLOCKED_SQL_PREFIXES`：
  - 增加分号检查：`if ";" in sql: raise ...`
  - 或修复正则为 `r"(?:^|;\s*)(DROP|DELETE|...)"`
- 新增测试：`"SELECT 1; DROP TABLE"` 被阻断

### 2.5 TLS + CSP 生产配置 [S]
- `configs/prod.yaml` 新增 `api:` 段：
  ```yaml
  api:
    tls_enabled: true
    ssl_certfile: "/etc/arrow-lake/tls/tls.crt"
    ssl_keyfile: "/etc/arrow-lake/tls/tls.key"
    security_headers_enabled: true
    content_security_policy: "default-src 'none'; frame-ancestors 'none'"  # Review: 新增 CSP
    docs_enabled: false
    cors_origins: []
  ```
  > **Review fix**: 新增 CSP header（REST API 的正确策略）
- Helm deployment 加 TLS volume mount，values 加 `tls.secretName`

### 2.6 JWT 黑名单持久化（Redis）[M]
- 修改 `arrow_lake/api/auth_service.py`：
  - `__init__` 新增可选 `redis_client`（同步 `redis.Redis`，与 Phase 1 一致）
  - `revoke_token()` → Redis SET + TTL（可用时）
  - `is_revoked()` → 先查 Redis，回退内存
  - 内存黑名单驱逐逻辑（超 100k 条目时清理过期项）
- `arrow_lake/api/app.py`：构造 `AuthService` 时传入 Redis client
- 更新 `tests/api/test_jwt_auth.py`（含 Redis 不可用回退测试）

### 2.7 安全配置收尾 [S ×4]
- `configs/prod.yaml` 新增 `rate_limit: {enabled: true, default_requests_per_minute: 120, default_burst: 20}`
- `configs/prod.yaml` 的 `audit.hmac_secret_key` 加注释 `# MUST set via ARROW_LAKE__AUDIT__HMAC_SECRET_KEY`
- `/metrics` 端点加认证保护或拆分到独立内部端口（Review: 当前零认证泄露内部状态）
- `deploy/helm/arrow-lake/values.yaml`：`networkPolicy.enabled: true` + Redis 6379 egress
- 添加 `Permissions-Policy: camera=(), microphone=(), geolocation=()` 到安全头（Review 新增）

---

## Phase 3: 运维增强（~2 天）

### 3.1 Helm HPA 模板 [M]
- 新建 `deploy/helm/arrow-lake/templates/hpa.yaml`
- 基于 CPU 利用率 + `arrow_lake_duckdb_pool_active_sessions` 自定义指标
- `values.yaml` 新增 `apiServer.autoscaling` 段（默认 `enabled: false`，需 Redis 就绪后启用）
- 验证：`helm template` + `helm lint`

### 3.2 备份 CronJob 模板 [M]
- 新建 `deploy/helm/arrow-lake/templates/cronjob-backup.yaml`
- 每日 02:00 调用 `POST /api/v1/backup/create`
- `values.yaml` 新增 `backup:` 段（schedule, image, retention）
- 验证：`helm template` 渲染正确

---

## 依赖关系

```
Phase 0 ───→ Phase 1.1 (QueryEngine Protocol)
         ───→ Phase 1.2 (RedisConfig)
              Phase 1.3 (RedisCountingSemaphore) ← 依赖 1.1 + 1.2 ⚠️ 关键路径
              Phase 1.4 (重构 SessionManager) ← 依赖 1.3
              Phase 1.5 (Lineage bypass + 缓存) ← 可与 1.4 并行
              Phase 1.6 (prod.yaml + health) ← 可与 1.4 并行
              Phase 1.7 (弃用 ConnectionPool) ← 可与 1.4 并行

Phase 0 ───→ Phase 2.1 (Gremlin 白名单) — 独立
              Phase 2.2 (RBAC) — 独立
              Phase 2.3 (路径穿越) — 独立
              Phase 2.4 (SQL 分号注入) — 独立 — Review 新增
              Phase 2.5 (TLS + CSP) — 独立
              Phase 2.6 (JWT Redis) ← 依赖 1.2 (RedisConfig)
              Phase 2.7 (安全配置收尾) — 独立

Phase 1 ───→ Phase 3.1 (HPA 依赖分布式 Session)
              Phase 3.2 (CronJob 独立)
```

**关键路径**：Phase 0 → 1.1 → 1.2 → 1.3 → 1.4 → 3.1

## 工作量估算

| Phase | 任务数 | 复杂度 | 预估时间 |
|-------|--------|--------|----------|
| Phase 0 | 4 | S | ~1 天 |
| Phase 1 | 7 | 2L+2M+3S | ~5-6 天 |
| Phase 2 | 7 | 1L+2M+4S | ~4-5 天 |
| Phase 3 | 2 | 2M | ~2 天 |
| **合计** | **20** | | **~12-14 天** |

## 新增依赖

- `redis[hiredis]>=5.0,<6.0` — 生产依赖
- `fakeredis>=2.21` — 开发依赖（测试用）

## 测试策略

- 新增 **~65 个测试**（Review 修正：原估 43 偏低 30-40%）
- 总测试数约 ~3065
- RBAC 测试用参数化：1 个 `test_rbac.py` 覆盖 32 端点
- Redis 测试：fakeredis 单元 + 1 个 `@pytest.mark.integration` 真实 Redis 测试
- 关键未覆盖场景（Review 补充）：
  - Redis acquire 期间连接失败 → 回退行为
  - 多线程并发 acquire → 竞争条件
  - 查询进行中 shutdown → 连接清理
  - 多行/注释 Gremlin 绕过
  - URL 编码路径穿越 `%2e%2e%2f`
  - 信号量 TTL 过期清理
  - JWT 黑名单 Redis 不可用回退

## 验证清单

- [ ] `pytest` 全量通过（含 80% 覆盖率门禁）
- [ ] `ruff check` + `ruff format --check` 干净
- [ ] `mypy` 无新增错误
- [ ] `helm template deploy/helm/arrow-lake/` 渲染成功
- [ ] `bandit -r arrow_lake/` 无 HIGH/CRITICAL
- [ ] Gremlin 白名单：所有未授权步骤被阻断，正常查询放行
- [ ] 路径穿越：`../`、绝对路径、URL 编码、符号链接全部阻断
- [ ] RBAC 未授权访问全部 403
- [ ] SQL 分号注入被阻断
- [ ] `/metrics` 端点有认证保护
- [ ] CSP + Permissions-Policy headers 在生产配置中存在
