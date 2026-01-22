# DIntelliHub 本地开发环境设置指南

**更新日期**: 2026-01-22
**环境类型**: Docker Compose 本地开发环境
**目的**: 快速启动开发环境，无需等待云资源申请

---

## 🎯 环境概述

### 开发环境架构

使用Docker Compose部署本地开发环境，包含以下服务：

| 服务 | 容器名 | 端口 | 用途 |
|------|--------|------|------|
| **PostgreSQL** | dintellihub-postgres | 5432 | 元数据库 (Gravitino) |
| **MinIO** | dintellihub-minio | 9000, 9001 | 对象存储 (替代S3) |
| **LanceDB** | dintellihub-lancedb | 8080 | 向量数据库 |
| **Redis** | dintellihub-redis | 6379 | 缓存 (可选) |
| **Prometheus** | dintellihub-prometheus | 9090 | 监控 |
| **Grafana** | dintellihub-grafana | 3000 | 可视化 |
| **Daft Processing** | dintellihub-daft | 8000 | 数据处理服务 |

### 网络架构
```
┌─────────────────────────────────────────────┐
│          Docker Network: dintellihub-net     │
├─────────────────────────────────────────────┤
│                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │PostgreSQL│  │  MinIO   │  │ LanceDB  │  │
│  │  :5432   │  │ :9000    │  │  :8080   │  │
│  └──────────┘  └──────────┘  └──────────┘  │
│                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │  Redis   │  │Prometheus│  │ Grafana  │  │
│  │  :6379   │  │  :9090   │  │  :3000   │  │
│  └──────────┘  └──────────┘  └──────────┘  │
│                                             │
│  ┌──────────────────────────────────┐      │
│  │      Daft Processing API          │      │
│  │           :8000                   │      │
│  └──────────────────────────────────┘      │
│                                             │
└─────────────────────────────────────────────┘
                    │
                    ▼
          ┌─────────────────┐
          │  Local Machine  │
          │  (开发环境)      │
          └─────────────────┘
```

---

## 📋 前置要求

### 必须安装
- **Docker**: >= 20.10
- **Docker Compose**: >= 2.0
- **Python**: >= 3.10
- **Git**: >= 2.0

### 验证安装
```bash
# 检查Docker
docker --version
# 输出: Docker version 20.10.x or higher

# 检查Docker Compose
docker compose version
# 输出: Docker Compose version v2.x.x or higher

# 检查Python
python --version
# 输出: Python 3.10.x or higher
```

---

## 🚀 快速启动

### Step 1: 克隆项目仓库
```bash
git clone https://github.com/your-org/wits-infra-dintellihub.git
cd wits-infra-dintellihub
```

### Step 2: 启动所有服务
```bash
# 启动所有服务（后台运行）
docker compose up -d

# 查看服务状态
docker compose ps

# 查看日志
docker compose logs -f
```

**预期输出**:
```
NAME                      IMAGE                      STATUS
dintellihub-postgres      postgres:14-alpine         Up (healthy)
dintellihub-minio         minio/minio:latest         Up (healthy)
dintellihub-lancedb       lancedb/lancedb:latest     Up
dintellihub-redis         redis:7-alpine            Up (healthy)
dintellihub-prometheus    prom/prometheus:latest     Up
dintellihub-grafana       grafana/grafana:latest    Up
dintellihub-daft          dintellihub-daft          Up
```

### Step 3: 验证服务
```bash
# 测试PostgreSQL连接
docker exec -it dintellihub-postgres psql -U admin -d gravitino -c "SELECT 1;"

# 测试MinIO (访问Console)
open http://localhost:9001
# 用户名: minioadmin
# 密码: minioadmin123

# 测试LanceDB
curl http://localhost:8080/health

# 测试Grafana
open http://localhost:3000
# 用户名: admin
# 密码: admin123
```

### Step 4: 初始化Python开发环境
```bash
# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安装依赖
pip install --upgrade pip
pip install daft lancedb datajuicer fastapi "uvicorn[standard]"
pip install psycopg2-binary redis python-dotenv
```

---

## 📂 项目结构

