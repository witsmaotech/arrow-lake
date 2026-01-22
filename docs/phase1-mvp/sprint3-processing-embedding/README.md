# Sprint 3: 数据处理 + 向量化

**Sprint周期**: Week 5-6
**Sprint目标**: Daft ETL管道可用，AI函数集成，嵌入生成服务正常
**状态**: 🔴 未开始

---

## 📋 Sprint概述

本Sprint聚焦于核心数据处理能力和AI函数集成，为向量检索提供嵌入向量。

### 关键成果
- ✅ Daft数据处理管道
- ✅ ETL转换逻辑
- ✅ AI函数集成（嵌入生成）
- ✅ OpenAI API集成
- ✅ 本地模型备选方案
- ✅ 批处理优化

---

## 🎯 Sprint任务列表

| 任务ID | 任务名称 | 负责人 | 状态 | 优先级 | 工期 | 截止日期 |
|--------|---------|--------|------|--------|------|----------|
| SP3-001 | Daft数据处理管道开发 | 后端开发 | 🔴 未开始 | P0 | 4天 | Week 5 Day 4 |
| SP3-002 | ETL转换逻辑实现 | 后端开发 | 🔴 未开始 | P0 | 3天 | Week 5 Day 5 |
| SP3-003 | AI函数集成框架 | 后端开发 | 🔴 未开始 | P0 | 3天 | Week 6 Day 2 |
| SP3-004 | OpenAI API集成 | 后端开发 | 🔴 未开始 | P0 | 2天 | Week 6 Day 3 |
| SP3-005 | 本地模型备选方案 (sentence-transformers) | 后端开发 | 🔴 未开始 | P1 | 3天 | Week 6 Day 4 |
| SP3-006 | 批处理优化 | 后端开发 | 🔴 未开始 | P1 | 2天 | Week 6 Day 5 |
| SP3-007 | 错误处理和重试机制 | 后端开发 | 🔴 未开始 | P0 | 2天 | Week 6 Day 5 |
| SP3-008 | 嵌入向量API开发 | 后端开发 | 🔴 未开始 | P0 | 2天 | Week 6 Day 5 |
| SP3-009 | 单元测试和集成测试 | 测试工程师 | 🔴 未开始 | P0 | 持续 | Week 5-6 |
| SP3-010 | 性能基准测试 (嵌入生成) | 测试工程师 | 🔴 未开始 | P1 | 2天 | Week 6 Day 5 |

---

## ✅ Sprint验收标准

### 功能验收
- [ ] Daft ETL管道可正常运行
- [ ] 支持常见数据转换操作（过滤、映射、聚合）
- [ ] AI函数集成成功（嵌入生成）
- [ ] OpenAI API和本地模型均可使用
- [ ] 批处理优化有效，API调用次数减少
- [ ] 错误处理机制完善，失败可重试
- [ ] 嵌入向量API返回正确结果

### 性能验收
- [ ] 单条文本嵌入生成 < 1秒（OpenAI API）
- [ ] 批量嵌入生成（100条）< 10秒
- [ ] 本地模型嵌入生成 < 500ms/条
- [ ] ETL处理吞吐 > 10K rows/s

### 质量验收
- [ ] 单元测试覆盖率 > 80%
- [ ] 集成测试通过率 = 100%
- [ ] 性能基准测试通过

---

## 📂 Sprint文档

### 设计文档
- [ ] `daft-pipeline-design.md` - Daft管道设计
- [ ] `etl-workflows.md` - ETL工作流设计
- [ ] `ai-functions-integration.md` - AI函数集成设计
- [ ] `embedding-service.md` - 嵌入服务设计

### 开发文档
- [ ] `daft-operators-guide.md` - Daft操作符指南
- [ ] `etl-transformation-reference.md` - ETL转换参考
- [ ] `embedding-api-spec.md` - 嵌入API规范

