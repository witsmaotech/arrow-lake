# 部署与运维

> 从 Docker Compose 全栈部署到 Prometheus 监控、备份恢复、安全加固和性能调优的完整运维指南。

***

## 1. Docker Compose 全栈部署

### 1.1 推荐生产栈：prod_minimal.yml（v1.10.0）

生产环境**主路径**为 `deploy/docker-compose.prod_minimal.yml`（16 个服务块，含 api / system-db / minio / redis / hg-server / hg-hubble / gravitino / lance-rest / nginx / jaeger 等，init 任务与 jaeger 按 profile 门控）。它取代了旧的 `docker-compose.yml --profile core`，是 v1.10.0 唯一受运维验证的部署栈。

```bash
# 启动整个生产栈（读 deploy/.env）
docker compose --project-directory deploy -p arrow-lake \
  -f deploy/docker-compose.prod_minimal.yml up -d

# 只 build api 镜像（避开 BuildKit 共享 tag quirk；镜像 tag 为 arrow-lake:1.11.4）
docker compose --project-directory deploy -p arrow-lake \
  -f deploy/docker-compose.prod_minimal.yml build api

# 直接用 docker compose 启动单个服务
docker compose --project-directory deploy -p arrow-lake \
  -f deploy/docker-compose.prod_minimal.yml up -d api
```

宿主侧端口（仅绑 `127.0.0.1`，远程访问需经反向代理）：

| 服务          | 宿主端口 → 容器         | 凭据 / 说明                          |
| ------------- | ---------------------- | ------------------------------------ |
| api           | `127.0.0.1:8000` → 8000 | API key 见 `deploy/.env`             |
| minio         | `127.0.0.1:9000` → 9000 | `minioadmin` / `minioadmin`（默认）  |
| hg-server     | `127.0.0.1:8089` → 8080 | auth 必开：`admin` / `pa`            |
| redis         | `127.0.0.1:6380` → 6379 | 密码 `:?` 强制必填                   |
| gravitino     | `127.0.0.1:8090` → 8090 | —                                    |
| system-db     | （内部）→ 8080          | libSQL sqld，v1.9.0 控制面           |

> **注意**：改 `arrow_lake/` Python 源码需 rebuild api 镜像；改 `deploy/scripts/*.sh` 等 bind-mount 文件 restart 即生效。秒级热重载方案见 §17.4 dev override。

### 1.2 旧版 profile 栈（参考）

`deploy/docker-compose.yml` 仍提供基于 profile 的按需启动（Ray/Jupyter/GPU 计算等场景）：

```bash
# 最小部署: API + MinIO
docker compose -f deploy/docker-compose.yml --profile core up -d

# 开发环境: API + MinIO + Ray + Jupyter
docker compose -f deploy/docker-compose.yml --profile dev up -d

# 查看日志
docker compose -f deploy/docker-compose.yml logs -f api

# 停止
docker compose -f deploy/docker-compose.yml --profile dev down
```

| Profile      | 包含服务                                 | 用途       |
| ------------ | ------------------------------------ | -------- |
| `core`       | api, minio, minio-init               | 生产最小部署   |
| `dev`        | core + ray-head, ray-worker, jupyter | 本地开发     |
| `compute`    | ray-head, ray-worker                 | 分布式计算    |
| `gpu`        | GPU overlay 的 ray-head, ray-worker   | GPU 加速推理 |
| `monitoring` | prometheus, grafana, jaeger          | 可观测性     |
| `ocr`        | turbo-ocr                            | OCR 服务   |

核心环境变量 (通过 `.env` 传入):

```bash
MINIO_ROOT_USER=admin
MINIO_ROOT_PASSWORD=changeme
ARROW_LAKE__STORAGE__BACKEND=minio
ARROW_LAKE__STORAGE__S3_ENDPOINT=http://minio:9000
ARROW_LAKE__API__ENABLED=true
ARROW_LAKE__API__PORT=8000
ARROW_LAKE__OBSERVABILITY__METRICS_ENABLED=true
```

***

## 2. OCR 服务

TurboOCR 通过独立 overlay 部署，需要 GPU (CUDA) 支持：

```bash
docker compose -f deploy/docker-compose.yml \
              -f deploy/docker-compose.ocr.yml \
              --profile ocr up -d

# 验证健康
curl -sf http://localhost:8002/health
```

关键配置 (`deploy/docker-compose.ocr.yml`):

```yaml
turbo-ocr:
  image: deepdoectection/turbo-ocr:latest
  environment:
    - OCR_MAX_PAGES=100
    - OCR_MAX_FILE_SIZE_MB=50
    - WORKERS=2
```

安全设计：TurboOCR 使用独立的 `ocr-internal` 桥接网络，生产环境建议去掉 `ports` 映射，仅允许内部访问。

***

## 3. Prometheus 监控

```bash
docker compose -f deploy/docker-compose.yml \
              -f deploy/docker-compose.monitoring.yml \
              --profile monitoring up -d
```

