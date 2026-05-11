# Arrow Lake v1.3.0 产品架构设计文档

**版本**: v1.3.0 | **日期**: 2026-05-09
**基于**: v1.0-draft-up + v1.1~v1.3.0 迭代实现
**状态**: 已发布

---

## 文档修订说明

本文档基于 `architecture-v1.0_draft_up.md` 更新，反映 v1.1~v1.3.0 五个版本迭代后的实际代码状态。

| 变更项 | 版本 | 说明 |
|--------|------|------|
| Lake Facade Mixin 拆分 | v1.0 | 8 个 Mixin，God Class 问题已解决 |
| Redis 分布式 Session | v1.3.0 | DuckDB 并发控制从 asyncio.Semaphore 升级为 Redis 计数信号量 |
| Redis JWT 黑名单 | v1.3.0 | JWT 撤销从内存 OrderedDict 升级为 Redis 持久化 |
| HugeGraph 外部部署 | v1.3.0 | 从 docker-compose 内置改为外部独立部署 + 网络互联 |
| Gremlin 安全加固 | v1.3.0 | 阻断危险 Gremlin 模式 (drop/eval/java.lang 等) |
| RBAC 三角色模型 | v1.2 | ADMIN / EDITOR / VIEWER，数据集级权限 |
| Helm 生产就绪 | v1.3.0 | HPA / PDB / Ingress / Secret / CronJob 备份 |
| 健康检查分离 | v1.3.0 | /health/live (liveness) + /health/ready (readiness) |
| 安全头 + TLS + CSP | v1.2 | OWASP 安全加固，生产环境强制 TLS |
| QueryEngine Protocol | v1.3.0 | 查询引擎可替换接口，支持未来分布式 OLAP 迁移 |
| DuckDB Session Pool | v1.3.0 | 空闲连接池 + 弱引用终结 + 慢查询追踪 |
| 备份 CronJob | v1.3.0 | Helm CronJob 每日 02:00 UTC 自动备份 |

---

## 一、系统架构总览

```
                              +---------------------------+
                              |     客户端 / 前端应用       |
                              +-------------+-------------+
                                            |
                              +-------------v-------------+
                              |   API Gateway / Ingress   |
                              +-------------+-------------+
                                            |
                   +------------------------+------------------------+
                   |                        |                        |
        +----------v----------+  +----------v----------+  +----------v----------+
        | Arrow Lake REST API |  |  Python SDK (Lake)  |  |  CLI (Click)        |
        | FastAPI · 15 routers|  |  Facade · 8 Mixins  |  |  15 command groups  |
        +----------+----------+  +----------+----------+  +---------------------+
                   |                        |
          +--------+--------+       +-------+-------+
          | Middleware Chain|       | RAG Engine    |
          | Auth·RateLimit  |       | Retrieve+Gen  |
          | Security·OTel   |       +---+-----+-----+
          +--------+--------+           |     |
                   |                +---v--+  +--v-----------+
          +--------v---+--------+   |DuckDB|  | LLM Provider  |
          | LanceDB SDK| DuckDB  |   |SQL   |  | OpenAI/vLLM   |
          | (数据管理层)|(查询    |   |引擎  |  | Ollama        |
          | 写入/索引   | 分析层) |   |      |  +---------------+
          | Schema演化  |         |   |      |
          | 版本管理    |         |   |      |
          +--------+----+--+--+--+---+------++
                   |     |  |  |           |
                   |     |  |  +-----------+
                   |     |  |  |
          +--------v-----v--v--v--------+
          |      Lance 数据格式层        |
          |  列式+向量+FTS+版本管理       |
          +----+-------------------------+
               |
               +──────────┬──────────────────┬─────────────────+
               |          |                  |                 |
     +---------v---+ +----v-----------+ +----v----------+ +----v----------+
     | Lance Files | | MinIO / S3     | | Redis         | | HugeGraph     |
     | (列式存储)   | | (Blob Storage) | | Session·JWT   | | (图数据库)     |
     +-------------+ +----------------+ | Semaphore     | | (外部部署)     |
                                       +---------------+ +---------------+

          +------------------------------------------------------------+
          |                       部署层                                |
          |  Docker Compose (6 profiles)  |  Helm + Kubernetes          |
          |  api · minio · redis          |  HPA · PDB · Ingress        |
          |  ray-head · ray-worker        |  Secret · CronJob Backup    |
          |  jupyter · turbo-ocr          |  NetworkPolicy              |
          +------------------------------------------------------------+
```

### 分层说明

