# DIntelliHub 架构调整方案

**调整日期**: 2026-01-22
**参考**: Shannon项目向量数据库抽象层设计
**目标**: 使用Docker容器统一管理LanceDB和Daft环境

---

## 📋 调整概述

### 原计划 vs 调整后

| 组件 | 原计划 | 调整后 | 理由 |
|------|--------|--------|------|
| **LanceDB** | Python库 | HTTP服务 (FastAPI) | 50倍性能提升，支持高并发 |
| **Daft** | Python库 | HTTP服务 (FastAPI) | 统一管理，支持分布式 |
| **部署方式** | 本地venv | Docker容器 | 环境隔离，易于扩展 |
| **服务通信** | 函数调用 | RESTful API | 解耦，易监控 |

---

## 🏗️ 新架构设计

### 服务架构图

```
┌─────────────────────────────────────────────────────┐
│            Docker Network: dintellihub-net          │
├─────────────────────────────────────────────────────┤
│                                                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ PostgreSQL  │  │    MinIO     │  │    Redis     │  │
│  │   :15432     │  │  :9000/9001  │  │   :16379     │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
│                                                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ LanceDB     │  │  Daft       │  │ Prometheus   │  │
│  │ HTTP Service│  │ HTTP Service│  │  :9090       │  │
│  │   :8765     │  │   :8000     │  └──────────────┘  │
│  └──────────────┘  └──────────────┘  ┌──────────────┐  │
│                                            │  Grafana   │  │
│                                            │  :13000    │  │
│                                            └──────────────┘  │
│                                                       │
└─────────────────────────────────────────────────────┘
```

### HTTP API服务

#### LanceDB HTTP服务 (FastAPI)

**端口**: 8765
**功能**:
- `/health` - 健康检查
- `/api/v1/search` - 向量搜索
- `/api/v1/upsert` - 数据写入
- `/api/v1/get_recent` - 最近记录查询
- `/api/v1/delete` - 删除记录

**优势**:
- ✅ 性能：10,000 QPS (vs 200 QPS子进程方案)
- ✅ 延迟：P99 <20ms (vs 150ms子进程方案)
- ✅ 高并发：支持多worker
- ✅ 易监控：独立进程，可观测性强

#### Daft HTTP服务 (FastAPI)

**端口**: 8000
**功能**:
- `/health` - 健康检查
- `/api/v1/process` - 数据处理任务
- `/api/v1/etl` - ETL pipeline
- `/api/v1/query` - 数据查询

**优势**:
- ✅ 分布式处理：Ray集群支持
- ✅ 多模态：文本、图像、音频、视频
- ✅ AI函数：集成OpenAI、Cohere
- ✅ 懒执行：优化查询性能

---

## 📦 Docker服务配置

### docker-compose.yml 更新

```yaml
services:
  # ... PostgreSQL, MinIO, Redis, Prometheus, Grafana (保持不变) ...

  # LanceDB HTTP Service (新增)
  lancedb-service:
    build:
      context: ./python
      dockerfile: Dockerfile.lancedb
    container_name: dintellihub-lancedb
    environment:
      - LANCEDB_URI=/data/lancedb
      - PORT=8765
      - HOST=0.0.0.0
      - PYTHONUNBUFFERED=1
    volumes:
      - lancedb_data:/data/lancedb
      - ./python/lancedb:/app/lancedb
    ports:
      - "8765:8765"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8765/health"]
      interval: 30s
      timeout: 10s
      retries: 3
    restart: unless-stopped
    networks:
      - dintellihub-net

  # Daft Processing HTTP Service (新增)
  daft-service:
    build:
      context: ./python
      dockerfile: Dockerfile.daft
    container_name: dintellihub-daft
    environment:
      - MINIO_ENDPOINT=minio:9000
      - MINIO_ACCESS_KEY=minioadmin
      - MINIO_SECRET_KEY=minioadmin123
      - POSTGRES_HOST=postgres
      - POSTGRES_PORT=5432
      - POSTGRES_USER=admin
      - POSTGRES_PASSWORD=admin123
      - POSTGRES_DB=gravitino
      - LANCEDB_SERVICE_URL=http://lancedb-service:8765
      - PYTHONUNBUFFERED=1
      - RAY_ADDRESS=ray://ray-head:10001
      - DAFT_WORKERS=4
      - DAFT_MEMORY_LIMIT=16GB
    volumes:
      - ./python/daft:/app/daft
      - ./data:/app/data
      - daft_cache:/tmp/daft_cache
    ports:
      - "8000:8000"
    depends_on:
      postgres:
        condition: service_healthy
      minio:
        condition: service_healthy
      lancedb-service:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
    restart: unless-stopped
    networks:
      - dintellihub-net

volumes:
  # ... existing volumes ...
  lancedb_data:
    driver: local
  daft_cache:
    driver: local
```