Prometheus 配置 (`deploy/monitoring/prometheus/prometheus.yml`) 抓取三个目标：
`arrow-lake-ray-head:8000/metrics`、`:8265/metrics` (Ray)、`arrow-lake-minio:9000/minio/v2/metrics/cluster`。

### 核心指标 (arrow\_lake\_ 前缀)

**DuckDB 连接池：**

| 指标                                      | 类型      | 说明         |
| --------------------------------------- | ------- | ---------- |
| `duckdb_pool_active_sessions`           | Gauge   | 当前活跃连接数    |
| `duckdb_pool_queued_requests`           | Gauge   | 等待连接的请求数   |
| `duckdb_pool_total_queries`             | Counter | 总查询执行数     |
| `duckdb_pool_total_errors`              | Counter | 查询错误总数     |
| `duckdb_pool_total_timeouts`            | Counter | 获取连接超时数    |
| `duckdb_pool_slow_queries`              | Counter | 超过阈值的慢查询数  |
| `duckdb_pool_evicted_connections_total` | Counter | 空闲/僵尸连接回收数 |

**HTTP 请求：**

| 指标                              | 类型        | 标签                         | 说明      |
| ------------------------------- | --------- | -------------------------- | ------- |
| `http_request_duration_seconds` | Histogram | method, path, status\_code | 请求延迟    |
| `auth_requests_total`           | Counter   | auth\_method, status       | 认证统计    |
| `rate_limit_rejected_total`     | Counter   | endpoint, path             | 速率限制拒绝数 |

**数据摄取：**

| 指标                                 | 类型      | 标签                  | 说明      |
| ---------------------------------- | ------- | ------------------- | ------- |
| `ingestion_rows_total`             | Counter | source              | 摄取总行数   |
| `ingestion_bytes_total`            | Counter | source              | 摄取总字节数  |
| `ingestion_errors_total`           | Counter | source, error\_type | 摄取错误数   |
| `processing_quality_rejects_total` | Counter | filter\_name        | 质量过滤拒绝数 |

Grafana: `http://localhost:3000` (admin / admin)
Jaeger: `http://localhost:16686` (OTLP 端点 `:4317`)

***

## 4. 备份与恢复

```python
from arrow_lake.ops.backup import BackupManager
from arrow_lake.config import StorageConfig

config = StorageConfig(
    backend="minio",
    s3_endpoint="http://localhost:9000",
    s3_bucket="arrow-lake",
)

mgr = BackupManager(
    storage_config=config,
    lance_base_uri="./data",
    backup_bucket="arrow-lake-backups",
)

# 创建备份
info = mgr.create_backup(
    dataset_names=["articles", "photos"],
    blob_prefixes=["thumbnails/"],
)
print(f"备份 ID: {info.backup_id}, 状态：{info.status}")

# 列出所有备份
for b in mgr.list_backups():
    print(f"{b.backup_id} | {b.created_at} | {b.status}")

# SHA-256 完整性校验
ok = mgr.verify_backup(info.backup_id)

# 恢复备份
mgr.restore_backup(info.backup_id, dataset_names=["articles"], overwrite=True)

# 删除旧备份
mgr.delete_backup("20260101T000000zabc12345")
```

REST API 方式：

```bash
# 创建
curl -X POST http://localhost:8000/api/v1/backup/create \
  -H "Content-Type: application/json" -H "X-API-Key: your-key" \
  -d '{"dataset_names": ["articles"]}'

# 恢复
curl -X POST http://localhost:8000/api/v1/backup/restore \
  -H "Content-Type: application/json" -H "X-API-Key: your-key" \
  -d '{"backup_id": "20260101T000000zabc12345", "overwrite": true}'
```

***

## 5. 安全加固要点

| 安全项                 | 配置方式                           | 默认值         | 生产建议               |
| ------------------- | ------------------------------ | ----------- | ------------------ |
| **API Key 认证**      | `ARROW_LAKE__API__API_KEY`     | 空 (禁用)      | 必须设置强随机字符串         |
| **JWT 认证**          | `auth.auth_mode`               | `api_key`   | 切换为 `jwt` 或 `both` |
| **JWT 密钥**          | `auth.jwt_secret_key`          | 空           | 32+ 字符随机密钥         |
| **CORS 限制**         | `api.cors_origins`             | `[]` (全部允许) | 明确指定允许域名           |
| **安全头**             | `api.security_headers_enabled` | `true`      | 保持启用               |
| **CSP**             | `api.content_security_policy`  | 空           | 设置合适的 CSP          |
| **X-Frame-Options** | `api.frame_options`            | `DENY`      | 保持 `DENY`          |
| **请求体大小**           | `api.max_request_size_bytes`   | `100MB`     | 按需收紧 (如 10MB)      |
| **请求超时**            | `api.request_timeout_seconds`  | `300s`      | 按需缩短               |
| **速率限制**            | `rate_limit.enabled`           | `true`      | 保持启用               |
| **HMAC 审计**         | `audit.hmac_secret_key`        | 空           | 生产必须设置             |
| **Masking HMAC (v1.9.6)** | `ARROW_LAKE__MASKING__HMAC_KEY`（环境变量） | 空 | 缺失则**启动阻断**；降级需 `ALLOW_MISSING_KEY=1`（见 §17.2） |
| **API Key 轮换**      | `api.api_key_rotation_days`    | `90`        | 30-90 天轮换          |
| **SSRF 防护**         | 内置 URL 校验                      | 启用          | 拒绝内网地址访问           |
| **输入验证**            | `schema_validation`            | `lenient`   | 可设为 `strict`       |