| 层级 | 组件 | 职责 |
|------|------|------|
| **客户端层** | CLI / REST API / SDK / Jupyter | 用户交互入口 |
| **网关层** | FastAPI + Middleware Chain | HTTP 接口、认证、限流、安全头、可观测 |
| **SDK 层** | Lake facade (8 Mixins) | 统一编程接口，懒初始化组件 |
| **管理层** | LanceDB SDK | 数据写入 / 索引创建 / Schema 演化 / 版本管理 |
| **查询层** | DuckDB + Lance 扩展 | OLAP SQL / 向量搜索 / FTS / 混合搜索 / Session Pool |
| **衍生层** | DuckDB + DuckLake 扩展 | ETL 物化 / 可写工作区 / DML |
| **格式层** | Lance | 列式存储 + 向量索引 + 全文索引 + 版本管理 |
| **存储层** | MinIO (S3) / 本地 FS | Lance 格式持久化 + 媒体二进制 |
| **协调层** | Redis | 分布式信号量 / JWT 黑名单 / Session 协调 |
| **图数据库** | HugeGraph (外部) | 知识图谱 / GraphRAG / Gremlin 遍历 |
| **外部层** | LLM / Ray / OTel | 生成 / 分布式计算 / 可观测 |

---

## 二、核心组件职责矩阵

| 组件 | 职责 | 读写 | v1.0 规划 | v1.3.0 实际状态 |
|------|------|------|----------|---------------|
| **Lance** | 统一数据格式 | SSOT | 已有 | 生产使用，7 种分块策略 |
| **LanceDB SDK** | 数据管理层 | 写入+管理 | 已有 | 完整 CRUD + 版本 + 索引 |
| **DuckDB** | 查询分析层 | 查询为主 | OLAP+Catalog | Session Pool + 资源治理 + lance/ducklake 扩展 |
| **DuckLake** | 可写衍生层 | 完整 DML | 新增 | DuckDB 扩展加载，ETL 物化 |
| **MinIO** | S3 存储后端 | 读写 | 配置就绪 | 生产集成，storage_options 接通 |
| **Redis** | 分布式协调 | 读写 | 不存在 | v1.3.0 新增：信号量 + JWT 黑名单 |
| **HugeGraph** | 知识图谱 | 读写 | 新增 | 外部部署，Gremlin 安全加固 |
| **RAG Engine** | 检索增强生成 | 读写 | 新增 | 完整 Pipeline + GraphRAG + 流式 |
| **FastAPI** | HTTP 接口 | — | 36 端点 | 15 routers，RBAC 三角色，安全头 |
| **Ray** | 分布式计算 | — | 已有 | Ray Cluster + GPU Autoscaler |
| **Prometheus + OTel** | 可观测性 | — | 基础 metrics | 完整 traces + metrics + 健康检查 |

---

## 三、代码规模与模块组织

### 代码规模

| 指标 | v1.0 规划 | v1.3.0 实际 |
|------|----------|------------|
| Python 源文件 | ~120 | 182 |
| 代码行数 | ~20,000 | 34,134 |
| 测试文件 | ~100 | 264 (44,335 行) |
| Cookbook 示例 | ~20 | 43 |
| 配置段 | ~20 | 31 |
| API Routers | ~10 | 15 |

### 模块组织

| 模块 | 文件数 | 职责 |
|------|--------|------|
| `config/` | 13 | Pydantic 配置模型，31 个子配置，4 层覆盖 |
| `ingest/` | 16 | 数据摄入管道：解析 → 分块(7 策略) → Lance 写入 |
| `query/` | 19 | OLAP/FTS/向量/混合/分面/集成 + DuckDB Session Pool + Redis 信号量 |
| `api/` | 17 | FastAPI REST API：15 路由器 + JWT/RBAC 认证 + 限流 + 安全头 |
| `rag/` | 7 | RAG 管道：检索 → 上下文组装 → LLM 生成 + GraphRAG |
| `knowledge_graph/` | 8 | HugeGraph 客户端 + 实体抽取 + 图检索 + Vermeer OLAP |
| `quality/` | 9 | 质量过滤 + 去重(SHA-256/pHash) + NeMo Curator |
| `workflow/` | 10 | Metaflow 工作流 + Argo 集成 + 审计追踪 + 回滚 |
| `catalog/` | 5 | Ray Actor 目录 + DuckDB 连接池 + 血缘存储 |
| `ray_runtime/` | 5 | Ray 集群管理 + GPU 自动扩缩 |
| `core/` | 5 | 日志(structlog) + 指标(Prometheus) + 断路器 + 验证 |
| `storage/` | 3 | Blob 存储(S3/MinIO CRUD) + 生命周期分层 |
| `embed/` | 4 | 嵌入编码(HuggingFace / OpenAI API / Ollama) |
| `cli/` | 16 | Click CLI：15 个子命令组 |
| `ops/` | 3 | 备份/恢复(SHA-256 校验 + 清单) |

---

## 四、v1.3.0 关键新增设计

### 4A. Redis 分布式协调

v1.3.0 引入 Redis 作为分布式协调层，解决两个核心问题：

1. **DuckDB 并发控制**：多实例部署时，asyncio.Semaphore 无法跨进程协调
2. **JWT 撤销持久化**：内存黑名单在 API 重启后丢失

#### Redis 计数信号量 (`query/_redis_semaphore.py`)

