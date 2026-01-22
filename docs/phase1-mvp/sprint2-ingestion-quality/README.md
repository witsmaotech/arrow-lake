# Sprint 2: 数据摄取 + 质量处理

**Sprint周期**: Week 3-4
**Sprint目标**: 文件上传和S3数据摄取可用，DataJuicer质量处理集成
**状态**: 🔴 未开始

---

## 📋 Sprint概述

本Sprint聚焦于数据摄取和质量处理核心能力，为后续数据处理提供高质量数据源。

### 关键成果
- ✅ 文件上传API（支持多种格式）
- ✅ S3摄取服务
- ✅ DataJuicer集成
- ✅ 质量算子实现（去重+过滤+清洗）
- ✅ 质量报告API

---

## 🎯 Sprint任务列表

| 任务ID | 任务名称 | 负责人 | 状态 | 优先级 | 工期 | 截止日期 |
|--------|---------|--------|------|--------|------|----------|
| SP2-001 | 文件上传API开发 | 后端开发 | 🔴 未开始 | P0 | 3天 | Week 3 Day 3 |
| SP2-002 | S3摄取服务开发 | 后端开发 | 🔴 未开始 | P0 | 3天 | Week 3 Day 5 |
| SP2-003 | 数据库连接器开发 (MySQL/PostgreSQL) | 后端开发 | 🔴 未开始 | P1 | 2天 | Week 4 Day 2 |
| SP2-004 | DataJuicer集成 | 后端开发 | 🔴 未开始 | P0 | 3天 | Week 4 Day 3 |
| SP2-005 | 质量算子实现 (去重+过滤+清洗) | 后端开发 | 🔴 未开始 | P0 | 3天 | Week 4 Day 4 |
| SP2-006 | 质量报告API开发 | 后端开发 | 🔴 未开始 | P1 | 2天 | Week 4 Day 5 |
| SP2-007 | 文件上传前端界面 | 前端开发 | 🔴 未开始 | P1 | 3天 | Week 4 Day 3 |
| SP2-008 | 单元测试编写 | 测试工程师 | 🔴 未开始 | P0 | 持续 | Week 3-4 |
| SP2-009 | 集成测试编写 | 测试工程师 | 🔴 未开始 | P1 | 持续 | Week 3-4 |
| SP2-010 | API文档编写 | 后端开发 | 🔴 未开始 | P2 | 1天 | Week 4 Day 5 |

---

## ✅ Sprint验收标准

### 功能验收
- [ ] 支持文件上传（CSV/JSON/PDF/Word/Markdown/TXT/JPG/PNG/MP3/MP4）
- [ ] S3摄取功能正常，支持批量摄取
- [ ] DataJuicer质量处理可用
- [ ] 质量算子可用（去重→过滤→清洗）
- [ ] 质量报告API返回准确统计
- [ ] 文件上传前端界面可用

### 质量验收
- [ ] 单元测试覆盖率 > 80%
- [ ] 集成测试通过率 = 100%
- [ ] API文档完整
- [ ] 大文件上传（>1GB）性能可接受

---

## 📂 Sprint文档

### 设计文档
- [ ] `ingestion-api-design.md` - 摄取API设计
- [ ] `file-upload-spec.md` - 文件上传规范
- [ ] `datajuicer-integration.md` - DataJuicer集成设计
- [ ] `quality-pipeline.md` - 质量处理流水线设计

### 开发文档
- [ ] `ingestion-api-spec.md` - 摄取API规范
- [ ] `quality-operators.md` - 质量算子说明
- [ ] `data-formats.md` - 支持的数据格式

### 测试文档
- [ ] `test-plan.md` - 测试计划
- [ ] `test-report.md` - 测试报告
- [ ] `performance-test.md` - 性能测试（大文件上传）

### 用户文档
- [ ] `api-documentation.md` - API文档
- [ ] `user-guide-upload.md` - 文件上传用户指南

### 回顾文档
- [ ] `sprint-retrospective.md` - Sprint回顾

---

## 🎯 数据摄取功能详细设计

### 1. 文件上传API

**端点**: `POST /v1/ingest/file`

**支持格式**:
- 文本: CSV, JSON, TXT, Markdown, PDF, Word
- 图像: JPG, PNG
- 音频: MP3
- 视频: MP4

**功能特性**:
- [ ] 拖拽上传
- [ ] 批量上传
- [ ] 断点续传
- [ ] 大文件分片上传（>100MB）
- [ ] 文件类型验证
- [ ] 文件大小限制（可配置）

**API规范**:
```python
POST /v1/ingest/file
Content-Type: multipart/form-data

Request:
  - file: binary (文件内容)
  - metadata: JSON (元数据)
  - dataset_id: string (目标数据集ID)

Response:
  - ingest_id: string (摄取ID)
  - status: string (pending/processing/completed/failed)
  - file_info: object (文件信息)
```

### 2. S3摄取服务

**功能特性**:
- [ ] S3桶列表和浏览
- [ ] 批量摄取S3对象
- [ ] 增量摄取（基于前缀/时间戳）
- [ ] S3事件触发摄取（S3 Event Bridge）

**API规范**:
```python
POST /v1/ingest/s3
Content-Type: application/json

Request:
  - bucket: string (S3桶名)
  - prefix: string (对象前缀)
  - pattern: string (文件名模式，如*.csv)
  - batch_size: integer (批量大小)

Response:
  - ingest_id: string (摄取ID)
  - estimated_files: integer (预估文件数)
  - status: string
```