生产配置示例：

```yaml
api:
  api_key: "${ARROW_LAKE_API_KEY}"
  cors_origins: ["https://app.example.com"]
  max_request_size_bytes: 10485760    # 10 MB
  security_headers_enabled: true
  frame_options: "DENY"

auth:
  auth_mode: jwt
  jwt_secret_key: "${JWT_SECRET_KEY}"
  jwt_access_token_minutes: 30
  jwt_refresh_token_days: 7

rate_limit:
  enabled: true
  default_requests_per_minute: 120

audit:
  enabled: true
  hmac_secret_key: "${AUDIT_HMAC_KEY}"
```

> **v1.9.0 RBAC 依赖外部 system_db**：prod_minimal 栈的 RBAC / 身份 / personal_token / 任务历史等控制面状态现由独立的 `system-db`（libSQL sqld）承载，`SYSTEM_DB_ENABLED=true` 时生效。store 不可达时按 **fail-closed**（返 401）处理，不会放行请求（除非显式 opt-in `SYSTEM_DB_SERVE_STALE_ON_ERROR=true`）。详见 §17.1。

***

## 6. 日志与审计

```python
from arrow_lake import Lake

lake = Lake.from_yaml("configs/prod.yaml")

# 记录审计事件
audit_id = lake.audit_record(
    event_type="data_ingest",
    dataset_name="articles",
    actor="pipeline-user",
    lance_version=42,
    metaflow_run_id="mf-20260424-001",
    metaflow_tags={"env": "prod", "team": "data"},
    payload={"rows": 1000, "source": "s3://raw/"},
)

# HMAC 完整性校验
lake.audit_verify(audit_id)   # True / False

# 查询审计日志
entries = lake.audit_query(
    dataset_name="articles",
    start="2026-04-01T00:00:00Z",
    end="2026-04-30T23:59:59Z",
    event_type="data_ingest",
)

# 导出数据集审计记录
export = lake.audit_export("articles")

# 运行异常检测
anomalies = lake.audit_analyze()
for a in anomalies:
    print(f"{a['severity']}: {a['description']}")
```

| 参数               | 类型     | 说明                                      |
| ---------------- | ------ | --------------------------------------- |
| `event_type`     | `str`  | 事件类型 (如 `data_ingest`, `backup_create`) |
| `dataset_name`   | `str`  | 关联数据集                                   |
| `actor`          | `str`  | 操作者 (默认 `"system"`)                     |
| `lance_version`  | `int`  | Lance 版本号                               |
| `metaflow_run_id`| `str`  | 关联的 Metaflow run ID                    |
| `metaflow_tags`  | `dict` | 关联的 Metaflow 标签                        |
| `payload`        | `dict` | 附加事件数据                                  |

### 审计 REST API

```bash
# 记录审计事件
curl -X POST http://localhost:8000/api/v1/datasets/articles/audit \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"action": "data_ingest", "actor": "pipeline-user"}'

# 查询审计条目
curl "http://localhost:8000/api/v1/datasets/articles/audit?start=2026-04-01T00:00:00Z" \
  -H "X-API-Key: your-key"

# 验证审计条目完整性
curl http://localhost:8000/api/v1/audit/{audit_id}/verify -H "X-API-Key: your-key"

# 运行异常检测
curl -X POST http://localhost:8000/api/v1/audit/analyze -H "X-API-Key: your-key"
```

### 审计 CLI

```bash
# 记录审计事件
arrow-lake audit record --dataset articles --action data_ingest --actor pipeline-user

# 查询审计日志
arrow-lake audit query --dataset articles --start 2026-04-01 --end 2026-04-30

# 运行异常分析
arrow-lake audit analyze
```

***

## 7. 性能调优

### SessionManager 连接池

```python
from arrow_lake import Lake
lake = Lake.from_yaml("configs/dev.yaml")
sm = lake.get_session_manager()

stats = sm.get_stats()
print(f"活跃：{stats.active_sessions}, 空闲：{stats.idle_connections}")
print(f"等待：{stats.queued_requests}, 查询：{stats.total_queries}")
```

OLAP 配置 (`OlapConfig`):

```yaml
olap:
  max_result_rows: 100000
  enable_predicate_pushdown: true
  enable_join: true
```

### 向量搜索 nprobes 调优

`nprobes` 控制 IVF 索引探测分区数，越大越精确但越慢：