```python
class RedisCountingSemaphore:
    """原子计数信号量 — Lua 脚本保证 acquire/release 原子性"""

    # Lua 原子操作
    _LUA_ACQUIRE = """
        local current = redis.call('INCR', KEYS[1])
        if current == 1 then redis.call('EXPIRE', KEYS[1], ARGV[2]) end
        if current <= tonumber(ARGV[1]) then return 1
        else redis.call('DECR', KEYS[1]); return 0 end
    """
    _LUA_RELEASE = """
        local current = redis.call('DECR', KEYS[1])
        if current < 0 then redis.call('INCR', KEYS[1]) end
        return current
    """
```

**双后端模式**：

| 模式 | 实现 | 适用场景 |
|------|------|---------|
| Redis (首选) | `RedisCountingSemaphore` | 多实例部署、生产环境 |
| 本地回退 | `threading.Semaphore` | 单实例开发、Redis 不可用时 |

**配置**：

```python
class RedisConfig(BaseModel):
    enabled: bool = False                          # 启用 Redis
    url: str = "redis://localhost:6379/0"           # 连接 URL
    password: str = ""                             # 密码
    ssl: bool = False                              # SSL
    semaphore_key_prefix: str = "arrow_lake:semaphore:"
    semaphore_ttl_seconds: int = 300               # 信号量 TTL
    redis_pool_size: int = 10                      # 连接池大小
```

#### Redis JWT 黑名单 (`api/auth_service.py`)

```python
class AuthService:
    # 双后端黑名单
    _blacklist: OrderedDict[str, float]  # 内存 (LRU, 上限 100K)
    _redis: Redis | None                  # Redis 持久化

    async def revoke_token(self, jti: str) -> None:
        if self._redis:
            # Redis 持久化，TTL = refresh_token 生命周期
            await self._redis.setex(
                f"jwt:blacklist:{jti}",
                self._refresh_days * 86400 + 3600,
                "1"
            )
        self._blacklist[jti] = time.time()  # 内存回退

    async def is_revoked(self, jti: str) -> bool:
        if self._redis:
            return bool(await self._redis.exists(f"jwt:blacklist:{jti}"))
        return jti in self._blacklist
```

**Redis 不可用时自动降级到内存黑名单，不阻断服务**。

### 4B. DuckDB Session Pool

v1.3.0 将 DuckDB 查询资源治理从简单的 `asyncio.Semaphore` 升级为完整的连接池：

```python
class DuckDBSessionManager:
    """DuckDB 会话池 — 连接复用 + 资源治理 + 健康检查"""

    def __init__(self, olap_config, storage_config=None, *,
                 semaphore=None,              # Redis 或 threading.Semaphore
                 slow_query_threshold_ms=5000,
                 idle_timeout_seconds=300,
                 max_session_lifetime_seconds=3600): ...

    def acquire(self, timeout=None, load_ducklake=False) -> _ManagedSession:
        """获取会话 — 信号量限流 → 空闲池复用 → 新建连接"""
```

**设计特性**：

| 特性 | 实现 | 说明 |
|------|------|------|
| 连接复用 | `_idle_pool: deque[_IdleConnection]` | 避免每次查询重建 DuckDB 连接 |
| 资源治理 | `memory_limit` + `statement_timeout` + `threads` | 每连接独立配置 |
| 健康检查 | `SELECT 1` 验证空闲连接 | 归还前验证，失败则丢弃 |
| 弱引用终结 | `weakref.finalize()` | 防止连接泄漏 |
| 慢查询追踪 | `record_slow_query()` | Prometheus 暴露 |
| 生命周期限制 | `max_session_lifetime_seconds` | 防止长寿命连接 |

**QueryEngine Protocol**：

```python
@runtime_checkable
class QueryEngine(Protocol):
    def acquire(self, *, timeout, load_ducklake) -> Any: ...
    def get_stats(self) -> Any: ...
    def shutdown(self) -> None: ...
    @property
    def pool_size(self) -> int: ...
```

**为未来迁移到分布式 OLAP (MotherDuck / HTAP / StarRocks) 预留接口**。

### 4C. HugeGraph 外部部署 + Gremlin 安全加固

#### 部署变更

| 方面 | v1.2 (内置) | v1.3.0 (外部) |
|------|-----------|-------------|
| 部署位置 | docker-compose 内置 | 独立部署 `/home/witshine/wits-projs/hugegraph` |
| 网络连接 | 同一 compose 网络 | `docker network connect arrow-lake_arrow-lake-net hg-server` |
| 生命周期 | 随 Arrow Lake 启停 | 独立管理 |
| 配置 | compose 环境变量 | `hg-net` 外部网络 |

#### Gremlin 注入防护

```python
# arrow_lake/knowledge_graph/client.py
_BLOCKED_GREMLIN_PATTERNS = [
    r"\.drop\(\s*\)",          # 删除操作
    r"\beval\s*\(",            # 代码执行
    r"\bSystem\b",             # 系统访问
    r"\bjava\.lang\b",         # Java 反射
    r"\bRuntime\b",            # 运行时操作
    r"\.inject\(",             # 数据注入
    r"\bThread\b",             # 线程操作
    r"\bProcess\b",            # 进程操作
]
```

**顶点 ID 验证**：`^[a-zA-Z0-9_\-一-鿿　-〿＀-￯:.\s]+$`

### 4D. Helm 生产就绪

