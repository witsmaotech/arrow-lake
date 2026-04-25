# M4: Production Readiness 实施计划

**版本**: M4-impl | **日期**: 2026-04-20
**基于**: M3 已完成 (Knowledge Graph + GraphRAG, 164 tests, Gate 通过)
**状态**: 待实施

---

## Context

M1-M3 已交付完整功能: 数据湖存储、RAG、GraphRAG、REST API。M4 将系统从 "功能可用" 提升到 "生产就绪"：

1. **可观测性增强** — 分离 liveness/readiness 探针, OpenTelemetry 集成, 请求关联 ID
2. **安全加固** — JWT 认证 + RBAC 角色权限 + API Key 轮换 + CI 安全扫描
3. **性能基线** — Histogram 指标修复, 性能回归测试, 负载测试, OMTM 仪表板

**关键约束:**
- 不破坏 M1-M3 现有功能 (auth 默认 disabled, OTel 作为可选依赖)
- 遵循现有模式: frozen dataclasses, httpx+tenacity, TDD
- 新代码 80%+ 测试覆盖
- 中文注释用户可见字符串

---

## Week 1: CI/CD 增强 + 可观测性 (Day 1-10)

### Day 1-2: 分离 Liveness / Readiness 探针

**问题**: Helm `deployment.yaml` livenessProbe 和 readinessProbe 都指向 `/health`。K8s 最佳实践是分离: liveness 轻量 (进程存活), readiness 检查依赖 (存储、HugeGraph)。

**修改文件:**
- `arrow_lake/api/routers/system.py` (+50 行): 拆分 `health_check` 为 `health_live` 和 `health_ready`
  - `GET /health/live` → 始终返回 200 (进程存活)
  - `GET /health/ready` → 检查存储 + catalog, 返回 200/503
  - `GET /health` → 保持向后兼容别名
- `arrow_lake/api/auth.py` (+2 行): `_PUBLIC_PATHS` 添加 `/health/live`, `/health/ready`
- `deploy/helm/arrow-lake/templates/deployment.yaml` (+4 行): 使用分离探针路径
- `deploy/helm/arrow-lake/values.yaml` (+4 行): 添加 `healthLivePath`, `healthReadyPath`

**新建文件:**
- `tests/api/test_health_probes.py` (~80 行): 3 个测试 — live 始终 200, ready 存储正常 200/异常 503, /health 向后兼容

### Day 3-4: 请求关联 ID 传播

**问题**: `ObservabilityConfig.correlation_id` 已定义但从未使用。生产调试需要请求 ID 贯穿调用链。

**修改文件:**
- `arrow_lake/config.py` (+3 行): `ApiConfig` 添加 `auto_generate_request_id: bool = True`
- `arrow_lake/api/middleware.py` (+40 行): 新增 `CorrelationIdMiddleware`
  - 从 `X-Request-ID` header 提取, 缺失时自动生成 UUID
  - 设置 `request.state.correlation_id`
  - 响应 header 返回 `X-Request-ID`
- `arrow_lake/api/app.py` (+3 行): 注册 CorrelationIdMiddleware (CORS 之后, Auth 之前)

**新建文件:**
- `tests/api/test_correlation.py` (~60 行): 测试 ID 传播、自动生成、响应 header

### Day 5-6: OpenTelemetry 集成

**问题**: 项目使用 `prometheus_client` 但无分布式追踪。OTel 是行业标准。

**新增依赖** (optional deps group `otel`):
- `opentelemetry-api>=1.24`, `opentelemetry-sdk>=1.24`, `opentelemetry-exporter-otlp-proto-grpc>=1.24`

**修改文件:**
- `pyproject.toml` (+3 行): `[project.optional-dependencies]` 添加 `otel` group
- `arrow_lake/config.py` (+20 行): 新增 `OpenTelemetryConfig(BaseModel)`
  - `enabled: bool = False`, `service_name`, `otel_endpoint`, `trace_sample_rate`