| nprobes | 精度 | 速度 | 场景        |
| ------- | -- | -- | --------- |
| 10      | 低  | 最快 | 粗筛 / 候选召回 |
| 20 (默认) | 中  | 快  | 一般搜索      |
| 64-128  | 高  | 慢  | 精排 / 精度敏感 |

```yaml
vector:
  nprobes: 20
  num_partitions: 256
  num_sub_vectors: 24
```

### 数据集压缩与 Helm 部署

```python
# 合并碎片文件
stats = lake.compact_dataset("articles")
print(f"文件：{stats.fragments_before} -> {stats.fragments_after}")
```

Kubernetes 部署：

```bash
helm install arrow-lake deploy/helm/arrow-lake/ \
  --namespace arrow-lake --create-namespace \
  -f deploy/helm/arrow-lake/values.yaml
```

```python
# 优雅关闭
lake.shutdown()
```

***

## 8. Redis 部署

当运行多个 API 副本 (HPA、Kubernetes 或多实例 Docker Compose) 时，Redis 用于分布式会话协调。它提供：

- **分布式信号量**，用于跨 Pod 的 DuckDB 连接池协调
- **JWT token 黑名单**，在 API 实例间共享
- Redis 不可用时自动回退到进程内 `threading.Semaphore`

### Docker Compose

Redis 包含在 `core`、`dev`、`monitoring` 和 `gpu` profile 中：

```yaml
# deploy/docker-compose.yml (节选)
redis:
  image: redis:7.4
  container_name: arrow-lake-redis
  profiles: ["core", "dev", "monitoring", "gpu"]
  command: >
    redis-server
    --maxmemory ${REDIS_MAXMEMORY:-256mb}
    --maxmemory-policy allkeys-lru
    --appendonly yes
  volumes:
    - redis-data:/data
  healthcheck:
    test: ["CMD", "redis-cli", "ping"]
```

在环境中设置 `redis.enabled` 后，API 服务器会自动连接 Redis：

```bash
ARROW_LAKE__REDIS__ENABLED=true
ARROW_LAKE__REDIS__URL=redis://redis:6379/0
ARROW_LAKE__REDIS__PASSWORD=${REDIS_PASSWORD:-}
```

> **v1.9.x prod_minimal 强制 Redis 密码**：prod_minimal 栈用 `${REDIS_PASSWORD:?REDIS_PASSWORD must be set}` 语法，**缺失则整个栈无法启动**（fail-fast，非旧版的默认空密码）。必须在 `deploy/.env` 显式设置 `REDIS_PASSWORD`。

### Helm (Kubernetes)

通过 `values.yaml` 配置 Redis：

```yaml
# deploy/helm/arrow-lake/values.yaml
redis:
  enabled: true
  url: "redis://redis:6379/0"
  password: ""  # 通过 ARROW_LAKE__REDIS__PASSWORD 或 Kubernetes secret 设置
  ssl: false
```

对于托管 Redis 服务 (AWS ElastiCache、Azure Cache for Redis、GCP Memorystore)，启用 TLS 并设置端点：

```yaml
redis:
  enabled: true
  url: "rediss://primary.xxxxxx.use1.cache.amazonaws.com:6379/0"
  password: "${REDIS_PASSWORD}"
  ssl: true
```

***

## 9. 水平 Pod 自动扩缩容 (HPA)

Arrow Lake 内置 Kubernetes HPA 模板，根据 CPU 和内存利用率自动扩缩 API Pod。**HPA 需要 Redis** 来协调跨副本的分布式信号量。

### 启用 HPA

```yaml
# values.yaml — 启用 HPA
apiServer:
  replicaCount: 2
  autoscaling:
    enabled: true
    minReplicas: 2
    maxReplicas: 8
    targetCPUUtilizationPercentage: 70
    targetMemoryUtilizationPercentage: 80

redis:
  enabled: true  # HPA 必需
```

```bash
helm install arrow-lake deploy/helm/arrow-lake/ \
  --namespace arrow-lake --create-namespace \
  -f values.yaml
```

### 扩缩容行为

HPA 模板 (`deploy/helm/arrow-lake/templates/hpa.yaml`) 配置：

| 参数                                | 默认值 | 说明                          |
| --------------------------------- | --- | --------------------------- |
| `minReplicas`                     | `2` | API Pod 最小数量               |
| `maxReplicas`                     | `8` | API Pod 最大数量               |
| `targetCPUUtilizationPercentage`  | `70` | 平均 CPU 超过此阈值时扩容           |
| `targetMemoryUtilizationPercentage` | `80` | 平均内存超过此阈值时扩容           |

**缩容稳定窗口**：300 秒，防止频繁抖动。
**扩容策略**：每 60 秒最多增加 100% 或 2 个 Pod，取较大值。

> **重要**：如果不使用 Redis，所有 API 副本将独立使用各自的进程内信号量，可能导致负载下 DuckDB 连接池耗尽。使用 HPA 时务必启用 Redis。

***

## 10. CronJob 备份自动化

Helm chart 包含一个 CronJob 模板，通过备份 REST API 实现每日自动备份。

### 启用定时备份