v1.3.0 新增 5 个 Helm 模板，覆盖生产 Kubernetes 部署：

| 模板 | API 版本 | 功能 |
|------|---------|------|
| `hpa.yaml` | autoscaling/v2 | CPU + Memory 双指标自动扩缩，2~8 副本 |
| `pdb.yaml` | policy/v1 | 最少可用 1 副本，防止全部驱逐 |
| `ingress.yaml` | networking.k8s.io/v1 | TLS 可配置，路径前缀路由 |
| `secret.yaml` | v1 (Opaque) | API Key / JWT Secret / Audit HMAC / Redis Password |
| `cronjob-backup.yaml` | batch/v1 | 每日 02:00 UTC，curl POST /api/v1/backup/create |

**HPA 扩缩行为**：

| 方向 | 策略 | 稳定窗口 |
|------|------|---------|
| Scale Up | 100% 或 2 pods / 60s | 60s |
| Scale Down | 10% / 60s | 300s |

**SLO 阈值**：

| 指标 | 目标 |
|------|------|
| P95 延迟 | < 1.0s |
| P99 延迟 | < 5.0s |
| 错误预算 | 0.1% |
| 可用性 | 99.9% |

### 4E. 安全加固矩阵

| 安全维度 | v1.2 实现措施 | v1.3.0 增强 |
|---------|-------------|-----------|
| **认证** | JWT (HS256/RS256/ES256/PS256) + API Key | Redis JWT 黑名单持久化 |
| **授权** | RBAC (ADMIN/EDITOR/VIEWER) | 数据集级权限，`require_role()` 依赖注入 |
| **传输安全** | TLS 可配置 | 生产强制 TLS，安全头 (HSTS/CSP/X-Frame) |
| **限流** | Token Bucket | 生产 120 req/min，burst 20 |
| **注入防护** | SQL 参数化 | Gremlin 模式阻断 + 顶点 ID 验证 |
| **审计** | HMAC 完整性 | 完整审计追踪 + 异常检测 |
| **容器安全** | 非 root (UID 1000) | 只读文件系统 + 能力丢弃 + PIDs 限制 |

---

## 五、中间件链设计

v1.3.0 的 API 请求经过 10 层中间件处理（按执行顺序）：

```
HTTP Request
    │
    ├── 1. CORS Middleware          # 跨域资源共享
    ├── 2. Exception Handlers       # 全局错误处理
    ├── 3. GZip Middleware          # 响应压缩 (≥1000 bytes)
    ├── 4. Metrics Middleware       # Prometheus HTTP 请求耗时
    ├── 5. Request Size Limit       # max_request_size_bytes 限制
    ├── 6. Security Headers         # CSP/HSTS/X-Frame/X-Content-Type
    ├── 7. Rate Limiting            # Token Bucket 限流 (可选)
    ├── 8. API Key Auth             # X-API-Key 验证 (可选)
    ├── 9. Correlation ID           # X-Request-ID 请求追踪
    ├── 10. JWT Authentication      # Bearer Token 验证 (可选)
    │
    ▼
Router Handler → Lake API → Query Engine / Storage
```

**安全头**：

```
X-Content-Type-Options: nosniff
Referrer-Policy: strict-origin-when-cross-origin
Strict-Transport-Security: max-age=31536000; includeSubDomains
Permissions-Policy: camera=(), microphone=(), geolocation=()
X-Frame-Options: DENY
Content-Security-Policy: default-src 'none'; frame-ancestors 'none'  (仅生产)
```

---

## 六、认证与授权设计

### JWT 双模式认证

| 模式 | 配置值 | 说明 |
|------|--------|------|
| `api_key` | `auth.auth_mode = "api_key"` | X-API-Key 头，向后兼容 |
| `jwt` | `auth.auth_mode = "jwt"` | Bearer Token |
| `both` | `auth.auth_mode = "both"` | 同时支持两种方式 |

### Token 生命周期

| Token | 默认有效期 | 存储 |
|-------|----------|------|
| Access Token | 30 分钟 | 客户端内存 |
| Refresh Token | 7 天 | 客户端持久化 |
| 黑名单 | Refresh Token 生命周期 + 1h | Redis (TTL) / 内存 (LRU 100K) |

### RBAC 三角色模型

```python
class Role(StrEnum):
    ADMIN = "admin"       # 全部操作 + 用户管理
    EDITOR = "editor"     # 数据集读写 + Ingest/Query/Search
    VIEWER = "viewer"     # 数据集只读 + Query/Search

# 端点级权限控制
@router.post("/api/v1/datasets/{name}/ingest")
async def ingest_data(..., role: Role = Depends(require_role(Role.EDITOR))):
    ...
```

---

## 七、数据流详细设计

### 7A. 摄入流程