- `arrow_lake/api/app.py` (+15 行): lifespan 中按需启用 OTel (FastAPIInstrumentor)

**新建文件:**
- `arrow_lake/api/telemetry.py` (~60 行): `setup_telemetry(config)` 配置 TracerProvider + OTLP exporter, 依赖缺失时优雅 no-op
- `tests/unit/test_telemetry.py` (~70 行): 测试 disabled no-op、enabled 配置、缺失依赖处理

### Day 7-8: CI Pipeline 增强

**问题**: 当前 CI 只有 lint+test。生产 CI 需要安全扫描和覆盖率门禁。

**修改文件:**
- `pyproject.toml` (+2 行): dev deps 添加 `bandit>=1.7`
- `.github/workflows/ci.yml` (+20 行):
  - 添加 `bandit -r arrow_lake/ -x tests/` 安全扫描 step
  - 添加 `--cov-fail-under=80` 到 pytest (覆盖率门禁)
  - 上传 coverage report artifact

**新建文件:**
- `.github/workflows/security.yml` (~50 行): 每周安全扫描 (bandit + pip-audit)

### Day 9-10: Week 1 Gate

```bash
uv run pytest tests/api/test_health_probes.py tests/api/test_correlation.py -v
uv run pytest tests/unit/test_telemetry.py -v
uv run pytest tests/unit/ tests/api/ -v --cov=arrow_lake --cov-fail-under=80
uv run ruff check arrow_lake/api/
uv run mypy arrow_lake/api/
uv run bandit -r arrow_lake/ -x tests/ -ll
```

---

## Week 2-3: RBAC + 安全加固 (Day 11-22)

### Day 11-12: JWT Token 模型 + 配置

**新增依赖** (optional deps group `jwt`):
- `PyJWT>=2.9`

**修改文件:**
- `pyproject.toml` (+2 行): 添加 `jwt` group
- `arrow_lake/config.py` (+30 行): 新增 `AuthConfig(BaseModel)`
  - `auth_mode: str = "api_key"` (值: `api_key` | `jwt` | `both`)
  - `jwt_secret_key`, `jwt_algorithm`, `jwt_access_token_minutes`, `jwt_refresh_token_days`
- `arrow_lake/exceptions.py` (+6 行): 新增错误码
  - `AUTH_TOKEN_EXPIRED`, `AUTH_INVALID_TOKEN`, `AUTH_INSUFFICIENT_PERMISSIONS`, `AUTH_API_KEY_ROTATION_REQUIRED`
- `arrow_lake/api/errors.py` (+8 行): 新错误码 → HTTP 401/403 映射

**新建文件:**
- `arrow_lake/api/auth_models.py` (~80 行):
  - `Role(StrEnum)`: ADMIN, EDITOR, VIEWER
  - `TokenPayload(BaseModel)`: sub, role, permissions, exp, iat, iss
  - `TokenPair(BaseModel)`: access_token, refresh_token
- `tests/unit/test_auth_models.py` (~60 行)

### Day 13-15: JWT 认证 + Token 端点

**修改文件:**
- `arrow_lake/api/auth.py` (+80 行): 新增 `JwtAuthMiddleware`
  - 从 `Authorization: Bearer <token>` 验证 JWT
  - 提取 role+permissions 到 `request.state.user`
  - `auth_mode="api_key"` 时使用现有 ApiKeyMiddleware
  - `auth_mode="both"` 时两种方式均可
- `arrow_lake/api/deps.py` (+30 行): 新增
  - `get_current_user(request) -> TokenPayload`
  - `require_role(role)` 工厂
  - `require_permission(perm)` 工厂
- `arrow_lake/api/app.py` (+5 行): 按 auth_mode 注册中间件