```yaml
# values.yaml — 启用自动备份
backup:
  enabled: true
  schedule: "0 2 * * *"  # 每天 02:00 UTC
  image:
    repository: curlimages/curl
    tag: "8.12.1"
  apiKeySecret:
    name: arrow-lake-api-key
    key: api-key
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 1
```

### 创建 API Key Secret

```bash
kubectl create secret generic arrow-lake-api-key \
  --namespace arrow-lake \
  --from-literal=api-key='your-secret-api-key-here'
```

### 工作原理

CronJob 运行一个轻量级 `curl` 容器，调用 `POST /api/v1/backup/create`，参数 `dataset_names: null`（备份所有数据集）。关键特性：

- **并发策略**：`Forbid` — 防止备份任务重叠
- **重试**：`backoffLimit: 2`，`restartPolicy: OnFailure`
- **历史保留**：保留 3 个成功和 1 个失败的任务供排查
- **幂等性**：每次运行创建一个带时间戳的新备份；旧备份需单独清理

### 验证备份任务

```bash
# 查看 CronJob 状态
kubectl get cronjob -n arrow-lake

# 查看最近的备份任务日志
kubectl logs -n arrow-lake job/arrow-lake-backup-$(date +%Y%m%d) -c backup

# 通过 API 列出备份
curl -s http://localhost:8000/api/v1/backup/list -H "X-API-Key: your-key" | jq
```

***

## 11. 生产安全检查清单

除了第 5 节的通用安全加固，多副本和 Kubernetes 部署还需要额外的安全控制。

### 传输安全 (TLS)

```yaml
# values.yaml — TLS 终止
apiServer:
  env:
    ARROW_LAKE__API__TLS_ENABLED: "true"
    ARROW_LAKE__API__TLS_CERT_PATH: "/certs/tls.crt"
    ARROW_LAKE__API__TLS_KEY_PATH: "/certs/tls.key"
```

在 Kubernetes 中使用 cert-manager 和 Ingress 资源：

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
spec:
  tls:
    - hosts: [arrow-lake.example.com]
      secretName: arrow-lake-tls
```

### 内容安全策略 (CSP)

```yaml
# config.yaml
api:
  security_headers_enabled: true
  content_security_policy: >
    default-src 'self';
    script-src 'self' 'nonce-{REQUEST_ID}';
    style-src 'self' 'unsafe-inline';
    img-src 'self' data: https:;
    frame-ancestors 'none';
    object-src 'none'
  frame_options: "DENY"
```

### 速率限制

```yaml
# config.yaml — 生产环境速率限制
rate_limit:
  enabled: true
  default_requests_per_minute: 120
  default_burst: 20
```

### 网络策略

Helm chart 附带默认的 `NetworkPolicy` 限制网络流量：

```yaml
# values.yaml
networkPolicy:
  enabled: true
  allowExternal: false  # 仅允许同命名空间内流量
```

| 方向   | 允许的流量                              | 端口   |
| ---- | --------------------------------- | ---- |
| 入站   | Ray Pod、Prometheus                | 8000 |
| 入站   | 外部 (如 `allowExternal: true`)      | 8000 |
| 出站   | MinIO / S3 存储端点                   | 9000 |
| 出站   | DNS 解析                            | 53   |

生产环境建议设置 `allowExternal: false`，通过 Ingress 控制器或服务网格路由外部流量。

### 安全措施总结

| 控制项       | Docker Compose                      | Kubernetes (Helm)                      |
| --------- | ----------------------------------- | -------------------------------------- |
| **TLS**   | 反向代理 (nginx/traefik)              | cert-manager + Ingress                 |
| **CSP**   | `api.content_security_policy`       | `api.content_security_policy`          |
| **速率限制**  | `rate_limit.enabled: true`          | `rate_limit.enabled: true`             |
| **网络隔离**  | Docker bridge 网络                   | `networkPolicy.enabled: true`          |
| **Redis 认证** | `REDIS_PASSWORD` 环境变量              | Kubernetes secret + `redis.password`   |
| **API Key** | `ARROW_LAKE__API__API_KEY`          | Kubernetes secret                      |
| **JWT**   | `auth.auth_mode: jwt`               | `auth.auth_mode: jwt`                  |
| **RBAC**  | 30+ 端点带角色检查                        | 30+ 端点带角色检查                            |
| **审计 HMAC** | `audit.hmac_secret_key`             | 通过 secret 设置 `audit.hmac_secret_key` |

***

## 12. v1.5.2 安全加固

v1.5.2 引入了多项关键安全修复，涵盖认证、注入防护和网络绑定。所有部署应至少升级到此版本。

### 安全修复

| 修复项 | 描述 | 影响 |
| --- | --- | --- |
| JWT 空密钥阻止 | 服务器在 `jwt_secret_key` 为空或使用默认值时拒绝启动 | 防止未认证 JWT 令牌签发 |
| Kerberos 命令注入消除 | Kerberos 主体名称中的 shell 元字符被过滤 | 消除通过构造主体名的远程代码执行风险 |
| SQL 注入参数化 | 所有用户提供的 SQL 参数使用参数化查询 | 防止 OLAP 和 lineage 查询端点的 SQL 注入 |
| Redis 默认密码移除 | Docker Compose 和 Helm values 中不再设置默认密码 | 强制生产环境显式配置密码；prod_minimal 用 `:?` 语法缺失即启动失败 |
| 127.0.0.1 绑定 | 默认 API 绑定地址改为仅 localhost | 减小攻击面；远程访问需设置 `api.host: 0.0.0.0` |
| SSRF 防护 | URL 校验阻止私有/内网地址 | 防止通过摄取 URL 实现服务端请求伪造 |
| Admin bypass 改用 Role enum | 硬编码 admin 字符串检查替换为 `Role` 枚举 | 类型安全的角色检查防止字符串比较绕过 |
| Refresh token 旋转撤销 | Refresh token 单次使用，每次使用后轮换 | 窃取的 refresh token 无法重复使用 |

### 健康检查端点

```bash
# 存活检查 (不检查依赖)
curl http://localhost:8000/health/live

