# 数据质量与去重

> 使用 Arrow Lake 的质量过滤和内容去重管线，确保入库数据的完整性和唯一性。

***

## 1. 质量过滤器

`quality_filter()` 对数据集运行所有已注册的质量过滤器，返回一份聚合报告。

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data")

# 运行全部已注册过滤器 (AND 模式)
report = lake.quality_filter("articles", mode="all")
print(f"通过：{report.passed}, 拒绝：{report.rejected}")

# 输出示例：
# 通过：9420, 拒绝：580

# 查看总体通过率
rate = report.overall_pass_rate()       # 94.2
print(f"通过率：{rate:.1f}%")

# 查看每个过滤器的明细
for detail in report.per_filter_breakdown():
    print(f"  {detail['filter_name']}: "
          f"通过 {detail['passed_count']}, "
          f"拒绝 {detail['rejected_count']}")

# 导出为 JSON (兼容 Metaflow Cards)
payload = report.to_json()
# {"total_rows": 10000, "passed_rows": 9420, ...}
```

### 参数说明

| 参数               | 类型    | 默认值     | 说明                                      |
| ---------------- | ----- | ------- | --------------------------------------- |
| `dataset_name`   | `str` | (必填)    | 数据集名称                                   |
| `active_filters` | `str` | 配置中的值   | 逗号分隔的过滤器名称，空字符串使用全部                     |
| `mode`           | `str` | `"all"` | `"all"` = AND (所有过滤器必须通过), `"any"` = OR |

`QualityReport` 是一个 `frozen dataclass`，包含以下字段：

* `total`: 输入总行数
* `passed`: 通过所有过滤器的行数
* `rejected`: 被至少一个过滤器拒绝的行数
* `filter_results`: 每个过滤器的 `FilterResult` 元组
* `schema_rejected`: 被 schema 验证拒绝的行数
* `duration_seconds`: 质量过滤耗时 (秒)

***

## 2. 内置过滤器

Arrow Lake 默认注册两个内置过滤器，可通过 `configs/dev.yaml` 的 `quality` 段调整阈值：

### TextLengthFilter

过滤文本长度不满足要求的行。要求数据集包含 `text_content` 列。

```yaml
# configs/dev.yaml
quality:
  text_min_chars: 1           # 最少字符数
  text_max_chars: null        # 最大字符数 (null = 不限制)
```

```python
# 指定仅运行文本长度过滤器
report = lake.quality_filter(
    "articles",
    active_filters="text_length",
    mode="all",
)
```

### ImageResolutionFilter

过滤图片分辨率低于阈值的行。要求列名为 `image_data`，且值为可解码的字节内容。

```yaml
# configs/dev.yaml
quality:
  image_min_width: 64         # 最小宽度 (像素)
  image_min_height: 64        # 最小高度 (像素)
```

```python
# 仅运行图片分辨率过滤器
report = lake.quality_filter(
    "photos",
    active_filters="image_resolution",
    mode="all",
)
```

过滤器运行前会先进行 schema 验证：`lenient` (默认，自动丢弃未知列) 或 `strict` (拒绝未知列和类型不匹配)。

***

## 3. 精确去重 (SHA-256)

`deduplicate()` 默认使用 SHA-256 哈希进行精确去重，适用于文本和二进制数据。

```python
# 精确去重：移除完全相同的行
result = lake.deduplicate("articles", strategy="exact", action="remove")
print(f"唯一：{result.unique_rows}, 重复：{result.duplicates_found}")
print(f"总输入：{result.total_rows}")
print(f"策略：{result.strategy}, 动作：{result.action}")

# 输出示例：
# 唯一：8750, 重复：1250
# 总输入：10000
# 策略：exact, 动作：remove
```

### action 参数

| 值          | 说明                              |
| ---------- | ------------------------------- |
| `"flag"`   | 保留所有行，新增 `is_duplicate` 布尔列标记重复 |
| `"remove"` | 直接从结果表中移除重复行，返回去重后的表            |

`DedupResult` 是一个 `frozen dataclass`，包含 `table` 字段可直接访问结果：

```python
# flag 模式：查看标记
result = lake.deduplicate("articles", strategy="exact", action="flag")
dup_table = result.table
print(dup_table.column("is_duplicate").to_pylist())
# [False, False, True, False, True, ...]
```

***

## 4. 感知去重 (pHash)

对于图片数据集，使用感知哈希 (pHash) 检测视觉相似但非完全相同的图片。

```python
# 感知去重：基于 pHash Hamming 距离
result = lake.deduplicate(
    "photos",
    strategy="perceptual",
    perceptual_threshold=10,
    action="remove",
)