**新建文件:**
- `arrow_lake/api/auth_service.py` (~100 行): `AuthService` 类
  - `create_access_token(user_id, role, permissions)` / `create_refresh_token(user_id)`
  - `refresh_access_token(refresh_token)` / `verify_token(token)`
  - 纯函数设计便于测试
- `arrow_lake/api/routers/auth.py` (~80 行):
  - `POST /api/v2/auth/token` (接受 API Key, 返回 JWT pair)
  - `POST /api/v2/auth/refresh` (刷新 access token)
  - `GET /api/v2/auth/me` (当前用户信息)
- `tests/unit/test_auth_service.py` (~80 行)
- `tests/api/test_jwt_auth.py` (~120 行)

### Day 16-18: RBAC 角色权限 + Dataset 级权限

**修改文件:**
- `arrow_lake/api/routers/datasets.py` (+15 行): ingest/delete 需 EDITOR+, read 需 VIEWER+
- `arrow_lake/api/routers/query.py` (+5 行): require_role("viewer")
- `arrow_lake/api/routers/search.py` (+5 行): require_role("viewer")
- `arrow_lake/api/routers/export.py` (+5 行): require_role("editor")
- `arrow_lake/api/routers/quality.py` (+5 行): require_role("editor")
- `arrow_lake/api/deps.py` (+20 行): `require_dataset_permission(action)` dataset 级 ACL 检查

**新建文件:**
- `arrow_lake/api/rbac.py` (~90 行):
  - `PermissionChecker`: 角色-权限矩阵求值
  - `RolePermission` frozen dataclass: 定义各角色可执行操作
  - `DatasetACL`: 内存 ACL 存储 (v2.0 升级为 DB)
- `arrow_lake/api/routers/admin.py` (~40 行):
  - `GET /api/v2/admin/users` (ADMIN only)
  - `POST /api/v2/admin/api-keys/rotate` (ADMIN only)
- `tests/unit/test_rbac.py` (~100 行)

### Day 19-20: API Key 轮换支持

**修改文件:**
- `arrow_lake/config.py` (+5 行): `ApiConfig` 添加 `api_key_rotation_days: int = 90`
- `arrow_lake/api/auth.py` (+20 行): ApiKeyMiddleware 检查 key 创建时间, 超期返回 403

**新建文件:**
- `tests/api/test_api_key_rotation.py` (~50 行)

### Day 21-22: Week 2-3 Gate

```bash
uv run pytest tests/unit/test_auth_models.py tests/unit/test_auth_service.py tests/unit/test_rbac.py -v
uv run pytest tests/api/test_jwt_auth.py tests/api/test_api_key_rotation.py -v
uv run pytest tests/ -v --cov=arrow_lake --cov-fail-under=80
uv run ruff check arrow_lake/api/
# 向后兼容: auth disabled 时所有现有测试通过
ARROW_LAKE__API__API_KEY= uv run pytest tests/api/ tests/integration/ tests/e2e/ -v
```

---

## Week 3-4: 性能基线 + OMTM (Day 23-34)

### Day 23-24: Histogram 指标 + 延迟追踪

**问题**: `query_latency_seconds` 是 Gauge (仅最后值), Helm `prometheusrule.yaml` 引用 `..._bucket` 但指标不存在。

**修改文件:**
- `arrow_lake/core/metrics.py` (+30 行):
  - `query_latency_seconds` 从 Gauge 改为 Histogram, bucket: `[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]`
  - 新增 `http_request_duration_seconds` Histogram
  - 新增 `auth_requests_total` Counter (labels: auth_method, status)
- `arrow_lake/api/middleware.py` (+20 行): CorrelationIdMiddleware 中记录 HTTP 请求耗时
- `deploy/helm/arrow-lake/templates/prometheusrule.yaml` (+10 行): 修正 alert rule 使用 `histogram_quantile`

**新建文件:**
- `tests/unit/test_histogram_metrics.py` (~50 行): 验证 bucket 结构、labels、metric 注册

