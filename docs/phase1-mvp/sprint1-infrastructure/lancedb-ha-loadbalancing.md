# LanceDB 主备负载均衡方案

**设计日期**: 2026-01-22
**架构师**: Winston
**目标**: 实现LanceDB服务的高可用和负载均衡

---

## 📋 方案概述

### 业务需求

- **高可用性**: 99.9%+ SLA，故障自动切换
- **负载均衡**: 读写分离，主库写，备库读
- **数据一致性**: 最终一致性，WAL日志同步
- **性能优化**: 搜索延迟 <20ms (P99)

### 技术选型对比

| 方案 | 优势 | 劣势 | 推荐度 |
|------|------|------|--------|
| **方案A: Nginx负载均衡** | 配置简单，快速部署 | 无自动主从切换 | ⭐⭐⭐⭐☆ MVP推荐 |
| **方案B: LanceDB原生复制** | 数据一致性最好 | 配置复杂，需要LanceDB Pro | ⭐⭐⭐☆☆ 生产推荐 |
| **方案C: LanceDB Cloud** | 免运维，全球多区域 | 成本高，依赖云服务 | ⭐⭐⭐⭐⭐ 企业推荐 |

---

## 🏗️ 方案A: Nginx负载均衡（MVP）

### 架构图

```
┌─────────────────────────────────────────────────────┐
│                Docker Network                       │
│  ┌──────────────────────────────────────────────┐  │
│  │           Nginx Load Balancer                │  │
│  │           :8765 (Public)                     │  │
│  │  ┌─────────────────────────────────────┐    │  │
│  │  │  upstream lancedb_backend {         │    │  │
│  │  │    least_conn;                       │    │  │
│  │  │    server primary:8765 weight=3;     │    │  │
│  │  │    server standby:8765 weight=1 backup;│  │
│  │  │  }                                   │    │  │
│  │  └─────────────────────────────────────┘    │  │
│  └────────────────┬───────────────────────────┘  │
│                   │                               │
│      ┌────────────┴────────────┐                 │
│      ▼                         ▼                 │
│ ┌────────────────┐    ┌────────────────┐        │
│ │ LanceDB-1      │    │ LanceDB-2      │        │
│ │ (Primary)      │    │ (Standby)      │        │
│ │ :8765          │    │ :8766 (host)   │        │
│ │                │    │ :8765 (internal)│       │
│ └───────┬────────┘    └────────┬───────┘        │
│         │                     │                 │
│         └──────────┬──────────┘                 │
│                    ▼                            │
│         ┌────────────────────┐                  │
│         │ Shared Storage     │                  │
│         │ /data/lancedb      │                  │
│         │ (Docker Volume)    │                  │
│         └────────────────────┘                  │
└─────────────────────────────────────────────────────┘
```

### 配置文件

#### 1. Nginx配置

**文件**: `nginx/lancedb-lb.conf`