# 同时使用精确 + 感知
result = lake.deduplicate(
    "photos",
    strategy="both",
    perceptual_threshold=10,
    action="flag",
)
```

### pHash 参数

* `strategy`: `"exact"` / `"perceptual"` / `"both"`
* `perceptual_threshold`: Hamming 距离阈值。值越小越严格：
  * `0`: 完全相同的图片
  * `5`: 轻微裁剪 / 压缩变体
  * `10`: (默认) 明显缩放 / 水印 / 颜色偏移
  * `20`: 宽松模式，允许较大视觉差异

底层使用 `imagehash` 库计算 `phash`，然后比较 Hamming 距离。

> **依赖说明**：感知去重需要 `imagehash` 库，通过 `pip install arrow-lake[dedup]` 安装。

***

## 5. NeMo Curator 集成 (GPU 加速 MinHash LSH)

对于大规模文本去重，Arrow Lake 支持通过 NeMo Curator 进行 GPU 加速的 MinHash LSH 近似去重。

> **依赖说明**：NeMo Curator 集成需要 `nemo-curator` 库，通过 `pip install arrow-lake[nemo-curator]` 安装。

```python
from arrow_lake.quality.nemo_curator import NeMoDeduplicator

deduper = NeMoDeduplicator(
    ngram_size=5,          # 每个 n-gram 的字符数
    num_hashes=128,        # MinHash 哈希函数数量
    threshold=0.8,         # Jaccard 相似度阈值
    text_column="text_content",
)

# GPU 可用时自动使用 MinHash LSH，否则回退到 SHA-256 精确去重
unique_table, dup_table = deduper.deduplicate(table)
print(f"GPU 加速：{deduper.using_gpu}")

# NeMo Curator 质量评分
from arrow_lake.quality.nemo_curator import NeMoCuratorFilter

scorer = NeMoCuratorFilter(
    classifiers=("quality",),         # 启用的分类器
    threshold=0.5,                    # 质量阈值
    batch_size=64,
)
passed_table, rejected_table = scorer.filter(table)
```

启用 GPU 去重需要在 YAML 配置中设置：

```yaml
# configs/dev.yaml
quality:
  nemo_curator_enabled: true
  nemo_curator_model: "nemo/quality-scorer"
  nemo_curator_threshold: 0.5
  nemo_curator_batch_size: 64
```

Docker 部署时使用 GPU overlay:

```bash
docker compose -f deploy/docker-compose.yml \
              -f deploy/docker-compose.gpu.yml up -d
```

***

## 6. 死信队列

质量过滤和 schema 验证中被拒绝的行会进入死信队列 (Dead Letter Queue)，支持重试、解决和清理。

```python
from arrow_lake.ingest.dead_letter import IngestDeadLetterQueue

dlq = IngestDeadLetterQueue(base_dir="./data")

# 查看队列统计
print(dlq.stats)
# {"pending": 12, "resolved": 3, "permanent": 1, "total": 16}

# 列出待处理的失败项
items = dlq.list_items(status="pending")
for item in items:
    print(f"{item.file_path}: {item.last_error}")

# 重试一条记录 (attempt_count 自增)
success = dlq.retry("s3://raw/broken_doc.pdf")
# True if the item exists and can_retry is True

# 手动标记为已解决
dlq.resolve("s3://raw/broken_doc.pdf")

# 标记为永久失败 (不再重试)
dlq.mark_permanent("s3://raw/corrupt.bin", reason="文件头损坏，无法修复")

# 按数据集过滤
dataset_items = dlq.list_items(dataset="articles")