```
[原始文件/URL/PDF/图片/视频]
    │
    v
+--- Ingestor.ingest_mixed() --------------------------------+
|                                                             |
| 1. 文件分类                                                 |
|    ├─ 文本 → text_content                                   |
|    ├─ 图片 → ImageProcessor (缩略图 + EXIF)                  |
|    ├─ 视频 → VideoProcessor (关键帧提取)                     |
|    └─ PDF  → Kreuzberg 解析 / TurboOCR (GPU)                 |
|                                                             |
| 2. 分块 (7 策略)                                            |
|    ├─ recursive / semantic / sentence / fixed_size           |
|    ├─ markdown_heading / html_section /None                  |
|    └─ QualityFilterRegistry 自动过滤                         |
|                                                             |
| 3. 写入 Lance (LanceDB SDK)                                 |
|    └─ metadata + vectors + text_content                     |
|                                                             |
| 4. 媒体上传 MinIO (大文件)                                   |
|    ├─ original → MinIO                                      |
|    └─ S3 URI → Lance 引用列                                  |
|                                                             |
| 5. 审计记录 → AuditTrail.record()                            |
| 6. 血缘记录 → LineageStore.record_event()                   |
+-------------------------------------------------------------+
```

### 7B. 查询流程 (Redis 信号量协调)

```
[SQL / 搜索请求]
    │
    v
+--- API Router ---------------------------------------------------+
|                                                                  |
| 1. JWT/Auth 验证 + RBAC 权限检查                                  |
|                                                                  |
| 2. lake.get_session_manager().acquire()                          |
|    ├─ Redis 信号量 acquire (INCR + cap, Lua 原子)                |
|    └─ 或 threading.Semaphore (Redis 不可用时)                     |
|                                                                  |
| 3. 从空闲池获取 DuckDB 连接                                       |
|    ├─ 验证连接健康 (SELECT 1)                                     |
|    └─ 不健康则丢弃，新建连接                                       |
|                                                                  |
| 4. 执行查询                                                      |
|    ├─ Lance 数据: __lance_scan() / lance_vector_search()         |
|    ├─ DuckLake: ATTACH TYPE ducklake + DML                       |
|    └─ 资源治理: memory_limit + statement_timeout                  |
|                                                                  |
| 5. 释放连接                                                      |
|    ├─ 归还空闲池 (标注归还时间)                                    |
|    ├─ Redis 信号量 release (DECR, Lua 原子)                      |
|    └─ Prometheus 指标记录                                         |
+------------------------------------------------------------------+
```

### 7C. RAG 查询流程

```
[用户问题]
    │
    v
+--- RAGPipeline.query() -------------------------------------+
|                                                             |
| 1. 并行检索                                                 |
|    ├─ lance_vector_search()  → DuckDB → Lance 向量索引       |
|    ├─ lance_fts()             → DuckDB → Lance FTS 索引      |
|    ├─ lance_hybrid_search()   → DuckDB → Lance RRF 融合      |
|    └─ HugeGraph 遍历         → Gremlin API → 子图三元组       |
|                                                             |
| 2. 上下文组装                                               |
|    ├─ Token 预算管理 (ContextWindow)                         |
|    ├─ 结果去重 + 引用追踪 (ContextCitation)                   |
|    └─ 图三元组合并 (GraphRAG)                                |
|                                                             |
| 3. Prompt 渲染 (Jinja2 模板 + PromptRegistry)               |
|                                                             |
| 4. LLM 生成 (via Provider)                                  |
|    ├─ 同步: generate() → RAGResponse                        |
|    └─ 流式: generate_stream() → AsyncIterator[str]           |
|                                                             |
| 5. 引用标注 + 返回                                           |
+-------------------------------------------------------------+
```

### 7D. 备份流程

```
[CronJob / API 调用]
    │
    v
+--- BackupManager.create_backup() ---------------------------+
|                                                             |
| 1. 收集目标数据集列表                                        |
|                                                             |
| 2. 逐数据集导出 Lance 版本                                   |
|    └─ SHA-256 分块校验 (8MB chunks)                          |
|                                                             |
| 3. 生成备份清单 (BackupManifest)                             |
|    ├─ datasets: [name, version, row_count, file_hash]        |
|    ├─ blob_prefixes: [S3 URI list]                           |
|    └─ file_hashes: {path: sha256}                            |
|                                                             |
| 4. 原子上传 (staging → copy → delete staging)               |
|                                                             |
| 5. 返回 BackupInfo (frozen dataclass)                       |
+-------------------------------------------------------------+
```

---

## 八、计算框架层

### 8A. DARMU 栈

| 首字母 | 框架 | 职责 | 部署位置 |
|--------|------|------|---------|
| **Da** | Daft | DataFrame 引擎，Ingest 文件读取 + 编程式 ETL | 嵌入 API 进程 |
| **R** | Ray | 分布式计算，CatalogActor + GPU 调度 + 并行 map | Ray Cluster (独立) |
| **M** | Metaflow | 工作流编排，质量管道 + 端到端 Flow + 调度 | 嵌入 API 进程 |
| **U** | DuckDB | OLAP SQL + Lance/DuckLake 扩展 + Session Pool | 嵌入 API 进程 |

### 8B. 三框架协作关系