```
wits-infra-dintellihub/
├── docker-compose.yml              # Docker Compose配置
├── .env                            # 环境变量（可选）
├── requirements.txt                # Python依赖
├── src/                            # 源代码
│   ├── api/                        # API服务
│   │   └── main.py                 # FastAPI入口
│   ├── processing/                 # 数据处理模块
│   │   ├── daft_pipeline.py        # Daft处理pipeline
│   │   └── data_quality.py         # DataJuicer质量处理
│   ├── vector/                     # 向量检索模块
│   │   ├── lancedb_client.py       # LanceDB客户端
│   │   └── embedding_service.py    # 向量嵌入服务
│   └── metadata/                   # 元数据模块
│       └── gravitino_client.py     # Gravitino客户端
├── data/                           # 本地数据目录
│   ├── raw/                        # 原始数据
│   ├── processed/                  # 处理后数据
│   └── test/                       # 测试数据
├── tests/                          # 测试代码
│   ├── test_daft_pipeline.py
│   ├── test_lancedb.py
│   └── test_api.py
├── deployments/                    # 部署配置
│   ├── monitoring/
│   │   ├── prometheus.yml
│   │   └── grafana/
│   └── daft/
│       └── Dockerfile
└── notebooks/                      # Jupyter notebooks
    ├── daft_tutorial.ipynb
    └── lancedb_tutorial.ipynb
```

---

## 🔧 常用命令

### 服务管理
```bash
# 启动所有服务
docker compose up -d

# 停止所有服务
docker compose stop

# 重启服务
docker compose restart

# 停止并删除所有容器
docker compose down

# 停止并删除所有容器和数据卷
docker compose down -v

# 查看日志
docker compose logs -f [service_name]

# 查看特定服务日志
docker compose logs -f daft-processing
```

### 数据库操作
```bash
# 连接PostgreSQL
docker exec -it dintellihub-postgres psql -U admin -d gravitino

# 备份数据库
docker exec dintellihub-postgres pg_dump -U admin gravitino > backup.sql

# 恢复数据库
docker exec -i dintellihub-postgres psql -U admin gravitino < backup.sql
```

### MinIO操作
```bash
# 使用MinIO Client (mc)
docker run --rm --network dintellihub-net minio/mc \
  alias set local http://minio:9000 minioadmin minioadmin123

# 列出所有bucket
docker run --rm --network dintellihub-net minio/mc \
  ls local/

# 上传文件
docker run --rm --network dintellihub-net -v $(pwd)/data:/data minio/mc \
  cp /data/test.json local/dintellihub-raw/

# 下载文件
docker run --rm --network dintellihub-net -v $(pwd)/data:/data minio/mc \
  cp local/dintellihub-processed/output.json /data/
```

---

## 💻 开发工作流

### 1. 本地开发Daft Pipeline

```python
# src/processing/daft_pipeline.py
import daft

# 从MinIO读取数据
df = daft.read_csv(
    "s3://dintellihub-raw/data.csv",
    storage_options={
        "key": "minioadmin",
        "secret": "minioadmin123",
        "endpoint_url": "http://localhost:9000"
    }
)

# 数据处理
df = df.filter(df["score"] > 0.5)
df = df.select(["id", "text", "score"])

# 写入MinIO
df.write_parquet(
    "s3://dintellihub-processed/output.parquet",
    storage_options={
        "key": "minioadmin",
        "secret": "minioadmin123",
        "endpoint_url": "http://localhost:9000"
    }
)
```

### 2. 本地开发LanceDB向量检索

```python
# src/vector/lancedb_client.py
import lancedb

# 连接LanceDB
db = lancedb.connect("/data/lancedb")  # 或连接到容器: "http://localhost:8080"

# 创建表
table = db.create_table(
    "documents",
    data=[
        {"id": 1, "text": "hello world", "vector": [0.1, 0.2, ...]}
    ]
)

# 向量搜索
results = table.search("query text").limit(5).to_pandas()
```

### 3. 运行测试

```bash
# 激活虚拟环境
source venv/bin/activate

# 运行所有测试
pytest tests/

# 运行特定测试
pytest tests/test_daft_pipeline.py -v

# 生成覆盖率报告
pytest tests/ --cov=src --cov-report=html
```

---

## 🐛 调试技巧

### 查看容器日志
```bash
# 实时查看所有服务日志
docker compose logs -f

# 查看特定服务日志
docker compose logs -f daft-processing

# 查看最近100行日志
docker compose logs --tail=100 postgres
```

### 进入容器调试
```bash
# 进入PostgreSQL容器
docker exec -it dintellihub-postgres sh

# 进入MinIO容器
docker exec -it dintellihub-minio sh

# 进入Daft Processing容器
docker exec -it dintellihub-daft bash
```

### 重启单个服务
```bash
# 重启Daft服务（不重启其他服务）
docker compose restart daft-processing

# 重建并启动Daft服务
docker compose up -d --build daft-processing
```