### 3. 数据库摄取

**支持数据库**:
- [ ] MySQL
- [ ] PostgreSQL
- [ ] MongoDB (P2)

**功能特性**:
- [ ] 基于时间戳的增量同步
- [ ] 基于自增ID的增量同步
- [ ] 全量同步
- [ ] SQL查询定制

**API规范**:
```python
POST /v1/ingest/database
Content-Type: application/json

Request:
  - connection: object (数据库连接信息)
  - query: string (SQL查询或表名)
  - mode: string (full/incremental)
  - watermark_column: string (水印列)

Response:
  - ingest_id: string
  - estimated_rows: integer
```

---

## 🎯 数据质量处理详细设计

### 1. DataJuicer集成

**推荐处理流程**:
```
Raw Data
  ↓
精确去重 (MD5 Hash)
  ↓
快速过滤 (长度、大小、格式)
  ↓
数据清洗 (HTML、邮箱、链接)
  ↓
语言过滤
  ↓
质量过滤 (LLM评分，可选)
  ↓
模糊去重 (MinHash LSH)
  ↓
Clean Data
```

**质量算子列表**:

**去重算子**:
- `document_md5_deduplicator` - MD5哈希去重
- `document_minhash_deduplicator` - MinHash LSH模糊去重

**过滤算子**:
- `text_length_filter` - 文本长度过滤
- `file_size_filter` - 文件大小过滤
- `language_filter` - 语言识别过滤

**清洗算子**:
- `clean_html_mapper` - HTML标签清理
- `clean_email_mapper` - 邮箱地址清理
- `clean_url_mapper` - URL链接清理

### 2. 质量报告API

**端点**: `GET /v1/quality/report/{dataset_id}`

**报告内容**:
```json
{
  "dataset_id": "ds_12345",
  "total_records": 1000000,
  "quality_stats": {
    "duplicates_removed": 50000,
    "filtered_by_length": 30000,
    "filtered_by_language": 20000,
    "cleaned_html": 15000,
    "llm_score_avg": 0.85
  },
  "final_records": 885000,
  "quality_score": 0.88
}
```

---

## 🚨 Sprint风险

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|----------|
| **DataJuicer集成复杂度超预期** | 🔴 高 | 中 | 提前技术验证，参考官方示例 |
| **大文件上传性能问题** | 🟡 中 | 中 | 分片上传、断点续传 |
| **质量算子调优耗时** | 🟢 低 | 低 | 使用推荐配置，渐进优化 |
| **文件格式兼容性问题** | 🟡 中 | 低 | 支持主流格式，其他转码 |

---

## 📊 关键指标

### 摄取性能
| 指标 | 目标值 | 备注 |
|------|--------|------|
| **小文件上传** | < 10秒 | < 10MB |
| **大文件上传** | < 5分钟 | > 100MB，支持断点续传 |
| **批量摄取吞吐** | > 1GB/s | S3批量摄取 |
| **数据库摄取** | > 10K rows/s | 增量同步 |

### 质量处理性能
| 指标 | 目标值 | 备注 |
|------|--------|------|
| **去重速度** | > 100K docs/s | MD5哈希去重 |
| **过滤速度** | > 50K docs/s | 复杂过滤 |
| **清洗速度** | > 30K docs/s | HTML清洗 |
| **质量评分** | > 1K docs/s | LLM评分 |

---

## 👥 Sprint团队

| 角色 | 姓名 | 职责 |
|------|------|------|
| **后端开发** | [待填写] | 摄取API、质量处理 |
| **前端开发** | [待填写] | 文件上传界面 |
| **测试工程师** | [待填写] | 单元测试、集成测试 |
| **架构师** | Winston | DataJuicer集成指导 |
| **Scrum Master** | [待填写] | Sprint协调 |

---

## 📅 Sprint时间线

```
Week 3:
  Day 1-3: 文件上传API开发
  Day 3-5: S3摄取服务开发

Week 4:
  Day 1-2: 数据库连接器开发
  Day 2-3: DataJuicer集成
  Day 3-4: 质量算子实现
  Day 4-5: 质量报告API + 前端界面
  持续:    单元测试 + 集成测试
```

---

## 🎯 关键决策点

**Week 3 Day 3**: 文件上传方式确认
- ✅ 使用分片上传（支持大文件）
- ❌ 使用简单上传（快速实现）

**Week 4 Day 3**: DataJuicer集成深度
- ✅ 完整集成所有算子
- 🔄 MVP阶段集成核心算子（去重+过滤+清洗）

---

## 📝 技术选型

### 文件上传
- **前端**: Dropzone.js / React-Dropzone
- **后端**: FastAPI UploadFile
- **存储**: 先存储到临时目录，再异步处理

### 数据摄取
- **S3 SDK**: boto3
- **数据库**: SQLAlchemy (MySQL/PostgreSQL), PyMongo (MongoDB)

### 数据质量
- **框架**: DataJuicer >= 0.2.0
- **分布式**: Ray (可选)

---

## 🔗 相关资源

- **任务跟踪**: `../../PROJECT-TASK-TRACKER.md`
- **架构文档**: `../../ARCH.md`
- **DataJuicer文档**: https://datajuicer.github.io/data-juicer/
- **FastAPI Upload教程**: https://fastapi.tiangolo.com/tutorial/request-files/

---

## 📧 联系方式

**Sprint负责人**: [待填写]
**技术支持**: Winston

---

**Sprint开始日期**: [待定]
**Sprint结束日期**: [待定]
**最后更新**: 2026-01-22
