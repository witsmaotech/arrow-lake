# Sprint 5: 混合检索 + 权限控制

**Sprint周期**: Week 9-10
**Sprint目标**: 混合检索（向量+全文+重排序）可用，RBAC权限控制实现
**状态**: 🔴 未开始

---

## 📋 Sprint概述

本Sprint聚焦于高级检索能力和企业级权限控制，为Beta版本提供核心功能。

### 关键成果
- ✅ 全文搜索集成
- ✅ 混合检索API（向量+全文）
- ✅ 结果融合算法（RRF）
- ✅ 重排序集成（Cohere）
- ✅ RBAC权限模型
- ✅ 权限检查实现
- ✅ 审计日志

---

## 🎯 Sprint任务列表

| 任务ID | 任务名称 | 负责人 | 状态 | 优先级 | 工期 | 截止日期 |
|--------|---------|--------|------|--------|------|----------|
| SP5-001 | 全文搜索集成 (LanceDB) | 后端开发 | 🔴 未开始 | P0 | 3天 | Week 9 Day 3 |
| SP5-002 | 混合检索API开发 | 后端开发 | 🔴 未开始 | P0 | 3天 | Week 9 Day 5 |
| SP5-003 | 结果融合算法 (RRF) | 后端开发 | 🔴 未开始 | P0 | 2天 | Week 10 Day 2 |
| SP5-004 | 重排序集成 (Cohere) | 后端开发 | 🔴 未开始 | P1 | 2天 | Week 10 Day 3 |
| SP5-005 | RBAC权限模型设计 | 架构师 | 🔴 未开始 | P0 | 1天 | Week 9 Day 2 |
| SP5-006 | RBAC实现 | 后端开发 | 🔴 未开始 | P0 | 4天 | Week 10 Day 4 |
| SP5-007 | 权限检查优化 (Redis缓存) | 后端开发 | 🔴 未开始 | P1 | 2天 | Week 10 Day 5 |
| SP5-008 | 审计日志实现 | 后端开发 | 🔴 未开始 | P1 | 2天 | Week 10 Day 5 |
| SP5-009 | 单元测试和集成测试 | 测试工程师 | 🔴 未开始 | P0 | 持续 | Week 9-10 |
| SP5-010 | 性能测试 (混合检索) | 测试工程师 | 🔴 未开始 | P1 | 2天 | Week 10 Day 5 |

---

## ✅ Sprint验收标准

### 功能验收
- [ ] 全文搜索功能可用
- [ ] 混合检索API正常工作
- [ ] 结果融合算法准确（RRF或加权融合）
- [ ] 重排序功能可用（可选）
- [ ] RBAC权限控制实现
- [ ] 权限检查性能 < 10ms
- [ ] 审计日志完整记录

### 性能验收
- [ ] 混合检索延迟 < 100ms (P99)
- [ ] 权限检查延迟 < 10ms
- [ ] 重排序延迟 < 50ms

### 质量验收
- [ ] 单元测试覆盖率 > 80%
- [ ] 集成测试通过率 = 100%
- [ ] 权限测试通过（无越权访问）

---

## 📂 Sprint文档

### 设计文档
- [ ] `hybrid-search-design.md` - 混合检索设计
- [ ] `rbac-design.md` - RBAC权限模型设计
- [ ] `permission-model.md` - 权限模型详细设计
- [ ] `result-fusion.md` - 结果融合算法设计

### 开发文档
- [ ] `hybrid-search-api-spec.md` - 混合检索API规范
- [ ] `rbac-implementation-guide.md` - RBAC实施指南
- [ ] `permission-check-optimization.md` - 权限检查优化

### 测试文档
- [ ] `test-plan.md` - 测试计划
- [ ] `permission-test-cases.md` - 权限测试用例
- [ ] `performance-test-report.md` - 性能测试报告

### 用户文档
- [ ] `rbac-user-guide.md` - RBAC用户指南
- [ ] `audit-log-guide.md` - 审计日志查询指南

### 回顾文档
- [ ] `sprint-retrospective.md` - Sprint回顾

---

## 🎯 混合检索详细设计

### 1. 混合检索架构

