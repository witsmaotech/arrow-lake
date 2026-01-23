# DIntelliHub - 分布式智能数据中心

基于 Daft + LanceDB 的大规模数据处理和向量检索平台。

## 🎯 项目概述

DIntelliHub 是一个分布式智能数据平台，专注于：
- **大规模数据处理**: 使用 Daft 进行高性能 ETL
- **向量检索**: 使用 LanceDB 进行语义搜索
- **多模态支持**: 文本、图像、音频数据处理
- **AI 集成**: 内置 AI 函数和模型集成

## 🏗️ 架构

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  数据源     │────▶│   Daft      │────▶│  LanceDB    │
│ (MinIO/S3)  │     │ (数据处理)  │     │ (向量存储)  │
└─────────────┘     └─────────────┘     └─────────────┘
                            │
                            ▼
                     ┌─────────────┐
                     │  PostgreSQL │
                     │  (元数据)   │
                     └─────────────┘
```

## 🚀 快速开始

### 1. 启动所有服务
```bash
docker compose up -d
```

### 2. 验证服务健康
```bash
python tests/validate_environment.py
```

### 3. 访问服务
- **LanceDB API**: http://localhost:8765/docs
- **Daft API**: http://localhost:8001/docs
- **Grafana**: http://localhost:13000 (admin/admin123)
- **Prometheus**: http://localhost:9090

## 📊 当前状态

**Sprint**: 1 - Week 1 ✅ 完成 | Week 2 🟡 进行中

### Week 1 成果
- ✅ 7 个核心服务部署完成
- ✅ 监控和测试体系建立
- ✅ 18,677 行代码交付
- ✅ 100% 服务健康率

### Week 2 目标
- ⏳ Daft 数据处理 POC
- ⏳ LanceDB 向量检索 POC
- ⏳ POC 验证报告

## 📁 项目结构

```
wits-infra-dintellihub/
├── docker-compose.yml          # Docker Compose 配置
├── python/                     # Python 服务代码
│   ├── lancedb/               # LanceDB HTTP 服务
│   └── daft/                  # Daft HTTP 服务
├── monitoring/                 # Prometheus 配置
├── grafana/                    # Grafana 仪表板
├── tests/                      # 测试套件
├── poc/                        # POC 测试
│   ├── scripts/               # POC 脚本
│   ├── data/                  # 测试数据
│   └── results/               # 测试结果
└── docs/                       # 项目文档
```

## 🛠️ 技术栈

- **数据处理**: Daft
- **向量数据库**: LanceDB
- **元数据存储**: PostgreSQL
- **对象存储**: MinIO (S3-compatible)
- **缓存**: Redis
- **监控**: Prometheus + Grafana
- **容器化**: Docker Compose

## 📖 文档

- [Sprint 1 计划](docs/phase1-mvp/sprint1-infrastructure/sprint-plan.md)
- [Week 2 POC 计划](docs/phase1-mvp/sprint1-infrastructure/week2-poc-plan.md)
- [Day 3 成功报告](docs/phase1-mvp/sprint1-infrastructure/DAY3-SUCCESS-REPORT.md)

## 👥 团队

- **Winston** - 架构师/平台运维/后端开发

## 📄 许可证

Copyright © 2026 DIntelliHub Project

---

**状态**: 🟢 Week 1 完成 | Week 2 进行中
**版本**: v0.1.0
**最后更新**: 2026-01-23