### Day 25-27: 性能回归测试 (CI 集成)

**修改文件:**
- `.github/workflows/ci.yml` (+15 行): 添加 perf-regression job
- `pyproject.toml` (+5 行): 添加 `perf_regression` marker

**新建文件:**
- `tests/benchmark/baselines/ingest_10k.json` (~15 行): 初始基线
- `tests/benchmark/baselines/vector_search_1k.json` (~15 行)
- `tests/benchmark/baselines/fts_search_1k.json` (~15 行)
- `tests/benchmark/test_perf_regression.py` (~100 行): 与基线比较, >20% 回退则失败
- `tests/benchmark/save_baselines.py` (~30 行): 保存新基线的脚本

### Day 28-30: 负载测试 + OMTM 仪表板

**新增依赖** (optional deps group `loadtest`): `locust>=2.31`

**新建文件:**
- `scripts/loadtest/locustfile.py` (~120 行):
  - Vector search / FTS / Hybrid / Ingestion 用户场景
  - 权重分配 + think time
- `scripts/loadtest/config.py` (~30 行): 目标 QPS, ramp-up, 持续时间
- `deploy/grafana/omtm-dashboard.json` (~100 行):
  - OMTM: p95 查询延迟 (目标 <100ms)
  - 辅助面板: QPS, 错误率, 活跃连接

### Day 31-32: 关键路径基准自动化

**新建文件:**
- `tests/benchmark/test_bench_kg_build.py` (~80 行): KG schema + vertex/edge + traversal 基准
- `tests/benchmark/test_bench_rag_pipeline.py` (~80 行): RAG 端到端基准
- `scripts/run_critical_benchmarks.sh` (~30 行): 运行所有关键路径基准

### Day 33-34: M4 Final Gate

```bash
# 全量单元+API测试+覆盖率
uv run pytest tests/unit/ tests/api/ -v --cov=arrow_lake --cov-fail-under=80

# Histogram 指标
uv run pytest tests/unit/test_histogram_metrics.py -v

# 性能回归
uv run pytest tests/benchmark/test_perf_regression.py -v -m perf_regression

# M1-M3 回归
uv run pytest tests/integration/ tests/e2e/ tests/regression/ -v

# 安全扫描
uv run bandit -r arrow_lake/ -x tests/ -ll

# Lint + Type
uv run ruff check arrow_lake/
uv run mypy arrow_lake/
```

---

## 关键设计决策

1. **auth_mode 配置开关** (`api_key` | `jwt` | `both`): 零破坏性变更。现有 api_key 部署不受影响。JWT 为 opt-in。

2. **OTel 作为可选依赖**: 添加到 `[project.optional-dependencies]` 而非核心依赖。`telemetry.py` 依赖缺失时优雅 no-op。避免膨胀基础 Docker 镜像。

3. **内存 RBAC 存储**: 单团队部署场景足够 (PRD: v2 才需多租户)。接口设计预留 DB 升级路径。

4. **Gauge→Histogram 迁移**: 修复 Helm alert rule 已有的 bug (引用 `..._bucket` 但指标是 Gauge)。

5. **性能基线为版本控制 JSON**: 而非外部服务, 使回归测试确定性且自包含。

---

## 非目标 (M4 不包含)

- 多租户隔离 (defer to v2)
- DB 用户/会话存储 (内存足够)
- OAuth2/OIDC 外部身份提供商
- 速率限制中间件 (M5)
- Ray actor 分布式追踪
- 自动金丝雀部署
- SLA/SLO 定义

---

## 文件清单

### 新建文件 (~2,500 行)