### 测试文档
- [ ] `test-plan.md` - 测试计划
- [ ] `performance-benchmark.md` - 性能基准测试
- [ ] `test-report.md` - 测试报告

### 回顾文档
- [ ] `sprint-retrospective.md` - Sprint回顾

---

## 🎯 Daft数据处理管道详细设计

### 1. Daft管道架构

```python
# Daft ETL管道示例
import daft

# 1. 读取数据
df = daft.read_csv("s3://datalake/raw/documents.csv")

# 2. 数据验证和转换
df = df.filter(df["text"].str.length() > 50)
df = df.with_column("cleaned_text", df["text"].str.clean_html())

# 3. AI函数集成 - 嵌入生成
df = df.with_column(
    "embedding",
    df["cleaned_text"].embed.openai(
        model="text-embedding-3-small"
    )
)

# 4. 数据质量检查
df = df.filter(df["embedding"].is_not_null())

# 5. 写入目标存储
df.write_lance("s3://datalake/processed/documents.lance")
```

### 2. 支持的ETL操作

**数据读取**:
- [ ] `read_csv()` - CSV文件
- [ ] `read_json()` - JSON文件
- [ ] `read_parquet()` - Parquet文件
- [ ] `read_lance()` - Lance格式

**数据转换**:
- [ ] `filter()` - 数据过滤
- [ ] `select()` / `with_column()` - 列选择和新增
- [ ] `drop_nulls()` - 删除空值
- [ ] `sort()` - 排序
- [ ] `group_by()` + `agg()` - 聚合

**AI函数**:
- [ ] `embed.openai()` - OpenAI嵌入
- [ ] `embed.cohere()` - Cohere嵌入
- [ ] `embed.huggingface()` - HuggingFace嵌入
- [ ] `llm.openai()` - LLM调用

**数据写入**:
- [ ] `write_csv()` - 写入CSV
- [ ] `write_parquet()` - 写入Parquet
- [ ] `write_lance()` - 写入Lance

### 3. 性能优化

**懒执行优化**:
- Daft自动优化查询计划
- 谓词下推（Predicate Pushdown）
- 投影下推（Projection Pushdown）

**分布式执行**:
- Ray集群自动并行
- 数据分片处理
- 资源自动调度

---

## 🎯 AI函数集成详细设计

### 1. 嵌入生成框架

**支持模型**:
- OpenAI: `text-embedding-3-small`, `text-embedding-3-large`
- Cohere: `embed-english-v3.0`, `embed-multilingual-v3.0`
- HuggingFace: sentence-transformers系列

**API设计**:

```python
POST /v1/embeddings
Content-Type: application/json

Request:
{
  "texts": ["text1", "text2", ...],
  "model": "openai/text-embedding-3-small",
  "batch_size": 100
}

Response:
{
  "embeddings": [[0.1, 0.2, ...], [0.3, 0.4, ...]],
  "model": "openai/text-embedding-3-small",
  "usage": {
    "total_tokens": 1000,
    "api_calls": 10
  }
}
```

### 2. 批处理优化

**优化策略**:
1. **批量合并**: 将多个小批次合并为大批次
2. **并发限制**: 控制并发API调用数（避免限流）
3. **缓存**: 缓存已生成的嵌入向量
4. **本地备选**: API失败时切换到本地模型

**性能提升**:
- 批量大小: 1 → 100（API调用减少99%）
- 成本降低: ~90%
- 速度提升: ~5倍

### 3. 错误处理和重试

**错误类型**:
- API限流 (429)
- API超时
- API错误 (500)
- 网络错误

**重试策略**:
- 指数退避: 1s → 2s → 4s → 8s
- 最大重试次数: 3次
- 降级策略: API → 本地模型

**死信队列**:
- 失败记录写入DLQ
- 定期重新处理
- 手动干预接口

---

## 🎯 本地模型备选方案

### 模型选择