# 清理已解决和永久失败的记录
removed = dlq.purge(resolved=True, permanent=True)
print(f"已清理 {removed} 条记录")
```

状态流转：`pending` -> `retrying` -> `pending` (失败) / `resolved` (已修复); 也可直接 `pending` -> `permanent`。

每个 `DeadLetterItem` 包含 `file_path`、`error`、`dataset`、`attempt_count`、`status`、时间戳等字段。

***

## 7. 质量配置参考

完整的质量过滤和去重配置 (对应 `QualityConfig`):

```yaml
quality:
  enabled: true
  filter_mode: all                    # all = AND, any = OR
  active_filters: ""                  # 空 = 使用全部已注册过滤器
  schema_validation: lenient          # lenient | strict

  # 内置过滤器阈值
  text_min_chars: 1
  text_max_chars: null
  image_min_width: 64
  image_min_height: 64

  # NeMo Curator GPU 质量评分
  nemo_curator_enabled: false
  nemo_curator_model: "nemo/quality-scorer"
  nemo_curator_threshold: 0.5
  nemo_curator_batch_size: 64

  # 内容去重
  dedup_enabled: false                # 默认 false（config/media.py:124）——去重需显式开启
  dedup_strategy: exact               # exact | perceptual | both（YAML 不接受 minhash，见 §5）
  dedup_action: flag                  # flag | remove
  dedup_perceptual_threshold: 10

  # 死信队列
  dead_letter_enabled: true
```

***

## 8. 最佳实践

### 何时用哪种去重策略

| 场景              | 推荐策略                           | 说明                        |
| --------------- | ------------------------------ | ------------------------- |
| 文章 / 新闻 / 代码    | `exact`                        | 相同内容产生相同 SHA-256，精确高效     |
| 产品图片去重          | `perceptual` (threshold 5-10)  | 过滤不同尺寸/压缩率的同一商品图          |
| 社交媒体图片          | `perceptual` (threshold 10-15) | 允许滤镜/裁剪差异                 |
| 中等文本近重复（改写/少量编辑） | `minhash`（CPU，编程式，见 §12）| 检测 Jaccard 相似度近重复，无需 GPU |
| 大规模文本语料 (>1M 行) | `NeMoDeduplicator` (GPU)       | MinHash LSH 近似去重，速度远超精确匹配 |
| 混合数据集 (文本 + 图片)   | `both`                         | 先精确去重再感知去重，两阶段流水线         |

### 典型数据质量管线

```python
from arrow_lake import Lake

lake = Lake.from_yaml("configs/dev.yaml")

# 第一步：质量过滤
report = lake.quality_filter("articles")
print(f"质量通过率：{report.overall_pass_rate():.1f}%")

# 第二步：去重 (flag 模式，先审查不删除)
result = lake.deduplicate("articles", strategy="exact", action="flag")
print(f"发现 {result.duplicates_found} 条重复")

# 第三步：审查后确认删除
if result.duplicates_found > 0:
    confirmed = lake.deduplicate("articles", strategy="exact", action="remove")
    print(f"去重完成，保留 {confirmed.unique_rows} 条")

# 第四步：检查死信队列
from arrow_lake.ingest.dead_letter import IngestDeadLetterQueue
dlq = IngestDeadLetterQueue()
print(f"失败项：{dlq.stats}")
```

### 性能提示

* 质量过滤和去重对数据集进行全表扫描，建议在非高峰期运行
* 对于大于 100 万行的数据集，启用 NeMo Curator GPU 加速
* `perceptual` 策略需要解码图片字节计算 pHash，CPU 消耗较大
* 设置 `text_max_chars` 可提前过滤超长文本，减少下游处理负担

---

## 9. 质量规则引擎 (v1.4.0)

声明式 `QualityRuleEngine` 用可配置的规则替代硬编码过滤器，支持从 JSON、YAML 或 REST API 加载规则。

### 9.1 编程式用法

```python
from arrow_lake.quality.rules import QualityRuleEngine, RuleDefinition
import pyarrow as pa

# 创建包含混合质量数据的表
table = pa.table({
    "text_content": ["good article", "hi", "another good one", "x"],
    "score": [0.9, 0.1, 0.85, 0.05],
})

# 配置规则
engine = QualityRuleEngine()
engine.add_rule(RuleDefinition(
    name="reject_short_text",
    column="text_content",
    check="length",
    params={"min": 3},
    action="reject",
    message="Text too short (min={min} chars)",
))
engine.add_rule(RuleDefinition(
    name="flag_low_score",
    column="score",
    check="range",
    params={"min": 0.5},
    action="flag",
))
engine.add_rule(RuleDefinition(
    name="dedup_content",
    column="text_content",
    check="duplicate",
    action="remove",
))