| 文件 | 行数 | Week | 说明 |
|------|------|------|------|
| `arrow_lake/api/telemetry.py` | 60 | W1 | OTel 配置 |
| `arrow_lake/api/auth_models.py` | 80 | W2 | JWT/Role/Token 模型 |
| `arrow_lake/api/auth_service.py` | 100 | W2 | Token 创建/验证服务 |
| `arrow_lake/api/rbac.py` | 90 | W3 | 角色权限矩阵 |
| `arrow_lake/api/routers/auth.py` | 80 | W2 | /api/v2/auth/* 端点 |
| `arrow_lake/api/routers/admin.py` | 40 | W3 | /api/v2/admin/* 端点 |
| `tests/api/test_health_probes.py` | 80 | W1 | 探针测试 |
| `tests/api/test_correlation.py` | 60 | W1 | 关联 ID 测试 |
| `tests/unit/test_telemetry.py` | 70 | W1 | OTel 测试 |
| `tests/unit/test_auth_models.py` | 60 | W2 | Auth 模型测试 |
| `tests/unit/test_auth_service.py` | 80 | W2 | Auth 服务测试 |
| `tests/unit/test_rbac.py` | 100 | W3 | RBAC 测试 |
| `tests/api/test_jwt_auth.py` | 120 | W2 | JWT 集成测试 |
| `tests/api/test_api_key_rotation.py` | 50 | W3 | Key 轮换测试 |
| `tests/unit/test_histogram_metrics.py` | 50 | W3 | Histogram 测试 |
| `tests/benchmark/test_perf_regression.py` | 100 | W4 | 性能回归测试 |
| `tests/benchmark/save_baselines.py` | 30 | W4 | 基线保存脚本 |
| `tests/benchmark/baselines/*.json` | 45 | W4 | 性能基线 (3) |
| `tests/benchmark/test_bench_kg_build.py` | 80 | W4 | KG 基准 |
| `tests/benchmark/test_bench_rag_pipeline.py` | 80 | W4 | RAG 基准 |
| `scripts/loadtest/locustfile.py` | 120 | W4 | 负载测试 |
| `scripts/loadtest/config.py` | 30 | W4 | 负载测试配置 |
| `deploy/grafana/omtm-dashboard.json` | 100 | W4 | OMTM 仪表板 |
| `.github/workflows/security.yml` | 50 | W1 | 安全扫描 |
| `scripts/run_critical_benchmarks.sh` | 30 | W4 | 基准运行脚本 |

### 修改文件 (~450 行变更)

| 文件 | 变更 | Week |
|------|------|------|
| `arrow_lake/config.py` | +70 行 (AuthConfig, OpenTelemetryConfig, ApiConfig 扩展) | W1-W2 |
| `arrow_lake/api/routers/system.py` | +50 行 (live/ready 分离) | W1 |
| `arrow_lake/api/auth.py` | +100 行 (JWT 中间件 + key 轮换) | W2-W3 |
| `arrow_lake/api/middleware.py` | +60 行 (CorrelationId + Metrics) | W1-W3 |
| `arrow_lake/api/deps.py` | +50 行 (get_current_user, require_role) | W2 |
| `arrow_lake/api/app.py` | +25 行 (中间件注册 + OTel) | W1-W2 |
| `arrow_lake/api/errors.py` | +8 行 (auth 错误码映射) | W2 |
| `arrow_lake/exceptions.py` | +6 行 (auth 错误码) | W2 |
| `arrow_lake/core/metrics.py` | +30 行 (Gauge→Histogram + 新 metrics) | W3 |
| `pyproject.toml` | +10 行 (optional deps + markers) | W1-W4 |
| `.github/workflows/ci.yml` | +35 行 (security + coverage + perf) | W1-W4 |
| `deploy/helm/arrow-lake/templates/deployment.yaml` | +4 行 (分离探针) | W1 |
| `deploy/helm/arrow-lake/values.yaml` | +4 行 (探针路径) | W1 |
| `deploy/helm/arrow-lake/templates/prometheusrule.yaml` | +10 行 (修复 alert) | W3 |
| `.env.example` | +15 行 (auth/otel 配置示例) | W2 |