---

## 📊 监控和可视化

### Prometheus监控指标
访问 http://localhost:9090

**关键指标**:
- `daft_processing_duration_seconds` - Daft处理时间
- `lancedb_query_duration_seconds` - LanceDB查询时间
- `api_request_duration_seconds` - API请求时间

### Grafana仪表板
访问 http://localhost:3000

**登录信息**:
- 用户名: `admin`
- 密码: `admin123`

**预配置仪表板**:
- DIntelliHub Overview - 系统总览
- Daft Processing - 数据处理性能
- LanceDB Performance - 向量检索性能
- API Metrics - API性能指标

---

## 🔐 环境变量配置

### 创建 .env 文件（可选）
```bash
# .env
# PostgreSQL
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_USER=admin
POSTGRES_PASSWORD=admin123
POSTGRES_DB=gravitino

# MinIO (S3 compatible)
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin123
USE_SSL=false

# LanceDB
LANCEDB_URI=/data/lancedb
# 或使用远程: LANCEDB_URI=http://localhost:8080

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379

# API
API_HOST=0.0.0.0
API_PORT=8000
DEBUG=true
```

### 在Python中使用环境变量
```python
import os
from dotenv import load_dotenv

load_dotenv()

postgres_host = os.getenv("POSTGRES_HOST", "localhost")
postgres_port = os.getenv("POSTGRES_PORT", "5432")
```

---

## 🧪 测试数据准备

### 生成测试数据
```bash
# 创建测试数据目录
mkdir -p data/test

# 生成测试CSV文件
cat > data/test/sample_data.csv << EOF
id,text,score
1,hello world,0.8
2,daft processing,0.9
3,lancedb vector,0.7
EOF

# 上传到MinIO
docker run --rm --network dintellihub-net \
  -v $(pwd)/data/test:/data \
  minio/mc \
  cp /data/sample_data.json local/dintellihub-raw/
```

---

## 🚨 常见问题

### Q1: 端口冲突
**问题**: `Error: port is already allocated`

**解决方案**:
```bash
# 修改docker-compose.yml中的端口映射
ports:
  - "5433:5432"  # 将5432改为5433
```

### Q2: 容器无法启动
**问题**: 容器状态为 `Exit 1`

**解决方案**:
```bash
# 查看详细日志
docker compose logs [service_name]

# 检查容器状态
docker compose ps -a

# 重建容器
docker compose down
docker compose up -d --build
```

### Q3: 无法连接PostgreSQL
**问题**: `connection refused`

**解决方案**:
```bash
# 检查PostgreSQL是否健康
docker compose ps postgres

# 等待健康检查通过
docker compose logs -f postgres

# 手动测试连接
docker exec -it dintellihub-postgres psql -U admin -d gravitino
```

### Q4: MinIO上传文件失败
**问题**: `The bucket does not exist`

**解决方案**:
```bash
# 手动创建bucket
docker exec -it dintellihub-minio-init /bin/sh
mc mb minio/dintellihub-raw
```

---

## 📚 下一步

### 立即开始
1. ✅ 启动开发环境: `docker compose up -d`
2. ✅ 验证所有服务: `docker compose ps`
3. ✅ 连接数据库测试
4. ✅ 运行第一个Daft pipeline
5. ✅ 运行第一个LanceDB查询

### 学习资源
- [Docker Compose文档](https://docs.docker.com/compose/)
- [Daft官方文档](https://docs.daft.ai/en/stable/)
- [LanceDB官方文档](https://lancedb.github.io/lancedb/)
- [FastAPI文档](https://fastapi.tiangolo.com/)

---

## 🔄 从Docker Compose迁移到K8s

### 为什么先用Docker Compose？
1. ✅ 快速启动，无需等待云资源申请
2. ✅ 降低初期成本
3. ✅ 便于本地开发和调试
4. ✅ 验证技术栈可行性

### 何时迁移到K8s？
- [ ] 团队规模 > 5人
- [ ] 数据量 > 100GB
- [ ] 需要高可用和自动扩缩容
- [ ] 准备部署到生产环境

### 迁移路径
1. Sprint 1-2: Docker Compose本地开发
2. Sprint 3-4: EKS/GKE测试环境部署
3. Sprint 5-6: 生产环境K8s集群

---

**环境状态**: 🟢 可用
**最后更新**: 2026-01-22
**维护者**: Winston (架构师)

**开始使用Docker Compose快速开发吧！** 🚀