```
┌──────────────────────────────────────────────────────────┐
│                    Metaflow 工作流                        │
│  (编排层: 步骤顺序、重试、调度、审计)                       │
│                                                          │
│  ┌─────────┐   ┌──────────┐   ┌──────────┐              │
│  │  Ingest │ → │  Quality │ → │  Embed   │   ...       │
│  │  Step   │   │  Step    │   │  Step    │              │
│  └────┬────┘   └────┬─────┘   └────┬─────┘              │
│       │              │              │                     │
│       ▼              ▼              ▼                     │
│  ┌─────────────────────────────────────────────┐         │
│  │            Lake Facade API                   │         │
│  └────────┬──────────┬───────────┬─────────────┘         │
│           │          │           │                          │
│           ▼          ▼           ▼                          │
│     ┌──────────┐ ┌────────┐ ┌──────────┐                  │
│     │   Daft   │ │ DuckDB │ │   Ray    │                  │
│     │ 文件读取  │ │ SQL OLAP│ │ 分布式    │                  │
│     │ ETL 转换  │ │ 向量/FTS│ │ Catalog  │                  │
│     │ 惰性求值  │ │ 混合搜索│ │ GPU 推理  │                  │
│     └──────────┘ └────────┘ └──────────┘                  │
│                                                         │
│  基础设施: Ray Cluster + Redis 协调                       │
└──────────────────────────────────────────────────────────┘
```

---

## 九、健康检查与可观测性

### 9A. 健康检查端点

| 端点 | 用途 | 检查内容 | K8s Probe |
|------|------|---------|-----------|
| `/health/live` | 进程存活 | 始终返回 200 | livenessProbe |
| `/health/ready` | 依赖就绪 | LanceDB 存储 + DuckDB 连接池 + MinIO | readinessProbe |
| `/health` | 向后兼容 | 同 readiness | — |
| `/metrics` | Prometheus 指标 | HTTP 耗时 + DuckDB 池统计 | — |

### 9B. Readiness 检查依赖链

```
GET /health/ready 检查顺序:
  1. LanceDB 存储连接 (本地/S3)
  2. MinIO S3 可达性 (如 backend=minio)
  3. DuckDB 连接池状态
     ├─ pool_size
     ├─ active_sessions
     ├─ queued_requests
     ├─ total_queries
     └─ total_errors
  4. HugeGraph REST API (如启用, 非阻断)
  5. Ray Cluster (如启用, 非阻断)
```

### 9C. OpenTelemetry Span 覆盖

| 操作 | Span 名称 | 关键属性 |
|------|----------|---------|
| Ingest | `arrow_lake.ingest` | dataset, row_count, duration_ms |
| OLAP 查询 | `arrow_lake.olap.query` | sql_hash, row_count, duration_ms |
| 向量搜索 | `arrow_lake.vector.search` | dataset, k, ef, duration_ms |
| RAG 查询 | `arrow_lake.rag.query` | strategy, top_k, llm_model |
| KG 构建 | `arrow_lake.kg.build` | dataset, entity_count |
| Redis 信号量 | `arrow_lake.redis.semaphore` | acquire/release, wait_ms |

---

## 十、配置设计

### 10A. 配置结构 (31 段)

```python
class ArrowLakeConfig(BaseModel):
    # 核心基础设施
    storage: StorageConfig          # MinIO/S3/本地
    compute: ComputeConfig          # Ray, GPU, Workers
    observability: ObservabilityConfig  # Metrics, Logging
    http: HttpConfig                # 超时, 重试

    # 媒体处理
    media: MediaConfig              # 缩略图, 预览
    embedding: EmbeddingConfig      # HF/OpenAI/Ollama
    decode: DecodeConfig            # 解码质量

    # 搜索引擎
    vector: VectorSearchConfig      # IVF_PQ, HNSW
    fts: FullTextSearchConfig       # Tantivy, 分词
    hybrid: HybridSearchConfig      # RRF 融合
    faceted: FacetedSearchConfig    # 分面搜索
    ensemble: EnsembleSearchConfig  # 集成搜索

    # 分析引擎
    olap: OlapConfig                # DuckDB 查询治理
    daft: DaftConfig                # Daft DataFrame

    # 数据管理
    quality: QualityConfig          # 过滤, 去重
    lifecycle: LifecycleConfig      # S3 分层存储
    lineage: LineageConfig          # 数据血缘
    export: ExportConfig            # CSV/Parquet/JSON

    # 工作流
    workflow: WorkflowConfig        # Metaflow
    argo: ArgoConfig                # Argo 集成
    autoscale: AutoscaleConfig      # Ray GPU 扩缩

    # API / 网关
    api: ApiConfig                  # TLS, CORS, Docs
    auth: AuthConfig                # JWT, RBAC
    rate_limit: RateLimitConfig     # Token Bucket
    audit: AuditConfig              # HMAC 签名

    # AI/ML
    llm: LLMConfig                  # LLM Provider
    rag: RAGConfig                  # RAG 管道
    hugegraph: HugeGraphConfig      # 图数据库

    # 运维
    redis: RedisConfig              # v1.3.0 分布式协调
    document: DocumentConfig        # 文档处理
    opentelemetry: OpenTelemetryConfig  # 追踪
```

### 10B. 环境变量覆盖