---

## 📂 项目结构调整

```
wits-infra-dintellihub/
├── docker-compose.yml
├── python/
│   ├── requirements.txt
│   ├── Dockerfile.lancedb      # LanceDB服务镜像
│   ├── Dockerfile.daft         # Daft服务镜像
│   ├── lancedb/
│   │   ├── __init__.py
│   │   ├── main.py             # FastAPI应用
│   │   ├── models.py           # 数据模型
│   │   └── config.py            # 配置
│   └── daft/
│       ├── __init__.py
│       ├── main.py              # FastAPI应用
│       ├── processing.py        # Daft处理逻辑
│       ├── etl.py               # ETL pipeline
│       └── ray_cluster.py       # Ray集群配置
├── data/
│   ├── raw/                    # 原始数据
│   ├── processed/              # 处理后数据
│   └── test/                  # 测试数据
└── src/                        # 应用代码（Python/Go）
    ├── api/
    ├── vector/
    └── processing/
```

---

## 🔧 实现细节

### LanceDB HTTP服务实现

**文件**: `python/lancedb/main.py`

```python
"""
LanceDB HTTP服务
为DIntelliHub提供向量数据库RESTful API
"""
from fastapi import FastAPI, HTTPException
from lancedb import connect
import logging

logger = logging.getLogger(__name__)

app = FastAPI(title="DIntelliHub LanceDB Service")

db = None

@app.on_event("startup")
async def startup():
    global db
    db_path = os.getenv("LANCEDB_URI", "/data/lancedb")
    db = connect(db_path)
    logger.info(f"LanceDB connected: {db_path}")

@app.get("/health")
async def health():
    return {"status": "ok", "service": "lancedb"}

@app.post("/api/v1/search")
async def semantic_search(request: SearchRequest):
    """语义向量搜索"""
    # 实现细节...
    pass

@app.post("/api/v1/upsert")
async def upsert(request: UpsertRequest):
    """插入或更新记录"""
    # 实现细节...
    pass

@app.post("/api/v1/get_recent")
async def get_recent(request: RecentRequest):
    """获取最近记录"""
    # 实现细节...
    pass
```

### Daft HTTP服务实现

**文件**: `python/daft/main.py`

```python
"""
Daft处理服务
为DIntelliHub提供分布式数据处理RESTful API
"""
from fastapi import FastAPI
import daft

app = FastAPI(title="DIntelliHub Daft Service")

@app.get("/health")
async def health():
    return {"status": "ok", "service": "daft"}

@app.post("/api/v1/process")
async def process_data(request: ProcessRequest):
    """处理数据"""
    # 实现细节...
    pass

@app.post("/api/v1/etl")
async def run_etl(request: ETLRequest):
    """运行ETL pipeline"""
    # 实现细节...
    pass
```

---

## 📅 Sprint 1 调整计划

### Week 1: Day 2 (今天) - HTTP服务搭建

**任务调整**:

| 原计划 | 调整后 | 工时 |
|--------|--------|------|
| LanceDB配置（Python库） | LanceDB HTTP服务搭建 | 4h |
| 监控系统配置 | Python服务监控配置 | 2h |
| Python虚拟环境 | Docker镜像构建 | 4h |
| 安装依赖 | requirements.txt更新 | 1h |
| 创建项目结构 | Python代码结构创建 | 2h |
| 环境变量配置 | .env和docker-compose.yml更新 | 1h |