# 评估但不修改数据
results = engine.evaluate(table)
for r in results:
    print(f"{r.rule_name}: {r.affected_count} rows ({r.action}) — {r.message}")

# 应用：移除 reject/remove 行，保留 flag 行
filtered, results = engine.apply(table)
print(f"Original: {table.num_rows} rows → Filtered: {filtered.num_rows} rows")
```

### 9.2 检查类型

| Check | 参数 | 说明 |
|-------|------|------|
| `length` | `min`, `max` | 字符串长度范围 |
| `range` | `min`, `max` | 数值范围 |
| `regex` | `pattern`, `invert` | 正则匹配 (invert=True 匹配不满足的) |
| `duplicate` | — | 精确哈希重复检测 |

### 9.3 动作类型

| Action | `evaluate()` 中的效果 | `apply()` 中的效果 |
|--------|-------------------|-----------------|
| `reject` | 报告违规数量 | 移除违规行 |
| `remove` | 报告违规数量 | 移除违规行 (与 reject 相同) |
| `flag` | 报告违规数量 | 保留行 (仅作标记) |

### 9.4 从 JSON 加载

```json
{
  "rules": [
    {"name": "min_text", "column": "text_content", "check": "length", "params": {"min": 10}, "action": "reject"},
    {"name": "valid_score", "column": "score", "check": "range", "params": {"min": 0.0, "max": 1.0}, "action": "flag"},
    {"name": "email_format", "column": "email", "check": "regex", "params": {"pattern": "^.+@.+$", "invert": true}, "action": "reject"},
    {"name": "dedup", "column": "text_content", "check": "duplicate", "action": "remove"}
  ]
}
```

```python
engine = QualityRuleEngine()
engine.load_from_json("rules.json")
```

### 9.5 REST API

```bash
# 通过 API 应用规则
curl -X POST http://localhost:8000/api/v1/datasets/articles/quality/rules \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "rules": [
      {"name": "min_len", "column": "text_content", "check": "length", "params": {"min": 10}, "action": "reject"},
      {"name": "no_dupes", "column": "text_content", "check": "duplicate", "action": "remove"}
    ]
  }'
```

---

## 10. 行级/列级访问控制 (v1.4.0)

行级和列级 ACL 限制每个角色在查询和搜索结果中能看到的数据。

### 10.1 设置 ACL 规则

```bash
# Viewer 只能看到 "title" 和 "summary" 列
curl -X PUT http://localhost:8000/api/v1/admin/acl/articles \
  -H "X-API-Key: admin-key" \
  -H "Content-Type: application/json" \
  -d '{"role": "viewer", "visible_columns": ["title", "summary"]}'

# Viewer 只能看到 region == US 的行
curl -X PUT http://localhost:8000/api/v1/admin/acl/sales \
  -H "X-API-Key: admin-key" \
  -H "Content-Type: application/json" \
  -d '{"role": "viewer", "row_filter": "region == US"}'

# 组合：列裁剪 + 行过滤
curl -X PUT http://localhost:8000/api/v1/admin/acl/hr_data \
  -H "X-API-Key: admin-key" \
  -H "Content-Type: application/json" \
  -d '{"role": "viewer", "visible_columns": ["name", "department"], "row_filter": "department == Engineering"}'
```

### 10.2 列出和删除 ACL

```bash
# 列出数据集的所有 ACL
curl http://localhost:8000/api/v1/admin/acl/articles -H "X-API-Key: admin-key"

# 删除 ACL
curl -X DELETE http://localhost:8000/api/v1/admin/acl/articles/viewer -H "X-API-Key: admin-key"
```

### 10.3 工作原理

- **列裁剪**：不可见列在查询/搜索结果序列化之前被移除
- **行过滤**：使用简单的 `column op value` 表达式 (`==`、`!=`、`<`、`<=`、`>`、`>=`) 过滤结果行
- **Admin 绕过**：`admin` 角色（v1.5.2 起使用 Role enum）始终能看到所有数据，不受 ACL 配置影响
- **无 ACL = 不过滤**：如果某个角色+数据集没有配置 ACL，结果原样返回
- **自动应用**：所有查询 (OLAP/元数据/Daft) 和搜索 (向量/全文/混合/分面/集成) 端点都会自动应用 ACL

***

## 11. Gravitino 标签与策略 (v1.4.1)

Arrow Lake 集成了 **Apache Gravitino** 实现元数据驱动的数据治理。`GravitinoTagService` 和 `GravitinoPolicyService` 提供数据分类、保留管理和列脱敏功能，可通过 REST API 或编程方式管理。

### 11.1 GravitinoTagService — 数据分类

`GravitinoTagService` 封装了 Gravitino Tag API，用于对表和列进行分类。当 Gravitino 不可用时会优雅降级（返回空列表而非报错）。

```python
from arrow_lake.quality.gravitino_tags import GravitinoTagService