```bash
# 前缀: ARROW_LAKE__
# 嵌套分隔符: __
# 优先级: YAML > 环境变量 > .env > 代码默认值

ARROW_LAKE__STORAGE__BACKEND=minio
ARROW_LAKE__REDIS__ENABLED=true
ARROW_LAKE__REDIS__URL=redis://redis:6379/0
ARROW_LAKE__OLAP__MAX_CONCURRENT_QUERIES=8
ARROW_LAKE__AUTH__AUTH_MODE=jwt
```

### 10C. Dev vs Prod 关键差异

| 维度 | Dev | Prod |
|------|-----|------|
| 存储 | minio (localhost) | s3 (AWS) |
| Redis | disabled | enabled |
| TLS | disabled | enabled |
| 安全头 | default | CSP + HSTS |
| 限流 | disabled | 120/min |
| OLAP 并发 | 无限制 | 8 并发 + 1GB 内存 |
| 日志级别 | DEBUG | WARNING |
| 去重策略 | exact (标记) | both (移除) |
| Parquet 压缩 | snappy | zstd |
| 生命周期 | disabled | 30d→90d→365d |

---

## 十一、部署架构

### 11A. Docker Compose Profile 矩阵

| Profile | 服务 | 用途 |
|---------|------|------|
| `core` | api, minio, minio-init, redis | 最小生产 |
| `dev` | core + ray-head, ray-worker, jupyter + 源码挂载 | 开发 |
| `compute` | ray-head, ray-worker | 计算扩展 |
| `gpu` | core + compute + GPU 资源 | GPU 加速 |
| `monitoring` | core + compute + prometheus | 可观测 |
| `ocr` | turbo-ocr (GPU) | OCR 处理 |

### 11B. 服务资源限制

| 服务 | 内存限制 | CPU 限制 | PIDs | 健康检查 |
|------|---------|---------|------|---------|
| API | 2G | 1.0 | 256 | /health (30s) |
| MinIO | 1G | 0.5 | 512 | mc ready (10s) |
| Redis | 512M | 0.5 | 256 | redis-cli ping (10s) |
| Ray Head | 4G | 2.0 | 1024 | ray status (15s) |
| Ray Worker | 4G | 2.0 | 1024 | disabled |
| Jupyter | 2G | 1.0 | 256 | /lab (15s) |
| TurboOCR | 2G + GPU | 1.0 | 64 | /health (30s) |

### 11C. 网络拓扑

```
arrow-lake-net (172.30.0.0/16)
├── api
├── minio
├── redis
├── ray-head
├── ray-worker
├── jupyter (dev only)
└── turbo-ocr (ocr only)

hg-net (external)
├── api (connected)
└── hg-server (外部 HugeGraph 实例)
```

---

## 十二、设计模式总览

| 模式 | 应用场景 | 代码位置 |
|------|---------|---------|
| **Mixin 组合** | Lake facade 通过 8 个 Mixin 组合能力 | `_lake_*.py` |
| **懒初始化** | `_get_component(key, factory)` 按需创建 | `__init__.py` |
| **Protocol 接口** | QueryEngine / StorageProtocol / EmbeddingEncoderProtocol | `_protocols.py` |
| **双后端降级** | Redis Semaphore → threading.Semaphore | `_redis_semaphore.py` |
| **双后端降级** | Redis JWT 黑名单 → 内存 OrderedDict | `auth_service.py` |
| **对象池** | DuckDB 空闲连接池 + 弱引用终结 | `session_manager.py` |
| **桥接模式** | 每个搜索领域独立的 Bridge 类 | `query/*.py` |
| **插件注册** | QualityFilterRegistry / PromptRegistry / FlowRegistry | 各模块 |
| **断路器** | TurboOcrClient / ApiEmbeddingEncoder / LLM Provider | 各模块 |
| **Lua 原子操作** | Redis 信号量 acquire/release | `_redis_semaphore.py` |
| **应用工厂** | `create_app(config)` FastAPI 实例 | `api/app.py` |
| **纯 ASGI 函数** | 中间件实现为函数而非类 | `api/middleware.py` |

---

## 十三、风险与缓解

| 风险 | 影响 | 概率 | 缓解措施 | 回滚路径 |
|------|------|------|---------|---------|
| DuckDB Lance 扩展版本耦合 | 高 | 中 | 版本锁定 1.5.2 + PyArrow fallback | `lance_scan_mode = "pyarrow_fallback"` |
| Redis 单点故障 | 中 | 低 | 内存降级 + AOF 持久化 | 自动切换 threading.Semaphore |
| DuckDB Session 泄漏 | 中 | 低 | 弱引用终结 + 生命周期限制 + Prometheus 监控 | `idle_timeout_seconds` 调整 |
| HugeGraph 外部网络断连 | 中 | 中 | tenacity 重试 + 非阻断健康检查 | KG 功能降级 |
| Gremlin 注入 | 高 | 低 | 模式阻断 + ID 验证 | 日志告警 |
| LLM API 延迟 | 中 | 高 | SSE 流式 + 本地 vLLM + 断路器 | P95 > 10s 降级 |
| HPA 扩缩抖动 | 低 | 中 | 300s 稳定窗口 + 10% 缓慢缩容 | 手动调整 min/max |
| JWT 黑名单内存溢出 | 低 | 低 | LRU 上限 100K + Redis TTL | Redis 优先 |