```nginx
user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log warn;
pid /var/run/nginx.pid;

events {
    worker_connections 4096;
    use epoll;
    multi_accept on;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    # 日志格式
    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent" "$http_x_forwarded_for" '
                    'rt=$request_time uct="$upstream_connect_time" '
                    'uht="$upstream_header_time" urt="$upstream_response_time"';

    access_log /var/log/nginx/access.log main;

    # 性能优化
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    types_hash_max_size 2048;
    client_max_body_size 100M;

    # Upstream配置
    upstream lancedb_backend {
        # 负载均衡算法：最少连接
        least_conn;

        # Primary节点（处理读写）
        server lancedb-primary:8765 weight=3 max_fails=2 fail_timeout=30s;

        # Standby节点（backup，主库故障时接管）
        server lancedb-standby:8765 weight=1 max_fails=2 fail_timeout=30s backup;

        # Keepalive连接池
        keepalive 32;
    }

    # 读写分离（可选）
    upstream lancedb_read {
        least_conn;

        # 读操作：主库 + 备库
        server lancedb-primary:8765 weight=2;
        server lancedb-standby:8765 weight=1;

        keepalive 32;
    }

    upstream lancedb_write {
        # 写操作：仅主库
        server lancedb-primary:8765 max_fails=2 fail_timeout=30s;

        keepalive 16;
    }

    # HTTP服务器
    server {
        listen 8765;
        server_name _;

        # 健康检查端点
        location /health {
            proxy_pass http://lancedb_backend/health;
            access_log off;

            # 健康检查配置
            health_check interval=10s fails=3 passes=2 match=status_ok;
        }

        # 读操作端点
        location /api/v1/search {
            proxy_pass http://lancedb_read;

            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header Connection "";

            # 超时配置
            proxy_connect_timeout 5s;
            proxy_send_timeout 30s;
            proxy_read_timeout 30s;

            # HTTP/1.1 with keepalive
            proxy_http_version 1.1;
            proxy_set_header Connection "";
        }

        # 写操作端点
        location /api/v1/upsert {
            proxy_pass http://lancedb_write;

            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header Connection "";

            # 写操作可以容忍更长延迟
            proxy_connect_timeout 5s;
            proxy_send_timeout 60s;
            proxy_read_timeout 60s;

            proxy_http_version 1.1;
            proxy_set_header Connection "";
        }

        # 其他端点（默认走主库）
        location / {
            proxy_pass http://lancedb_backend;

            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header Connection "";

            proxy_connect_timeout 5s;
            proxy_send_timeout 60s;
            proxy_read_timeout 60s;

            proxy_http_version 1.1;
            proxy_set_header Connection "";
        }

        # 监控端点
        location /nginx_status {
            stub_status on;
            access_log off;
            allow 127.0.0.1;
            allow 172.16.0.0/12;  # Docker网络
            deny all;
        }
    }

    # 状态码匹配
    map $status $status_ok {
        "~^[23]" 1;
        default 0;
    }
}
```

#### 2. Docker Compose更新

**文件**: `docker-compose.yml` (追加)

```yaml
  # LanceDB Primary (主库)
  lancedb-primary:
    build:
      context: .
      dockerfile: python/Dockerfile.lancedb
    container_name: dintellihub-lancedb-primary
    environment:
      - LANCEDB_URI=/data/lancedb
      - LANCEDB_ROLE=primary
      - PORT=8765
      - HOST=0.0.0.0
      - PYTHONUNBUFFERED=1
      - LOG_LEVEL=INFO
    volumes:
      - lancedb_data:/data/lancedb
      - ./python/lancedb:/app/lancedb
    ports:
      - "8765:8765"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8765/health"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 30s
    restart: unless-stopped
    networks:
      - dintellihub-net

  # LanceDB Standby (备库)
  lancedb-standby:
    build:
      context: .
      dockerfile: python/Dockerfile.lancedb
    container_name: dintellihub-lancedb-standby
    environment:
      - LANCEDB_URI=/data/lancedb
      - LANCEDB_ROLE=standby
      - PORT=8765
      - HOST=0.0.0.0
      - PYTHONUNBUFFERED=1
      - LOG_LEVEL=INFO
    volumes:
      - lancedb_data:/data/lancedb
      - ./python/lancedb:/app/lancedb
    ports:
      - "8766:8765"  # Host端口映射
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8765/health"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 30s
    restart: unless-stopped
    networks:
      - dintellihub-net

  # Nginx Load Balancer
  lancedb-lb:
    image: nginx:alpine
    container_name: dintellihub-lancedb-lb
    volumes:
      - ./nginx/lancedb-lb.conf:/etc/nginx/nginx.conf:ro
      - nginx_logs:/var/log/nginx
    ports:
      - "8765:8765"  # 对外暴露端口
    depends_on:
      lancedb-primary:
        condition: service_healthy
      lancedb-standby:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "wget", "--quiet", "--tries=1", "--spider", "http://localhost:8765/health"]
      interval: 30s
      timeout: 10s
      retries: 3
    restart: unless-stopped
    networks:
      - dintellihub-net

  # Prometheus (更新)
  prometheus:
    # ... 现有配置 ...
    volumes:
      - prometheus_data:/prometheus
      - ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro  # 添加
      - ./prometheus/rules:/etc/prometheus/rules:ro  # 添加

volumes:
  # ... 现有volumes ...
  lancedb_data:
    driver: local
  nginx_logs:
    driver: local
```

#### 3. Prometheus配置