tag_svc = GravitinoTagService(config.gravitino)

# 预定义标签常量
print(GravitinoTagService.SENSITIVE)   # "sensitive"
print(GravitinoTagService.PII)         # "pii"
print(GravitinoTagService.FINANCIAL)   # "financial"
print(GravitinoTagService.EXPIRES_30D) # "expires:30d"

# 创建自定义标签
tag_svc.create_tag("internal_only", comment="Internal use only — not for external sharing")

# 给表打标签
tag_svc.tag_table("hr_data", ["sensitive", "pii"])

# 给特定列打标签
tag_svc.tag_column("hr_data", "ssn", ["pii"])

# 列出表的所有标签
tags = tag_svc.list_tags("hr_data")
print(tags)  # ["sensitive", "pii"]

# 查找具有特定标签的所有表
tables = tag_svc.get_tables_by_tag("pii")
print(tables)  # ["hr_data", "customer_records"]
```

#### 预定义标签

| 常量                | 值               | 用途                  |
| ----------------- | --------------- | ------------------- |
| `SENSITIVE`       | `"sensitive"`   | 通用敏感数据标记            |
| `PII`             | `"pii"`         | 个人身份信息              |
| `FINANCIAL`       | `"financial"`   | 金融或支付相关数据           |
| `EXPIRES_30D`     | `"expires:30d"` | 30 天后应清除的数据         |

### 11.2 GravitinoPolicyService — 保留策略与脱敏

`GravitinoPolicyService` 管理保留策略和脱敏策略，实现自动化的数据生命周期治理。

```python
from arrow_lake.quality.gravitino_policies import GravitinoPolicyService

policy_svc = GravitinoPolicyService(config.gravitino)

# 创建保留策略 — 数据保留 90 天
policy_svc.create_retention_policy("log_retention", days=90)

# 创建脱敏策略 — 脱敏指定列
policy_svc.create_masking_policy("email_mask", columns=["email", "phone"])

# 将策略应用到表
policy_svc.apply_policy("email_mask", "customer_data")

