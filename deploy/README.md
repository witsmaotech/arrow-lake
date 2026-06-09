# Arrow Lake 部署指南

本目录包含 Arrow Lake 全套 Docker Compose 部署配置，支持从开发到生产的多种环境。

## 目录

- [环境总览](#环境总览)
- [前置条件](#前置条件)
- [快速启动](#快速启动)
- [环境详解](#环境详解)
  - [开发环境 (dev)](#开发环境-dev)
  - [GPU 环境 (gpu)](#gpu-环境-gpu)
  - [生产环境 (production)](#生产环境-production)
  - [监控环境 (monitoring)](#监控环境-monitoring)
  - [知识图谱环境 (kg)](#知识图谱环境-kg)
  - [OCR 环境 (ocr)](#ocr-环境-ocr)
- [配置说明](#配置说明)
- [服务架构](#服务架构)
- [常用运维](#常用运维)
- [故障排查](#故障排查)
- [安全加固清单](#安全加固清单)
- [Kubernetes 部署](#kubernetes-部署)

---

## 环境总览

| 模式 | 命令 | 包含服务 | 用途 |
|------|------|---------|------|
| core | `make up` | API + MinIO | 最小运行单元 |
| dev | `make dev` | core + Ray + Jupyter + 源码挂载 | 日常开发 |
| gpu | `make gpu` | core + Ray (GPU) | 嵌入/训练加速 |
| full | `make full` | dev + Prometheus + Grafana + Jaeger | 全栈开发 + 可观测性 |
| kg | `make kg` | core + HugeGraph | 知识图谱 / GraphRAG |
| ocr | `make ocr` | TurboOCR (内部网络) | PDF/图片 OCR |

### Compose 文件结构

采用 **overlay 分层** 设计，base 定义共享配置，overlay 按需叠加：

```
deploy/
  docker-compose.yml              ← 基础配置 (所有环境共享)
  docker-compose.dev.yml          ← 开发覆盖 (源码挂载、DEBUG 日志)
  docker-compose.gpu.yml          ← GPU 覆盖 (CUDA 镜像、GPU 分配)
  docker-compose.monitoring.yml   ← 监控覆盖 (Prometheus + Grafana + Jaeger)
  docker-compose.hugegraph.yml    ← 知识图谱覆盖 (HugeGraph)
```

---

## 前置条件

### 必需

| 依赖 | 最低版本 | 检查命令 |
|------|---------|---------|
| Docker | 24.0+ | `docker --version` |
| Docker Compose | V2.20+ | `docker compose version` |
| 磁盘空间 | 10 GB+ | `df -h .` |
| 内存 | 8 GB+ | `free -h` |

### GPU 环境 (可选)

| 依赖 | 说明 |
|------|------|
| NVIDIA 驱动 | 525+ |
| NVIDIA Container Toolkit | [安装指南](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) |
| CUDA | 12.4 (Dockerfile.gpu 内置) |

验证 GPU 可用：

```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

---

## 快速启动

### 1. 克隆项目

```bash
git clone https://gitee.com/wits__sunpw/wits-infra-dintellihub.git
cd wits-infra-dintellihub
```

### 2. 初始化环境配置

```bash
cd deploy
make env
```

该命令会：
- 从 `.env.example` 生成 `.env`
- 自动生成 MinIO 随机密码
- 自动生成 Grafana 随机密码

> 也可以手动复制：`cp .env.example .env`，然后编辑 `.env`。

### 3. 启动服务

```bash
# 最简启动 — API + MinIO
make up

# 开发启动 — API + MinIO + Ray + Jupyter
make dev
```

### 4. 验证

```bash
# 健康检查
curl http://localhost:8000/health

# 查看服务状态
make ps

# 查看日志
make logs-api
```

### 5. 访问

| 服务 | 地址 |
|------|------|
| API 文档 (Swagger) | http://localhost:8000/docs |
| API 健康检查 | http://localhost:8000/health |
| API 指标 (Prometheus) | http://localhost:8000/metrics |
| MinIO Console | http://localhost:9001 |
| Jupyter (仅 dev) | http://localhost:8888 |
| Grafana (仅 monitoring) | http://localhost:3000 |
| Jaeger (仅 monitoring) | http://localhost:16686 |

---

## 环境详解

### 开发环境 (dev)

```bash
make dev
```

**启动服务**：API + MinIO + Ray Head + Ray Worker + Jupyter

**特点**：
- 本地源码只读挂载到容器，修改代码后 API 自动热重载 (`--reload`)
- 日志级别 DEBUG
- Jupyter 可直接访问项目代码、测试和配置
- 无 GPU 依赖，纯 CPU 运行

**源码挂载映射**：

| 容器路径 | 宿主机路径 | 权限 |
|---------|-----------|------|
| /app/arrow_lake | ./arrow_lake | 只读 |
| /app/flows | ./flows | 只读 |
| /app/tests | ./tests | 只读 (Jupyter 可写) |
| /app/configs | ./configs | 只读 |
| /app/examples | ./examples | 读写 (Jupyter) |

**典型工作流**：

```bash
# 1. 启动开发环境
make dev

# 2. 在另一个终端查看日志
make logs-api

# 3. 修改 arrow_lake/ 下的代码 — API 自动热重载

# 4. 进入容器调试
make shell-api

# 5. 打开 Jupyter 写实验代码
# 浏览器访问 http://localhost:8888
```

**资源需求**：~12 GB 内存，4 CPU

### GPU 环境 (gpu)

```bash
make gpu
```

**启动服务**：API (CPU) + MinIO + Ray Head (GPU) + Ray Worker (GPU)

**特点**：
- Ray 节点使用 `Dockerfile.gpu` 构建 (CUDA 12.4 + PyTorch CUDA)
- 每个 Ray 节点默认分配 1 张 GPU，通过环境变量调整
- 嵌入模型加载到 GPU，推理速度提升 5-10 倍

**GPU 配置**：

```bash
# .env 中调整
GPU_COUNT_PER_HEAD=1      # Ray Head 使用的 GPU 数
GPU_COUNT_PER_WORKER=1    # 每个 Worker 使用的 GPU 数
RAY_WORKER_REPLICAS=2     # Worker 副本数
```

**验证 GPU**：

```bash
make shell-ray
python -c "import torch; print(f'CUDA: {torch.cuda.is_available()}, GPUs: {torch.cuda.device_count()}')"
```

**资源需求**：~20 GB 内存，NVIDIA GPU (6 GB+ 显存)，4 CPU

### 生产环境 (production)

生产环境推荐使用 `core` profile + 反向代理 + TLS。

#### 最小生产部署

```bash
# 1. 准备配置
cd deploy
make env

# 2. 编辑 .env，设置安全凭证
#    - 修改 MINIO_ROOT_PASSWORD
#    - 设置 ARROW_LAKE__API__API_KEY
#    - 设置 ARROW_LAKE__AUTH__JWT_SECRET_KEY
#    - 配置 ARROW_LAKE__API__CORS_ORIGINS
#    - 设置 ARROW_LAKE__RATE_LIMIT__ENABLED=true

# 3. 构建镜像
make build

# 4. 启动
make up

# 5. 验证
curl -H "X-API-Key: your-api-key" http://localhost:8000/health
```

#### 生产环境 .env 必改项

```bash
# === 安全凭证 ===
MINIO_ROOT_PASSWORD=<生成强密码>
ARROW_LAKE__STORAGE__S3_ACCESS_KEY=<与MINIO一致>
ARROW_LAKE__STORAGE__S3_SECRET_KEY=<与MINIO一致>
ARROW_LAKE__API__API_KEY=<生成API密钥>
ARROW_LAKE__AUTH__JWT_SECRET_KEY=<生成JWT密钥>

# === 认证 ===
ARROW_LAKE__AUTH__AUTH_MODE=both
ARROW_LAKE__RATE_LIMIT__ENABLED=true
ARROW_LAKE__RATE_LIMIT__DEFAULT_REQUESTS_PER_MINUTE=120

# === 可观测性 ===
ARROW_LAKE__OPENTELEMETRY__ENABLED=true
ARROW_LAKE__OPENTELEMETRY__OTEL_ENDPOINT=http://jaeger:4317
ARROW_LAKE__OBSERVABILITY__LOG_LEVEL=WARNING

# === 资源限制 ===
API_MEMORY_LIMIT=4G
RAY_HEAD_MEMORY_LIMIT=8G
RAY_WORKER_REPLICAS=2
RAY_WORKER_MEMORY=8G
```

#### 生产环境加 Ray 集群

如果生产环境需要 Ray 做分布式计算 (数据摄取、嵌入生成等)：

```bash
# 同时启动 core + compute profile
docker compose \
  -f docker-compose.yml \
  --profile core \
  --profile compute \
  up -d
```

#### 反向代理 (Nginx)

生产环境建议在 API 前加 Nginx 做 TLS 终止：

```nginx
server {
    listen 443 ssl;
    server_name arrow-lake.example.com;

    ssl_certificate     /etc/nginx/ssl/server.crt;
    ssl_certificate_key /etc/nginx/ssl/server.key;

    client_max_body_size 100m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
    }
}
```

生成自签名证书（仅测试用）：

```bash
make certs
# 输出到 deploy/certs/server.key + server.crt
```

### 监控环境 (monitoring)

```bash
# 搭配开发环境
make full

# 或独立启动（需要先启动 core）
make up
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml --profile monitoring up -d
```

**启动服务**：Prometheus + Grafana + Jaeger

| 组件 | 端口 | 用途 |
|------|------|------|
| Prometheus | 9090 | 指标采集和告警 |
| Grafana | 3000 | 仪表盘可视化 |
| Jaeger | 16686 (UI) / 4317 (gRPC) / 4318 (HTTP) | 分布式追踪 |

**预置 Grafana 仪表盘**：

- `ingestion-dashboard.json` — 数据摄取监控
- `query-dashboard.json` — 查询性能分析
- `processing-dashboard.json` — 处理流水线指标
- `system-dashboard.json` — 系统资源使用
- `slo-dashboard.json` — SLO/SLA 追踪

**访问 Grafana**：
- 用户名：`.env` 中 `GRAFANA_ADMIN_USER` 的值
- 密码：`.env` 中 `GRAFANA_ADMIN_PASSWORD` 的值（`make env` 自动生成）

### 知识图谱环境 (kg)

```bash
make kg
```

**启动服务**：API + MinIO + HugeGraph

**配置**：

```bash
# .env
HUGEGRAPH_VERSION=1.7.0
HUGEGRAPH_PORT=8089
HUGEGRAPH_MEMORY_LIMIT=4G

# Arrow Lake KG 配置
ARROW_LAKE__HUGEGRAPH__ENABLED=true
ARROW_LAKE__HUGEGRAPH__HOST=hugegraph
ARROW_LAKE__HUGEGRAPH__PORT=8080
ARROW_LAKE__HUGEGRAPH__GRAPH_NAME=arrow_lake_kg
```

**验证**：

```bash
curl http://localhost:8089/graphs/arrow_lake_kg/schema
```

### OCR 环境 (ocr)

```bash
make ocr
```

**启动服务**：TurboOCR（仅内部网络，不暴露主机端口）

**安全设计**：
- OCR 服务仅通过 Docker 内部网络 `arrow-lake-net` 访问
- API 容器可通过 `http://turbo-ocr:8002` 调用 OCR
- 无主机端口暴露，外部无法直接访问

**注意**：需要 GPU + NVIDIA Container Toolkit。

---

## 配置说明

### 配置加载优先级

```
CLI 环境变量 > .env 文件 > docker-compose.yml 默认值
```

### 配置层级

Arrow Lake 使用 `ARROW_LAKE__` 前缀的环境变量，`__` 作为层级分隔符：

```bash
# 对应 config.main.ArrowLakeConfig.storage.s3_endpoint
ARROW_LAKE__STORAGE__S3_ENDPOINT=http://minio:9000

# 对应 config.api.ApiConfig.cors_origins
ARROW_LAKE__API__CORS_ORIGINS='["https://example.com"]'
```

### 核心配置项

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ARROW_LAKE__STORAGE__BACKEND` | minio | 存储后端：minio / s3 / gcs / local |
| `ARROW_LAKE__STORAGE__S3_ENDPOINT` | http://localhost:9000 | S3 兼容端点 |
| `ARROW_LAKE__EMBEDDING__MODEL` | Qwen/Qwen3-Embedding-0.6B | 嵌入模型 |
| `ARROW_LAKE__EMBEDDING__BACKEND` | local | 嵌入后端：local / openai / ray_serve |
| `ARROW_LAKE__API__API_KEY` | (空) | API 密钥（生产必须设置） |
| `ARROW_LAKE__AUTH__AUTH_MODE` | api_key | 认证模式：api_key / jwt / both |
| `ARROW_LAKE__RATE_LIMIT__ENABLED` | false | 速率限制 |
| `ARROW_LAKE__OPENTELEMETRY__ENABLED` | false | 分布式追踪 |

完整配置见项目根目录 `.env.example`。

---

## 服务架构

```
┌──────────────────────────────────────────────────────────────────────┐
│                         Docker Network: arrow-lake-net              │
│                                                                      │
│  ┌──────────┐  ┌────────────┐  ┌────────────┐  ┌───────────────┐   │
│  │   API    │  │  Ray Head  │  │   MinIO    │  │   HugeGraph   │   │
│  │ :8000    │──│ :6379/:8265│──│ :9000/:9001│  │   :8089→8080  │   │
│  └──────────┘  └─────┬──────┘  └────────────┘  └───────────────┘   │
│                      │                                                │
│               ┌──────┴──────┐  ┌────────────┐                       │
│               │ Ray Worker  │  │   Jupyter   │  ┌──────────────┐   │
│               │  (N 副本)   │  │   :8888    │  │  TurboOCR    │   │
│               └─────────────┘  └────────────┘  │  :8002(内部) │   │
│                                                  └──────────────┘   │
└──────────────────────────────────────────────────────────────────────┘

  监控栈 (monitoring profile):
  ┌───────────┐  ┌────────┐  ┌─────────┐
  │Prometheus │  │ Grafana│  │ Jaeger  │
  │  :9090    │  │ :3000  │  │ :16686  │
  └───────────┘  └────────┘  └─────────┘
```

### 端口映射

| 服务 | 容器端口 | 主机端口 | Profile |
|------|---------|---------|---------|
| API | 8000 | 8000 (可配) | core, dev, gpu, monitoring |
| MinIO API | 9000 | 9000 (可配) | core, dev, gpu, monitoring |
| MinIO Console | 9001 | 9001 (可配) | core, dev, gpu, monitoring |
| Ray GCS | 6379 | 6378 | compute, dev, gpu, monitoring |
| Ray Dashboard | 8265 | 8265 | compute, dev, gpu, monitoring |
| Jupyter | 8888 | 8888 (可配) | dev |
| HugeGraph | 8080 | 8089 (可配) | kg |
| TurboOCR | 8002 | (仅内部) | ocr |
| Prometheus | 9090 | 9090 (可配) | monitoring |
| Grafana | 3000 | 3000 (可配) | monitoring |
| Jaeger UI | 16686 | 16686 (可配) | monitoring |
| Jaeger OTLP | 4317/4318 | 4317/4318 | monitoring |

---

## 常用运维

### 服务管理

```bash
make ps              # 查看服务状态
make logs            # 查看所有日志 (最近 50 行)
make logs-api        # 跟踪 API 日志
make logs-ray        # 跟踪 Ray Head 日志
make logs-jupyter    # 跟踪 Jupyter 日志
make restart         # 重启所有服务
make down            # 停止所有服务 (保留数据)
make clean           # 停止所有服务 + 删除数据卷
```

### 进入容器

```bash
make shell-api       # 进入 API 容器
make shell-ray       # 进入 Ray Head 容器
make shell-jupyter   # 进入 Jupyter 容器
```

### 镜像构建

```bash
make build           # 构建 CPU + GPU 镜像
make build-api       # 仅构建 API 镜像
make build-gpu       # 仅构建 GPU 镜像 (Ray)

# 手动构建
docker build -f deploy/Dockerfile -t arrow-lake:latest .
docker build -f deploy/Dockerfile.gpu -t arrow-lake:gpu .

# 多架构构建
docker buildx build --platform linux/amd64,linux/arm64 \
  -f deploy/Dockerfile -t arrow-lake:latest .
```

### 数据卷管理

```bash
# 查看卷
docker volume ls | grep arrow-lake

# 查看卷详情
docker volume inspect arrow-lake_minio-data
docker volume inspect arrow-lake_lake-data

# 备份 MinIO 数据
docker run --rm -v arrow-lake_minio-data:/data -v $(pwd):/backup \
  alpine tar czf /backup/minio-data-backup.tar.gz /data

# 备份 Lake 数据
docker run --rm -v arrow-lake_lake-data:/data -v $(pwd):/backup \
  alpine tar czf /backup/lake-data-backup.tar.gz /data
```

### 扩缩容 Ray Worker

```bash
# .env 中调整副本数
RAY_WORKER_REPLICAS=3

# 重启 Worker
docker compose -f docker-compose.yml -f docker-compose.dev.yml \
  --profile dev up -d ray-worker
```

---

## 故障排查

### 容器启动失败

```bash
# 查看详细日志
docker compose logs api --tail=100

# 查看容器退出码
docker compose ps -a

# 检查配置是否有效
docker compose config
```

### MinIO 连接失败

```bash
# 检查 MinIO 健康状态
curl http://localhost:9000/minio/health/live

# 检查 bucket 是否创建
docker compose exec minio mc ls local/

# 手动创建 bucket
docker compose exec minio mc mb local/arrow-lake --ignore-existing
```

### 健康检查失败

```bash
# 手动执行健康检查命令
docker compose exec api curl -sf http://localhost:8000/health

# 查看健康检查日志
docker inspect --format='{{json .State.Health}}' arrow-lake-api | python -m json.tool
```

### GPU 不可用

```bash
# 检查 NVIDIA 驱动
nvidia-smi

# 检查 Container Toolkit
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi

# 检查容器内 GPU
make shell-ray
python -c "import torch; print(torch.cuda.is_available())"
```

### 内存不足 (OOM)

```bash
# 查看容器资源使用
docker stats --no-stream

# 调整 .env 中的内存限制
# API_MEMORY_LIMIT=4G
# RAY_HEAD_MEMORY_LIMIT=8G
# RAY_WORKER_MEMORY=8G
```

### 端口冲突

```bash
# 查看端口占用
lsof -i :8000
lsof -i :9000

# 修改 .env 中的端口
# API_PORT=8080
# MINIO_API_PORT=19000
```

---

## 安全加固清单

生产部署前，确认以下所有项：

### 凭证

- [ ] `MINIO_ROOT_PASSWORD` 已改为强密码
- [ ] `ARROW_LAKE__API__API_KEY` 已设置为强随机值（禁止留空或使用 dev 默认值）
- [ ] `ARROW_LAKE__AUTH__JWT_SECRET_KEY` 已设置为 32+ 字符随机值（如使用 JWT）
- [ ] `GRAFANA_ADMIN_PASSWORD` 已修改
- [ ] `REDIS_PASSWORD` 已设置为强随机值
- [ ] `.env` 文件未提交到版本控制（已在 `.gitignore` 中）

### API 安全

- [ ] `ARROW_LAKE__API__DOCS_ENABLED=false`（关闭 Swagger/OpenAPI 文档暴露）
- [ ] `ARROW_LAKE__AUTH__AUTH_MODE` 设为 `both` 或 `jwt`
- [ ] `ARROW_LAKE__RATE_LIMIT__ENABLED=true`
- [ ] `ARROW_LAKE__API__CORS_ORIGINS` 限制为信任域名
- [ ] MinIO Console 不暴露公网 (仅 `127.0.0.1:9001`)

> **⚠️ 空密钥 = 无认证**：`ARROW_LAKE__API__API_KEY` 为空时认证中间件被跳过，
> 所有 API 端点可无密钥访问。生产环境**必须**设置为强随机值：
> ```bash
> python3 -c "import secrets; print(secrets.token_urlsafe(32))"
> ```

### 网络安全

- [ ] 生产环境**不使用** `dev` profile（无 Jupyter、无源码挂载）
- [ ] 反向代理 (Nginx) 配置 TLS
- [ ] MinIO API 端口不暴露公网（通过 Nginx 代理或 VPN 访问）

### 容器安全

- [ ] 所有容器以非 root 用户运行（已内置 `arrowlake` 用户）
- [ ] 文件系统只读 (`read_only: true`)
- [ ] 最小 Linux capabilities (`cap_drop: ALL`)
- [ ] 进程数限制 (`pids: 256/512`)
- [ ] 资源限制 (memory/cpu limits)
- [ ] 日志轮转 (50m × 5 files)

### 可观测性

- [ ] `ARROW_LAKE__OPENTELEMETRY__ENABLED=true`
- [ ] Prometheus 指标采集正常
- [ ] 日志级别设为 `WARNING` 或 `INFO`

---

## Kubernetes 部署

对于大规模生产环境，项目提供 Helm Chart：

```bash
# 查看 Charts
ls deploy/helm/arrow-lake/

# 安装
helm install arrow-lake deploy/helm/arrow-lake/ \
  -f deploy/helm/arrow-lake/values.yaml \
  --namespace arrow-lake --create-namespace

# 生产 values
helm install arrow-lake deploy/helm/arrow-lake/ \
  -f deploy/helm/arrow-lake/values.yaml \
  --namespace arrow-lake --create-namespace

# 开发 values
helm install arrow-lake deploy/helm/arrow-lake/ \
  -f deploy/helm/arrow-lake/values-dev.yaml \
  --namespace arrow-lake-dev --create-namespace
```

Helm Chart 包含：
- Deployment + Service
- NetworkPolicy (网络隔离)
- PrometheusRule (告警规则)
- SLO 追踪

---

## 文件清单

```
deploy/
├── docker-compose.yml              # 基础配置 (所有环境)
├── docker-compose.dev.yml          # 开发覆盖
├── docker-compose.gpu.yml          # GPU 覆盖
├── docker-compose.monitoring.yml   # 监控覆盖
├── docker-compose.hugegraph.yml    # 知识图谱覆盖
├── Dockerfile                      # CPU 多阶段构建
├── Dockerfile.gpu                  # GPU 多阶段构建 (CUDA 12.4)
├── Makefile                        # 运维命令快捷方式
├── .dockerignore                   # 构建上下文排除
├── README.md                       # 本文件
├── scripts/
│   ├── init-env.sh                 # 环境初始化 (生成 .env)
│   └── gen-certs.sh               # TLS 证书生成
├── monitoring/
│   ├── prometheus/
│   │   └── prometheus.yml          # Prometheus 抓取配置
│   └── grafana/
│       └── provisioning/
│           └── datasources/
│               └── prometheus.yml  # Grafana 数据源
├── grafana/                        # 预置仪表盘 JSON
│   ├── ingestion-dashboard.json
│   ├── query-dashboard.json
│   ├── processing-dashboard.json
│   ├── system-dashboard.json
│   ├── slo-dashboard.json
│   └── otm-dashboard.json
├── minio-init/
│   └── create-bucket.sh           # MinIO 初始化脚本
└── helm/
    └── arrow-lake/                 # Helm Chart
        ├── Chart.yaml
        ├── values.yaml
        ├── values-dev.yaml
        └── templates/
            ├── deployment.yaml
            ├── service.yaml
            ├── networkpolicy.yaml
            ├── prometheusrule.yaml
            └── _helpers.tpl
```