**文件**: `prometheus/prometheus.yml`

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  # LanceDB Primary
  - job_name: 'lancedb-primary'
    static_configs:
      - targets: ['lancedb-primary:8765']
    metrics_path: '/metrics'

  # LanceDB Standby
  - job_name: 'lancedb-standby'
    static_configs:
      - targets: ['lancedb-standby:8765']
    metrics_path: '/metrics'

  # Nginx LB
  - job_name: 'nginx-lb'
    static_configs:
      - targets: ['lancedb-lb:8765']
    metrics_path: '/nginx_status'

  # 其他服务...
```

**文件**: `prometheus/rules/lancedb-alerts.yml`

```yaml
groups:
  - name: lancedb_alerts
    interval: 30s
    rules:
      # 主库宕机告警
      - alert: LanceDBPrimaryDown
        expr: up{job="lancedb-primary"} == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "LanceDB primary instance is down"
          description: "LanceDB primary has been down for more than 1 minute."

      # 备库宕机告警
      - alert: LanceDBStandbyDown
        expr: up{job="lancedb-standby"} == 0
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "LanceDB standby instance is down"
          description: "LanceDB standby has been down for more than 5 minutes."

      # 搜索延迟过高
      - alert: LanceDBHighLatency
        expr: lancedb_search_latency_p99 > 100
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "LanceDB search latency is high"
          description: "P99 latency is {{ $value }}ms (threshold: 100ms)"

      # 磁盘空间不足
      - alert: LanceDBDiskSpaceLow
        expr: lancedb_disk_free_gb < 10
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "LanceDB disk space is low"
          description: "Only {{ $value }}GB free (threshold: 10GB)"
```

### LanceDB服务更新

#### 1. 增强的健康检查

**文件**: `python/lancedb/main.py` (更新)

```python
@app.get("/health", response_model=HealthResponse)
async def health_check():
    """增强的健康检查"""
    try:
        # 基础检查
        tables = db.table_names()

        # 磁盘空间检查
        import shutil
        disk = shutil.disk_usage(settings.LANCEDB_URI)

        # 内存检查
        import psutil
        memory = psutil.virtual_memory()

        # 角色信息
        role = getattr(settings, 'LANCEDB_ROLE', 'unknown')

        return HealthResponse(
            status="ok",
            service="lancedb",
            version=settings.APP_VERSION,
            metadata={
                "role": role,
                "tables": len(tables),
                "disk_free_gb": round(disk.free / (1024**3), 2),
                "disk_used_gb": round(disk.used / (1024**3), 2),
                "memory_percent": memory.percent,
                "connections": getattr(db, "_connection_count", 0)
            }
        )
    except Exception as e:
        logger.error("Health check failed", error=str(e))
        raise HTTPException(status_code=503, detail="Service unhealthy")

# 新增：Prometheus metrics端点
from prometheus_client import Counter, Histogram, Gauge, generate_latest

# Metrics
search_requests = Counter('lancedb_search_requests_total', 'Total search requests')
search_latency = Histogram('lancedb_search_latency_seconds', 'Search latency')
upsert_requests = Counter('lancedb_upsert_requests_total', 'Total upsert requests')
disk_free_gb = Gauge('lancedb_disk_free_gb', 'Free disk space in GB')