---

## 十四、成本模型

### 运行成本估算

| 组件 | 规格 | 月成本 (估算) | 说明 |
|------|------|-------------|------|
| Arrow Lake API | 2 vCPU, 4GB RAM | $20-40 | HPA 2-8 副本 |
| MinIO | 100GB SSD | $10-20 | S3 兼容存储 |
| Redis | 512MB RAM | $5-10 | v1.3.0 新增 |
| DuckDB | 嵌入式 | $0 | 共享 API 资源 |
| Ray Cluster | 4 vCPU, 16GB RAM | $40-80 | 可选, GPU 额外 |
| HugeGraph | 4 vCPU, 8GB RAM | $30-50 | 外部独立部署 |
| LLM API | 按调用量 | $10-200 | 取决于查询量 |
| Prometheus + Grafana | 1 vCPU, 2GB RAM | $10-15 | 可观测性 |
| **合计 (最小生产)** | | **$125-415/月** | 含 Redis，不含 GPU + LLM |

### 缩放触发器

| 指标 | 阈值 | 扩展动作 |
|------|------|---------|
| API CPU | > 70% | HPA 自动扩容 (2→8 pods) |
| API 内存 | > 80% | HPA 自动扩容 |
| 并发查询 | > 8 QPS | 增加 max_concurrent_queries |
| 数据量 | > 10M 行 | MotherDuck 迁移 |
| 向量索引 | > 1M 向量 | Ray GPU 集群 |
| KG 节点 | > 1M | HugeGraph 集群模式 |

---

## 十五、版本演进里程碑

| 里程碑 | 版本 | 核心交付 | 状态 |
|--------|------|---------|------|
| M0 | v1.0 | Mixin 拆分 + DuckDB 查询治理 + lance 扩展 + storage_options | ✅ |
| M1 | v1.1 | 生产加固 + 可观测性 + S3 集成 | ✅ |
| M2 | v1.2 | 文档处理管线 + 安全加固 + RAG/KG + 向量化管线 | ✅ |
| M3 | v1.2.1 | 五方评审修复 + 全量测试通过 | ✅ |
| M4 | v1.2.2 | 向量化管线 + chonkie 兼容 + 全量示例验证 | ✅ |
| M5 | **v1.3.0** | Redis 分布式 Session + JWT 黑名单 + HugeGraph 安全 + Helm 生产就绪 | ✅ |

---

## 附录 A: API 路由器清单 (15 个)

| 路由器 | 前缀 | 核心端点 | 最低角色 |
|--------|------|---------|---------|
| system | `/` | /health/live, /health/ready, /metrics, /version | — |
| auth | `/api/v1/auth` | /login, /refresh, /verify, /revoke | — |
| datasets | `/api/v1/datasets` | /, /{name}, /{name}/ingest, /{name}/compact | VIEWER+ |
| search | `/api/v1/search` | /vector, /fts, /hybrid, /faceted, /ensemble | VIEWER |
| query | `/api/v1/query` | /sql, /olap, /daft | VIEWER |
| export | `/api/v1/export` | /csv, /parquet, /json | VIEWER |
| embedding | `/api/v1/embedding` | /compute, /add, /indices | EDITOR+ |
| quality | `/api/v1/quality` | /filter, /dedup | EDITOR |
| lineage | `/api/v1/lineage` | /record, /history, /query | VIEWER |
| audit | `/api/v1/audit` | /record, /verify, /query, /analyze | VIEWER |
| backup | `/api/v1/backup` | /create, /restore, /list, /delete | ADMIN |
| rag | `/api/v1/rag` | /query, /stream, /extract, /history | VIEWER |
| kg | `/api/v1/kg` | /build, /query, /schema, /stats | EDITOR |
| admin | `/api/v1/admin` | /catalog, /health, /flows | ADMIN |

---

## 附录 B: 备份策略

| 组件 | 备份方式 | 频率 | 保留 | 实现 |
|------|---------|------|------|------|
| Lance 数据 | SHA-256 分块校验 + 清单 | 每日 02:00 | 7 天 | Helm CronJob |
| DuckLake 工作区 | DuckLake snapshot 导出 | 每日 | 3 天 | 可重建 |
| HugeGraph | Gremlin dump → JSON | 每周 | 4 周 | 外部管理 |
| Redis | AOF 持久化 | 实时 | — | docker volume |
| 配置 | YAML + .env (Git) | 每次变更 | Git 历史 | 版本控制 |

---

## 附录 C: 相关文档

- [v1.0 架构设计文档 (优化版)](architecture-v1.0_draft_up.md)
- [v1.3.0 实现计划](../impls/implementation-plan-v1.3.0.md)
- [CHANGELOG](../../CHANGELOG.md)
- [部署指南](../cookbook/12-deployment.md)
- [REST API 参考](../cookbook/10-rest-api.md)
- [配置参考](../cookbook/03-configuration.md)