```
Query: "machine learning algorithms"
  ↓
┌────────────────────────────────┐
│  向量搜索 (Vector Search)       │
│  - 语义相似度匹配                │
│  - 返回Top 20候选               │
└────────────┬───────────────────┘
             │
┌────────────┴───────────────────┐
│  全文搜索 (Full-text Search)    │
│  - 关键词匹配                   │
│  - BM25算法                     │
│  - 返回Top 20候选               │
└────────────┬───────────────────┘
             │
┌────────────┴───────────────────┐
│  结果融合 (Result Fusion)       │
│  - RRF (Reciprocal Rank Fusion) │
│  - 加权融合                     │
│  - 去重                         │
└────────────┬───────────────────┘
             │
┌────────────┴───────────────────┐
│  重排序 (Reranking)             │
│  - Cohere Rerank API            │
│  - 返回Top 5最终结果            │
└────────────────────────────────┘
```

### 2. 混合检索API

**端点**: `POST /v1/query/hybrid`

**API规范**:
```python
POST /v1/query/hybrid
Content-Type: application/json

Request:
{
  "query": "machine learning algorithms",
  "filters": {
    "category": "technology",
    "date": "2024-01-01:2024-12-31"
  },
  "limit": 10,
  "fusion_method": "rrf",  # rrf / weighted
  "rerank": true,
  "rerank_model": "cohere-rerank-v3"
}

Response:
{
  "results": [
    {
      "id": "doc1",
      "text": "...",
      "score": 0.95,
      "vector_score": 0.92,
      "ft_score": 0.85,
      "rerank_score": 0.95
    }
  ],
  "total": 10,
  "search_metadata": {
    "vector_search_time": 35,
    "fulltext_search_time": 25,
    "fusion_time": 5,
    "rerank_time": 40,
    "total_time": 105
  }
}
```

### 3. 结果融合算法

**RRF (Reciprocal Rank Fusion)**:
```python
def rrffusion(vector_results, ft_results, k=60):
    scores = {}

    # 向量搜索结果
    for rank, doc in enumerate(vector_results):
        scores[doc.id] = scores.get(doc.id, 0) + 1 / (k + rank + 1)

    # 全文搜索结果
    for rank, doc in enumerate(ft_results):
        scores[doc.id] = scores.get(doc.id, 0) + 1 / (k + rank + 1)

    # 排序
    sorted_docs = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return sorted_docs
```

**加权融合**:
```python
def weighted_fusion(vector_results, ft_results, alpha=0.7):
    scores = {}

    # 向量搜索权重: alpha
    for doc in vector_results:
        scores[doc.id] = doc.score * alpha

    # 全文搜索权重: 1 - alpha
    for doc in ft_results:
        if doc.id in scores:
            scores[doc.id] += doc.score * (1 - alpha)
        else:
            scores[doc.id] = doc.score * (1 - alpha)

    return sorted(scores.items(), key=lambda x: x[1], reverse=True)
```

---

## 🎯 RBAC权限控制详细设计

### 1. 权限模型

**模型层次**:
```
Metalake (租户)
  └── Catalog (数据源类型)
      └── Schema (数据库/命名空间)
          └── Object (表、Topic、Fileset)
              └── Privilege (权限)
```

**权限类型**:
- **Catalog权限**: CREATE_CATALOG, USE_CATALOG
- **Schema权限**: CREATE_SCHEMA, USE_SCHEMA
- **Object权限**: SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, DROP
- **Column权限**: SELECT (列级权限)

### 2. 角色定义

**预定义角色**:

| 角色 | 描述 | 权限 |
|------|------|------|
| **admin** | 管理员 | 所有权限 |
| **data_admin** | 数据管理员 | 数据管理权限 |
| **data_scientist** | 数据科学家 | SELECT权限 |
| **data_engineer** | 数据工程师 | SELECT + INSERT权限 |
| **viewer** | 查看者 | 只读权限 |

**角色继承**:
```
admin
  ├── data_admin
  │     ├── data_scientist
  │     └── data_engineer
  └── viewer
```

### 3. 权限检查API

**端点**: `GET /v1/permissions/check`

**API规范**:
```python
GET /v1/permissions/check
Content-Type: application/json

Request:
{
  "user": "user1",
  "resource": "catalog_prod.schema_ml.table_documents",
  "privilege": "SELECT"
}

Response:
{
  "allowed": true,
  "reason": "Role data_scientist has SELECT privilege"
}
```

### 4. 权限检查优化

**缓存策略**:
- **L1缓存**: 本地内存缓存（LRU，1分钟TTL）
- **L2缓存**: Redis缓存（5分钟TTL）
- **缓存键**: `{user}:{resource}:{privilege}`