# 就绪检查 (验证存储、Redis 等)
curl http://localhost:8000/health/ready

# 完整健康报告
curl http://localhost:8000/health -H "X-API-Key: your-key"

# Prometheus 指标
curl http://localhost:8000/metrics
```

***

## 13. 数据血缘

Arrow Lake 提供内置的数据血缘追踪，用于跟踪数据集依赖关系和下游影响分析。

### Python API

```python
from arrow_lake import Lake

lake = Lake.from_yaml("configs/prod.yaml")

# 记录血缘事件
lake.lineage_record_event(
    "articles_clean",
    "transform",
    source_datasets=["articles_raw"],
    transform_type="quality_filter",
    metadata={"rows_removed": 580},
)

# 查看数据集的血缘历史
history = lake.lineage_history("articles_clean")
for event in history:
    print(f"{event['operation']} at {event['timestamp']}")

# 用 SQL 查询血缘事件
import pyarrow as pa
result = lake.lineage_query(
    "SELECT * FROM lineage WHERE operation = 'transform'"
)

# 获取完整血缘图 (上游 + 下游)
graph = lake.lineage_graph("articles_clean", max_depth=10)
print(f"节点: {len(graph['nodes'])}, 边: {len(graph['edges'])}")

# 分析变更数据集的下游影响
impact = lake.lineage_impact("articles_raw")
for item in impact:
    print(f"受影响: {item['dataset']}, 深度: {item['depth']}")
```

### 血缘 REST API

```bash
# 记录血缘事件
curl -X POST http://localhost:8000/api/v1/lineage/record \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"dataset_name": "articles_clean", "event_type": "transform", "source_datasets": ["articles_raw"]}'

# 获取血缘历史
curl http://localhost:8000/api/v1/lineage/history/articles_clean \
  -H "X-API-Key: your-key"

# 用 SQL 查询血缘
curl -X POST http://localhost:8000/api/v1/lineage/query \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT * FROM lineage WHERE operation = '\''transform'\''"}'

# 获取血缘图
curl http://localhost:8000/api/v1/lineage/graph/articles_clean?max_depth=10 \
  -H "X-API-Key: your-key"

# 分析下游影响
curl -X POST http://localhost:8000/api/v1/lineage/impact \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"dataset_name": "articles_raw"}'
```

### 血缘 CLI

```bash
# 记录血缘事件
arrow-lake lineage record --dataset articles_clean --operation transform --sources articles_raw

# 查看血缘历史
arrow-lake lineage history --dataset articles_clean

# 显示血缘图
arrow-lake lineage graph --dataset articles_clean --max-depth 10

# 分析下游影响
arrow-lake lineage impact --dataset articles_raw
```

***

## 14. 存储生命周期管理

Arrow Lake 支持 S3 存储分层，通过生命周期规则自动将数据转移到低成本存储类 (如 Glacier)，并按需恢复。

### Python API

```python
from arrow_lake import Lake

lake = Lake.from_yaml("configs/prod.yaml")

# 预览生命周期规则 (不实际应用)
rules = lake.lifecycle_rules(prefix="archive/")
print(rules)

# 应用生命周期规则到桶前缀
result = lake.lifecycle_apply(prefix="archive/")
print(f"已应用: {result}")

# 查看对象存储层级
tiers = lake.lifecycle_status(prefix="archive/")
for item in tiers:
    print(f"{item['key']}: {item['storage_class']}")

# 恢复 Glacier 层级的对象 (临时访问)
lake.lifecycle_restore("archive/old_data.parquet", days=7)
```

### 生命周期 CLI

```bash
# 预览生命周期规则
arrow-lake lifecycle rules --prefix archive/

# 应用生命周期规则
arrow-lake lifecycle apply --prefix archive/

# 查看存储层级状态
arrow-lake lifecycle status --prefix archive/

# 恢复 Glacier 对象
arrow-lake lifecycle restore --key archive/old_data.parquet --days 7
```

***

## 15. 通过 Lake API 管理备份

除了第 4 节展示的底层 `BackupManager`，备份也可直接通过 `Lake` 对象管理：

```python
from arrow_lake import Lake