**推荐模型**:
- `all-MiniLM-L6-v2` - 快速，质量中等
- `all-mpnet-base-v2` - 平衡性能和质量
- `gte-large` - 高质量，较慢

**性能对比**:

| 模型 | 维度 | 速度 | 质量 | 显存 |
|------|------|------|------|------|
| **all-MiniLM-L6-v2** | 384 | 快 (500ms) | 中 | 1GB |
| **all-mpnet-base-v2** | 768 | 中 (1s) | 高 | 2GB |
| **gte-large** | 1024 | 慢 (2s) | 很高 | 4GB |

**部署方案**:
- Docker容器化部署
- GPU加速（可选）
- REST API封装

---

## 🚨 Sprint风险

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|----------|
| **Daft API使用复杂，调试困难** | 🔴 高 | 中 | 提前技术验证，参考官方示例 |
| **AI函数调用性能不达标** | 🔴 高 | 中 | 批处理优化，本地模型备选 |
| **第三方API成本超预算** | 🟡 中 | 高 | 批处理优化，成本监控 |
| **本地模型性能不如API** | 🟢 低 | 低 | 作为备选方案，可接受 |

---

## 📊 关键指标

### ETL性能
| 指标 | 目标值 | 备注 |
|------|--------|------|
| **数据处理吞吐** | > 10K rows/s | 简单转换 |
| **复杂数据处理** | > 1K rows/s | 含AI函数 |
| **内存使用** | < 16GB | 单节点 |

### 嵌入生成性能
| 指标 | OpenAI API | 本地模型 |
|------|-----------|----------|
| **单条延迟** | < 1s | < 500ms |
| **批量吞吐** | > 100条/10s | > 50条/10s |
| **成本** | $0.00002/1K tokens | $0 (GPU成本) |

---

## 👥 Sprint团队

| 角色 | 姓名 | 职责 |
|------|------|------|
| **后端开发** | [待填写] | Daft管道、AI函数 |
| **测试工程师** | [待填写] | 单元测试、性能测试 |
| **架构师** | Winston | Daft技术指导 |
| **Scrum Master** | [待填写] | Sprint协调 |

---

## 📅 Sprint时间线

```
Week 5:
  Day 1-4: Daft数据处理管道开发
  Day 4-5: ETL转换逻辑实现

Week 6:
  Day 1-2: AI函数集成框架
  Day 2-3: OpenAI API集成
  Day 3-4: 本地模型备选方案
  Day 4-5: 批处理优化 + 错误处理
  Day 5:   嵌入向量API + 性能测试
  持续:    单元测试 + 集成测试
```

---

## 🎯 关键决策点

**Week 5 Day 4**: Daft POC验证结果
- ✅ Daft验证通过 → 继续使用Daft
- ❌ Daft验证失败 → 启动Spark备选方案

**Week 6 Day 3**: 本地模型是否必需
- ✅ 是（成本控制）→ 开发本地模型
- ❌ 否（优先性能）→ 仅使用API

---

## 📝 技术选型

### 数据处理
- **框架**: Daft >= 0.3.0
- **分布式**: Ray >= 2.8
- **备选**: PySpark（如果Daft不行）

### 嵌入生成
- **API**: OpenAI / Cohere
- **本地**: sentence-transformers
- **部署**: Docker + GPU（可选）

### 错误处理
- **重试**: tenacity库
- **死信队列**: Redis / RabbitMQ

---

## 🔗 相关资源

- **任务跟踪**: `../../PROJECT-TASK-TRACKER.md`
- **架构文档**: `../../ARCH.md`
- **Daft文档**: https://docs.daft.ai/en/stable/
- **OpenAI Embeddings**: https://platform.openai.com/docs/guides/embeddings
- **Sentence Transformers**: https://www.sbert.net/

---

## 📧 联系方式

**Sprint负责人**: [待填写]
**技术支持**: Winston

---

**Sprint开始日期**: [待定]
**Sprint结束日期**: [待定]
**最后更新**: 2026-01-22