**新任务**:
- [ ] 创建Dockerfile.lancedb
- [ ] 创建Dockerfile.daft
- [ ] 实现LanceDB FastAPI服务
- [ ] 实现Daft FastAPI服务
- [ ] 配置服务健康检查
- [ ] 测试服务间通信

### Week 1: Day 3 (明天) - 服务集成和测试

**任务**:
- [ ] 服务间网络测试
- [ ] API端点测试
- [ ] 集成测试
- [ ] 性能基准测试
- [ ] 监控面板配置

---

## 🎯 关键决策

### 为什么选择HTTP服务方案？

| 维度 | 子进程方案 | HTTP服务方案 ⭐ |
|------|-----------|--------------|
| **性能** | 200 QPS | 10,000 QPS |
| **延迟** | P99 150ms | P99 20ms |
| **并发** | 串行 | 并发（多worker） |
| **部署** | 复杂 | 简单（Docker） |
| **监控** | 困难 | 简单（独立进程） |
| **扩展** | 难以扩展 | 水平扩展（多实例）|

### 与Shannon项目的差异

| 项目 | Shannon | DIntelliHub |
|------|---------|-------------|
| **调用方** | Go Orchestrator | Python/Go混合 |
| **用途** | 向量搜索 | 数据处理+向量 |
| **LanceDB** | 向量存储 | 向量存储+嵌入 |
| **Daft** | 无 | 核心组件 |

---

## 📊 性能预期

### LanceDB HTTP服务

| 指标 | 目标值 | 说明 |
|------|--------|------|
| P50延迟 | < 10ms | 语义搜索 |
| P99延迟 | < 20ms | 语义搜索 |
| 吞吐量 | > 10,000 QPS | 并发搜索 |
| 并发worker | 4个 | 可扩展 |

### Daft HTTP服务

| 指标 | 目标值 | 说明 |
|------|--------|------|
| 处理速度 | > 1GB/min | 单worker |
| 并发worker | 4个 | Ray集群 |
| 内存限制 | 16GB/worker | 可配置 |

---

## ⚠️ 风险与缓解

### 风险1: HTTP服务复杂度增加
- **影响**: 中
- **概率**: 中
- **缓解**:
  - 使用成熟框架（FastAPI）
  - 完善的健康检查
  - 详细的日志记录
  - 参考Shannon项目成熟方案

### 风险2: 网络调用开销
- **影响**: 低
- **概率**: 低
- **缓解**:
  - Docker内网通信，延迟<5ms
  - 连接复用
  - 批量操作

### 风险3: Docker镜像构建时间
- **影响**: 低
- **概率**: 中
- **缓解**:
  - 利用Docker缓存
  - 多阶段构建
  - 提前构建基础镜像

---

## 🚀 下一步行动

### 立即行动

1. **创建Dockerfile**
   - Dockerfile.lancedb
   - Dockerfile.daft

2. **实现FastAPI服务**
   - python/lancedb/main.py
   - python/daft/main.py

3. **更新requirements.txt**
   - FastAPI
   - LanceDB
   - Daft
   - Uvicorn/Gunicorn

4. **更新docker-compose.yml**
   - 添加lancedb-service
   - 添加daft-service

5. **测试服务**
   - 健康检查
   - API调用
   - 性能测试

---

## 📈 预期收益

### 性能提升
- LanceDB吞吐量: **50倍提升**
- LanceDB延迟: **7倍降低**
- Daft处理: **分布式支持**

### 开发效率
- 统一Docker管理 ✅
- 环境隔离 ✅
- 易于调试 ✅
- 水平扩展 ✅

### 架构优势
- 服务解耦 ✅
- 技术栈统一 ✅
- 易于监控 ✅
- 生产就绪 ✅

---

**调整状态**: 📝 计划制定完成
**创建日期**: 2026-01-22
**预计执行**: Week 1 Day 2-3

**基于Shannon项目成熟方案，DIntelliHub将采用HTTP服务架构！** 🚀