@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint"""
    # 更新disk gauge
    disk = shutil.disk_usage(settings.LANCEDB_URI)
    disk_free_gb.set(disk.free / (1024**3))

    return generate_latest()
```

#### 2. 主库优先处理

**文件**: `python/lancedb/main.py` (添加)

```python
# 在semantic_search中添加角色检查
@app.post("/api/v1/search")
async def semantic_search(request: SearchRequest):
    role = getattr(settings, 'LANCEDB_ROLE', 'unknown')

    # 备库在索引未完全同步时的降级处理
    if role == "standby":
        logger.info("Search on standby", collection=request.collection)
        # 备库可以处理读请求，但需要检查是否需要同步

    # ... 原有搜索逻辑 ...

    # 记录metrics
    search_requests.inc()
    with search_latency.time():
        # 搜索操作
        pass
```

### 启动和测试

#### 1. 启动服务

```bash
# 启动所有服务
docker compose up -d lancedb-primary lancedb-standby lancedb-lb

# 检查状态
docker compose ps

# 查看日志
docker compose logs -f lancedb-lb
```

#### 2. 测试负载均衡

```bash
# 健康检查
curl http://localhost:8765/health

# 测试搜索（会分发到primary和standby）
for i in {1..10}; do
  curl -X POST http://localhost:8765/api/v1/search \
    -H "Content-Type: application/json" \
    -d '{
      "collection": "test",
      "vector": [0.1, 0.2, 0.3],
      "limit": 5
    }'
done

# 查看Nginx日志
docker compose logs lancedb-lb | grep "lancedb"
```

#### 3. 测试故障切换

```bash
# 停止主库
docker compose stop lancedb-primary

# 测试请求（应该自动切换到standby）
curl http://localhost:8765/health
curl -X POST http://localhost:8765/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{"collection": "test", "vector": [0.1, 0.2, 0.3], "limit": 5}'

# 恢复主库
docker compose start lancedb-primary
```

---

## 🏗️ 方案B: LanceDB原生复制（生产）

### 架构

```
┌──────────────────────────────────────────────────┐
│              LanceDB Primary                     │
│  ┌────────────────────────────────────────┐    │
│  │  Write-Ahead Log (WAL)                │    │
│  │  /data/wal/wal.log                    │    │
│  └──────────────┬─────────────────────────┘    │
│                 │                               │
│                 │ Replication Stream           │
│                 ▼                               │
│  ┌────────────────────────────────────────┐    │
│  │        LanceDB Replica                 │    │
│  │  sync_mode: async                      │    │
│  │  apply_lag: <5s                        │    │
│  └────────────────────────────────────────┘    │
└──────────────────────────────────────────────────┘
```

### 配置

**Primary配置**:
```python
import lancedb

# 创建主库
db = lancedb.connect("/data/lancedb", mode="overwrite")

# 启用WAL
db.enable_wal(
    wal_path="/data/wal",
    wal_rotation_size_gb=1,
    wal_retention_hours=24
)

# 创建表
table = db.create_table("vectors", data=data)

# 允许复制
table.enable_replication()
```

**Replica配置**:
```python
# 创建备库
db_replica = lancedb.connect("/data/lancedb_replica", mode="attach")

# 配置复制
db_replica.replicate_from(
    primary_uri="/data/lancedb",
    wal_path="/data/wal",
    sync_mode="async",  # async | sync
    apply_lag_seconds=5
)
```

---

## 🏗️ 方案C: LanceDB Cloud（企业）

### 迁移步骤

```python
# 1. 导出本地数据
import lancedb

db_local = lancedb.connect("/data/lancedb")

# 2. 连接LanceDB Cloud
db_cloud = lancedb.connect(
    "db://api_key@cloud.lancedb.com/dintellihub"
)

# 3. 迁移数据
for table_name in db_local.table_names:
    table = db_local.open_table(table_name)
    data = table.to_arrow()

    db_cloud.create_table(table_name, data=data)

# 4. 切换应用连接
# 更新 .env
# LANCEDB_URI=db://api_key@cloud.lancedb.com/dintellihub
```

---

## 📊 性能对比

| 方案 | 可用性 | 性能 | 复杂度 | 成本 | 推荐阶段 |
|------|--------|------|--------|------|----------|
| **方案A: Nginx LB** | 99.5% | 高 | 低 | 低 | MVP |
| **方案B: 原生复制** | 99.9% | 高 | 中 | 中 | 生产 |
| **方案C: LanceDB Cloud** | 99.99% | 最高 | 低 | 高 | 企业 |

---

## ✅ 实施建议

### MVP阶段（当前）
- ✅ 使用**方案A: Nginx负载均衡**
- ⏰ 实施时间: 4-6小时
- 🎯 目标: 快速实现高可用

### 生产阶段（3个月后）
- ✅ 升级到**方案B: LanceDB原生复制**
- ⏰ 实施时间: 2-3天
- 🎯 目标: 数据一致性保证

### 企业阶段（未来）
- ✅ 迁移到**方案C: LanceDB Cloud**
- ⏰ 实施时间: 1周
- 🎯 目标: 免运维，全球部署

---

**设计完成时间**: 2026-01-22
**下一步**: 实施方案A的Nginx配置