**性能提升**:
- 无缓存: 50ms (数据库查询)
- L1缓存命中: < 1ms
- L2缓存命中: < 5ms

---

## 🎯 审计日志详细设计

### 1. 审计事件

**事件类型**:
- **认证事件**: 登录、登出、认证失败
- **授权事件**: 权限检查、访问拒绝
- **数据事件**: 数据读取、数据修改、数据删除
- **管理事件**: 角色创建、权限修改、用户管理

### 2. 审计日志格式

```json
{
  "event_id": "evt_20260122_123456",
  "timestamp": "2026-01-22T12:34:56Z",
  "event_type": "data.read",
  "user": "user1",
  "resource": "catalog_prod.schema_ml.table_documents",
  "action": "SELECT",
  "result": "success",
  "ip_address": "192.168.1.100",
  "user_agent": "Mozilla/5.0...",
  "metadata": {
    "query": "SELECT * FROM documents LIMIT 10",
    "rows_affected": 10
  }
}
```

### 3. 审计日志查询

**端点**: `GET /v1/audit/logs`

**查询参数**:
- `user`: 用户名
- `event_type`: 事件类型
- `start_time`: 开始时间
- `end_time`: 结束时间
- `resource`: 资源
- `limit`: 返回数量

---

## 🚨 Sprint风险

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|----------|
| **重排序API成本高** | 🟡 中 | 高 | 缓存重排序结果，本地备选 |
| **权限检查性能不达标** | 🟡 中 | 中 | Redis缓存，批量检查 |
| **混合检索准确率低** | 🟡 中 | 低 | 调优融合权重，A/B测试 |
| **审计日志存储成本** | 🟢 低 | 中 | 定期归档，压缩存储 |

---

## 📊 关键指标

### 检索性能
| 指标 | 目标值 | 备注 |
|------|--------|------|
| **混合检索延迟 (P99)** | < 100ms | 包含重排序 |
| **向量搜索延迟** | < 50ms | 仅向量搜索 |
| **全文搜索延迟** | < 30ms | BM25算法 |
| **重排序延迟** | < 50ms | Cohere API |

### 权限性能
| 指标 | 目标值 | 备注 |
|------|--------|------|
| **权限检查延迟** | < 10ms | 缓存命中 |
| **权限检查延迟** | < 50ms | 缓存未命中 |

---

## 👥 Sprint团队

| 角色 | 姓名 | 职责 |
|------|------|------|
| **后端开发** | [待填写] | 混合检索、RBAC |
| **测试工程师** | [待填写] | 权限测试、性能测试 |
| **架构师** | Winston | RBAC设计指导 |
| **Scrum Master** | [待填写] | Sprint协调 |

---

## 📅 Sprint时间线

```
Week 9:
  Day 1-2: RBAC权限模型设计
  Day 1-3: 全文搜索集成
  Day 3-5: 混合检索API开发

Week 10:
  Day 1-2: 结果融合算法
  Day 2-3: 重排序集成
  Day 2-4: RBAC实现
  Day 4-5: 权限检查优化 + 审计日志
  Day 5:   性能测试
  持续:    单元测试 + 集成测试
```

---

## 🎯 关键决策点

**Week 9 Day 5**: 融合方法选择
- ✅ RRF (简单，无参数)
- 🔄 加权融合 (可调优)

**Week 10 Day 3**: 重排序是否必需
- ✅ 是（准确率优先）
- ❌ 否（性能优先）

---

## 📝 技术选型

### 混合检索
- **向量搜索**: LanceDB
- **全文搜索**: LanceDB FTS (基于Tantivy)
- **重排序**: Cohere Rerank API

### 权限控制
- **权限模型**: Gravitino RBAC
- **缓存**: Redis
- **审计日志**: PostgreSQL + Elasticsearch

---

## 🔗 相关资源

- **任务跟踪**: `../../PROJECT-TASK-TRACKER.md`
- **架构文档**: `../../ARCH.md`
- **Gravitino RBAC**: https://gravitino.apache.org/docs/1.1.0/security/rbac/
- **Cohere Rerank**: https://docs.cohere.com/reference/rerank

---

## 📧 联系方式

**Sprint负责人**: [待填写]
**技术支持**: Winston

---

**Sprint开始日期**: [待定]
**Sprint结束日期**: [待定]
**最后更新**: 2026-01-22