# 列出所有策略
policies = policy_svc.list_policies()
print(policies)  # ["log_retention", "email_mask"]
```

### 11.3 标签与策略的 REST API

标签和策略也可通过 `/api/v1/metadata/*` REST 端点管理。所有端点需要 `X-API-Key` 请求头，当 Gravitino 未配置时返回 503。

```bash
# --- 标签 ---

# 列出标签 (可按表过滤)
curl "http://localhost:8000/api/v1/metadata/tags?table=articles" \
  -H "X-API-Key: your-key"
# => {"success": true, "data": [{"name": "sensitive"}], "error": null, "metadata": {"total": 1}}

# 创建标签（JSON body）
curl -X POST http://localhost:8000/api/v1/metadata/tags \
  -H "X-API-Key: your-key" -H "Content-Type: application/json" \
  -d '{"name": "pii", "comment": "PII data"}'
# => {"success": true, "data": {"name": "pii"}, "error": null, "metadata": {}}

# --- 策略 ---

# 列出所有策略
curl http://localhost:8000/api/v1/metadata/policies \
  -H "X-API-Key: your-key"
# => {"success": true, "data": [{"name": "log_retention"}], "error": null, "metadata": {"total": 1}}

# 创建保留策略（JSON body）
curl -X POST http://localhost:8000/api/v1/metadata/policies/retention \
  -H "X-API-Key: your-key" -H "Content-Type: application/json" \
  -d '{"name": "log_retention", "days": 90}'
# => {"success": true, "data": {"name": "log_retention", "days": 90}, "error": null, "metadata": {}}

# 创建脱敏策略（JSON body，function 必填：redact|hash|partial|nullify）
curl -X POST http://localhost:8000/api/v1/metadata/policies/masking \
  -H "X-API-Key: your-key" -H "Content-Type: application/json" \
  -d '{"name": "email_mask", "columns": ["email"], "function": "partial"}'
# => {"success": true, "data": {"name": "email_mask", "columns": ["email"], "function": "partial"}, "error": null, "metadata": {}}
```

### 11.4 启用 Gravitino

```yaml
# config.yaml
gravitino:
  enabled: true
  uri: "http://localhost:8090"        # Gravitino 服务器 URI
  metalake: "arrow_lake"              # Metalake 名称
  lance_rest_enabled: true            # 启用 Lance REST Catalog
  lance_rest_uri: "http://localhost:8888"
  sync_interval_seconds: 300          # 后台 Catalog 同步间隔
```

当 `gravitino.enabled` 为 `false` (默认值) 时，所有 `/api/v1/metadata/*` 端点返回 503，`GravitinoTagService`/`GravitinoPolicyService` 构造函数静默完成，不进行连接。现有的质量过滤、去重和 ACL 功能不受影响。

***

## 12. MinHash 近似去重 (CPU datasketch)

除精确 SHA-256 和感知 pHash 外，`ContentDeduplicator` 还内置 **MinHash LSH** 近似去重
（`quality/dedup.py:70,91-96,132`），基于 CPU 版 `datasketch`，用于检测**语义近重复**的文本
（改写、少量编辑的变体），无需 GPU。

```python
from arrow_lake.quality.dedup import ContentDeduplicator

deduper = ContentDeduplicator(
    strategy="minhash",      # MinHash LSH 近似去重
    action="flag",
    text_column="text_content",  # 必填（strategy="minhash" 时强制要求，dedup.py:95-96）
    ngram_size=5,            # 字符 n-gram shingling 大小
    num_hashes=128,          # MinHash 置换数（num_perm）
    threshold=0.8,           # Jaccard 相似度阈值 (0.0–1.0)，高于此视为近重复
)
result = deduper.deduplicate(table)
# DedupResult(strategy="minhash", ...)
```

> **陷阱（YAML 不接受 minhash）**：`QualityConfig.dedup_strategy` 的 validator 只允许
> `exact`/`perceptual`/`both`（`config/media.py:129-134`），写 `minhash` 会触发
> `ValidationError`。MinHash **只能编程式调用** `ContentDeduplicator(strategy="minhash", ...)`，
> 不能通过 `dedup_strategy: minhash` 配置。大规模语料（>1M 行）需 GPU 时用 §5 的 `NeMoDeduplicator`。

| 参数 | 默认 | 说明 |
|------|------|------|
| `strategy` | `exact` | `minhash` 走独立路径（`dedup.py:132`） |
| `text_column` | (必填) | 要计算 MinHash 的文本列 |
| `ngram_size` | `5` | 字符 n-gram |
| `num_hashes` | `128` | MinHash 置换数（精度/成本权衡） |
| `threshold` | `0.8` | Jaccard 相似度阈值 |

***

## 13. 质量画像与评分 (QualityProfiler)

`QualityProfiler`（`quality/profiler.py:39`）对数据集做整体质量画像，输出 `DatasetQualityProfile`，
含 `overall_quality_score`（0.0–1.0，`profiler.py:34`）和各维度统计。对应 REST 端点
`GET /api/v1/datasets/{name}/quality/profile`（`routers/quality.py:156`）。

```python
from arrow_lake.quality.profiler import QualityProfiler

profiler = QualityProfiler()
profile = profiler.profile(table, dataset_name="articles")
print(profile.overall_quality_score)   # 0.0–1.0
# DatasetQualityProfile 还含空值率、基数、分布等维度统计
```

```bash
# REST：获取数据集质量画像
curl http://localhost:8000/api/v1/datasets/articles/quality/profile -H "X-API-Key: your-key"
```

***

## 14. 质量 REST API 全景

`routers/quality.py` 暴露的端点（前缀 `/api/v1/datasets/{name}/quality`）：

| 方法 | 端点 | 说明 | 代码位置 |
|------|------|------|---------|
| `POST` | `/quality/filter` | 运行质量过滤器，返回聚合报告 | quality.py:46 |
| `GET` | `/quality/report` | 获取上一次质量过滤报告 | quality.py:64 |
| `POST` | `/quality/deduplicate` | 对数据集去重（exact/perceptual/both） | quality.py:81 |
| `POST` | `/quality/rules` | 应用声明式规则引擎（见 §9.5） | quality.py:105 |
| `GET` | `/quality/profile` | 质量画像与评分（见 §13） | quality.py:156 |
| `POST` | `/quality/llm_label` | LLM 富化：给行打标签（**异步 202**） | quality.py:213 |
| `POST` | `/quality/extract` | LLM 富化：从文本抽取结构化字段（**异步 202**） | quality.py:252 |
| `POST` | `/quality/mask-preview` | 预览脱敏效果（function/columns） | quality.py:291 |

`llm_label` 与 `extract` 走 fire-and-forget 异步任务（`status_code=202`），立即返回 `task_id`，
通过 `GET /api/v1/tasks/{task_id}/status` 轮询结果（见第 14 章 TaskManager）。

```bash
# 去重（REST）
curl -X POST http://localhost:8000/api/v1/datasets/articles/quality/deduplicate \
  -H "X-API-Key: your-key" -H "Content-Type: application/json" \
  -d '{"strategy": "exact", "action": "flag"}'

# mask-preview（function 必填：redact|hash|partial|nullify）
curl -X POST http://localhost:8000/api/v1/datasets/hr_data/quality/mask-preview \
  -H "X-API-Key: your-key" -H "Content-Type: application/json" \
  -d '{"columns": ["ssn", "email"], "function": "partial"}'
```

***

## 15. 数据准备：清洗与 LLM 富化

### 15.1 结构化清洗 `POST /clean`

`routers/cleaning.py:222` 的 `POST /api/v1/datasets/{name}/clean` 把声明式清洗步骤（DuckDB 语义）
编译成 SQL，再经 `restore_dataset` 写回 Lance。支持按列链式算子。

```bash
curl -X POST http://localhost:8000/api/v1/datasets/sales/clean \
  -H "X-API-Key: your-key" -H "Content-Type: application/json" \
  -d '{"steps": [
        {"column": "revenue", "op": "fill_null", "value": 0},
        {"column": "region",  "op": "trim"},
        {"column": "email",   "op": "lowercase"}
      ]}'
```

### 15.2 LLM 富化（llm_label / extract，异步）

`quality/llm_enrich.py:105,156` 提供两类 LLM 富化，均异步执行（202 + task_id 轮询）：

- **`llm_label`**（quality.py:213）：对每行文本运行 LLM 分类，新增标签列（如情感、主题、意图）。
- **`extract`**（quality.py:252）：从非结构化文本抽取结构化字段（实体、键值对），新增列。

```bash
# 异步 LLM 打标签（立即返回 task_id）
curl -X POST http://localhost:8000/api/v1/datasets/articles/quality/llm_label \
  -H "X-API-Key: your-key" -H "Content-Type: application/json" \
  -d '{"text_column": "text_content", "label_column": "sentiment", "prompt": "positive|negative|neutral"}'
# => 202 {"task_id": "...", "status": "pending"}

# 轮询结果
curl http://localhost:8000/api/v1/tasks/<task_id>/status -H "X-API-Key: your-key"
```

### 15.3 字段注释 (v1.9.3)

`ingest/field_comments.py` 支持给数据集字段加人类可读注释（PyArrow 直读 parquet/CSV sidecar，
写入 Lance schema `comment` 元数据）。对应 `POST /api/v1/datasets/{name}/schema/annotate`。

```bash
# 给字段加注释
curl -X POST http://localhost:8000/api/v1/datasets/articles/schema/annotate \
  -H "X-API-Key: your-key" -H "Content-Type: application/json" \
  -d '{"field": "text_content", "comment": "正文文本（已清洗，去 HTML 标签）"}'

# 查看带注释的 schema
curl http://localhost:8000/api/v1/datasets/articles/schema -H "X-API-Key: your-key"
```

字段注释持久化在 Lance schema 的 `SchemaField.comment` 中，摄入时由 `_write_table` 钩子自动捕获，
后续 `GET /schema` 会回显。