lake = Lake.from_yaml("configs/prod.yaml")

# 创建完整备份 (所有数据集)
info = lake.backup_create()
print(f"备份 ID: {info.backup_id}")

# 创建部分备份
info = lake.backup_create(dataset_names=["articles", "photos"])

# 恢复备份
lake.backup_restore(
    info.backup_id,
    dataset_names=["articles"],
    overwrite=True,
)

# 列出所有备份
for b in lake.backup_list():
    print(f"{b.backup_id} | {b.created_at} | {b.status}")

# 删除备份
lake.backup_delete("20260101T000000zabc12345")
```

### 备份 REST API

```bash
# 创建备份
curl -X POST http://localhost:8000/api/v1/backup/create \
  -H "Content-Type: application/json" -H "X-API-Key: your-key" \
  -d '{"dataset_names": ["articles"]}'

# 列出备份
curl http://localhost:8000/api/v1/backup/list -H "X-API-Key: your-key"

# 恢复备份
curl -X POST http://localhost:8000/api/v1/backup/restore \
  -H "Content-Type: application/json" -H "X-API-Key: your-key" \
  -d '{"backup_id": "20260101T000000zabc12345", "overwrite": true}'

# 删除备份
curl -X DELETE http://localhost:8000/api/v1/backup/20260101T000000zabc12345 \
  -H "X-API-Key: your-key"
```

### 备份 CLI

```bash
# 创建备份
arrow-lake backup create --datasets articles,photos

# 列出备份
arrow-lake backup list

# 恢复备份
arrow-lake backup restore --id 20260101T000000zabc12345 --datasets articles

# 删除备份
arrow-lake backup delete --id 20260101T000000zabc12345
```

***

## 16. 维护命令

```bash
# 运行所有维护任务
arrow-lake maintenance

# 质量去重 (CLI)
arrow-lake quality dedup --dataset articles --strategy exact
arrow-lake quality filter --dataset articles --mode all
```

***

## 17. v1.10.0 生产运维要点（prod_minimal）

> 本节沉淀 v1.9.0–v1.9.6 生产实测的运维要点与高频踩坑，均为 prod_minimal 栈语境。

### 17.1 system_db 控制面（v1.9.0 libSQL）

v1.9.0 起控制面状态（RBAC / 身份 / personal_token / catalog / 任务历史 / RAG 会话 / 血缘索引）迁移至独立的 `system-db` 服务（libSQL / Turso sqld）：

- prod_minimal 提供 `system-db` 服务块，`deploy/.env` 设 `SYSTEM_DB_ENABLED=true`。
- 迁移 V001–V004（RBAC、identity、personal_token、catalog、task_history、RAG sessions、lineage）在容器内**自动执行**。
- **fail-closed**：store 不可达时 RBAC 读返 401，不放行请求（除非 opt-in `SYSTEM_DB_SERVE_STALE_ON_ERROR=true`，可能 honor 宕机期间撤销的权限，谨慎使用）。

### 17.2 Masking HMAC 必配（v1.9.6 安全基线）

`ARROW_LAKE__MASKING__HMAC_KEY` 是**纯环境变量**（非 YAML 配置段），决定 hash masking 是否可用：

- **缺失则启动阻断**（fail-fast）。prod_minimal 已配占位 `${ARROW_LAKE__MASKING__HMAC_KEY:-}`，须在 `deploy/.env` 设强 key（`openssl rand -hex 32`，32+ bytes）。
- opt-in 降级：`ARROW_LAKE__MASKING__ALLOW_MISSING_KEY=1`（仅 dev/测试，hash masking 不可用但服务可起）。
- 部署后**必须配置 `HMAC_KEY`**，否则线上无法启动。

### 17.3 HugeGraph 运维（高频踩坑）

- **per-dataset 动态图**：每个数据集独立图 `kg_{ds}`，rocksdb 后端在容器 `/var/lib/hugegraph/graphs/{name}/`（持久卷）。**auth 必开**（动态建图需要），凭据 `admin` / `pa`（env `HUGEGRAPH_PASSWORD`）。
- **traverser OOM（已修）**：曾因 HugeGraph start 脚本自带 `-Xmx32768m` 与 compose `JAVA_OPTS` 的 `-Xmx2g` 冲突，JVM 对**重复 `-Xmx` 取末值**→ 实际堆仅 2g，稠密图遍历 OOM。修复：`HG_SERVER_MEMORY_LIMIT=12288M` + compose `JAVA_OPTS="-Xms2g -Xmx8g ..."`。验证：
  ```bash
  docker exec arrow-lake-hg-server ps -ef | grep -oE "Xmx[0-9]+[mg]" | tail -1
  # 应输出 Xmx8g（末值生效）
  ```
- **drop/clear 图后必须 restart hg-server**：GraphManager 的内存 schema 缓存不刷新，否则后续 `ensure_schema` 报 500（非良性 400）。一键 SOP：
  ```bash
  make kg-clear-graph DS=<dataset>   # clear：留 shell，持久干净
  make kg-drop-graph  DS=<dataset>   # drop：删注册，内存干净
  ```
  两者均自动 clear/drop + restart hg-server + 等待 healthy。
- **动态图 gremlin 遍历源未全局绑定**：`g.V()` / `{name}.traversal()` 会报 `MissingProperty`。查顶点边用 **REST**（`GET /graphs/{name}/graph/vertices?limit=N --compressed`）或项目端点 `/api/v1/kg/stats`，勿用裸 gremlin。

### 17.4 Gravitino 1.3.0 升级变化

- server `apache/gravitino:${GRAVITINO_VERSION:-1.3.0}`；**proxy 必须中和**：compose `gravitino` 服务显式设 `HTTP_PROXY/HTTPS_PROXY/http_proxy/https_proxy: ""` + `NO_PROXY/no_proxy: "*"`，否则 Docker daemon 注入死代理 → s3a 经代理走 → SigV4 被改 → minio `403 Forbidden`。
- **`GRAVITINO_HOME=/opt/gravitino`**（1.3.0 布局变，数据卷须挂 `/opt/gravitino/data`）；S3 属性用 **`s3.*`**（`s3.endpoint` / `s3.access-key-id` / `s3.secret-access-key`，location `s3://`），旧 `fs.s3a.*` 在 1.3.0 fileset catalog **不生效**。
- 明确：Gravitino 是**可选治理**（RBAC / tag / 血缘 / fileset），**不在数据 / 查询热路径**（dataset CRUD / query / KG / search / RAG 不依赖）。卡死时可临时关：`GRAVITINO_ENABLED=false`。`GravitinoSyncScheduler` 有熔断（连续 5 次失败自停）。

