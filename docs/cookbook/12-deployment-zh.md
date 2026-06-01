# 部署与运维

> 从 Docker Compose 全栈部署到 Prometheus 监控、备份恢复、安全加固和性能调优的完整运维指南。

***

## 1. Docker Compose 全栈部署

Arrow Lake 通过 profile 机制按需启动服务：

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
```

| 参数              | 类型     | 说明                                      |
| --------------- | ------ | --------------------------------------- |
| `event_type`    | `str`  | 事件类型 (如 `data_ingest`, `backup_create`) |
| `dataset_name`  | `str`  | 关联数据集                                   |
| `actor`         | `str`  | 操作者 (默认 `"system"`)                     |
| `lance_version` | `int`  | Lance 版本号                               |
| `payload`       | `dict` | 附加事件数据                                  |

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
stats = lake._get_storage().compact("articles")
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