### 17.5 docling GPU 切换

- prod_minimal **默认内联挂 GPU**（`deploy.resources.devices` + `count: ${GPU_COUNT:-1}`），非叠 `gpu.override.yml`；`DOCUMENT_OCR_BACKEND` 默认 `docling`。无 GPU 机器设 `GPU_COUNT=0` 或改回 `kreuzberg`。
- `API_CPU_LIMIT` 默认 `1.0` 是 docling CPU 侧瓶颈（PDF 渲染 / layout / 表格后处理吃 CPU；552 页 / 1 核会超时崩）。宿主多核设 `API_CPU_LIMIT=8.0` 后约 5.4min / 552 页（接近 host）。
- 显式切 GPU（覆盖 inline 配置）：
  ```bash
  docker compose --project-directory deploy -p arrow-lake \
    -f deploy/docker-compose.prod_minimal.yml \
    -f deploy/docker-compose.gpu.override.yml \
    up -d --force-recreate api
  ```

### 17.6 dev override 秒级热重载

改 `arrow_lake/` Python 源码**不必 rebuild 镜像**——叠加 dev override 即可挂源码 + `uvicorn --reload`：

```bash
docker compose --project-directory deploy -p arrow-lake \
  -f deploy/docker-compose.prod_minimal.yml \
  -f deploy/docker-compose.dev.override.yml \
  up -d --force-recreate api
```

> **必须 `--force-recreate`**：否则容器保留 prod command（uvicorn 不带 `--reload`），新代码 / 新端点不生效、404。dev override 同时 bind-mount `console/`，改前端 `*.html|css|js` 刷新浏览器即生效。仅最终固化出镜像时才 rebuild。

### 17.7 RAG hybrid 502 修复

`lance_scan_mode` 默认 `auto` → bridge 走 **DuckDB native lance vector stream + IVF_PQ** → 触发 **Rust panic**（abort，uvicorn worker died，浏览器 "Failed to fetch" / curl 502）。sync vector search 单独正常，仅 DuckDB lance scanner 对 IVF_PQ 的 async vector stream 有 abort bug。修复：prod_minimal api env 已设：

```yaml
ARROW_LAKE__OLAP__LANCE_SCAN_MODE: "pyarrow_fallback"
```

走 pyarrow fallback 的 sub-bridge（绕开 DuckDB path，性能略降但 RAG 通）。排查口诀：RAG 502 + api 日志见 `Failed to create Lance search stream ... Index for column text_embedding` + `terminate called` = 此 bug。

### 17.8 compose env 注入机制与 export base_dir

- **compose env 注入**：api 服务用 compose `environment:` 块 + `${VAR:-default}` 插值。**`deploy/.env` 的裸值不会自动注入容器**——只有被 compose `${VAR}` 引用的变量才生效。改后端配置须改 compose `environment:` 块（或 dev override）。
- **export base_dir**：api 容器 `read_only: true`，`/app/exports` 是**瞬态 tmpfs**（重启丢）。持久导出须设 `ARROW_LAKE__EXPORT__BASE_DIR=/data/lake/exports`（持久可写卷，prod_minimal 已配）。同理 `he_ka_base_dir` 等需写本地路径的配置须指向挂载卷（如 `/data/lake/ka`）。
