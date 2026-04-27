# Round 1: 安全加固 + CRITICAL 修复

**日期**: 2026-04-27
**范围**: arrow_lake/ 源码安全审查 + CRITICAL 级缺陷修复

---

## 发现问题 (9 项)

### CRITICAL (4 项)

| # | 文件 | 问题 | 状态 |
|---|------|------|------|
| C1 | `knowledge_graph/client.py:34` | Gremlin blocked patterns 不完整，缺 groovy./script./ExecTransformer 等 | ✅ 已修复 |
| C2 | `query/_db.py:130` | S3 凭证直接 setdefault 到 os.environ，进程级泄漏 | ✅ 已修复 (save/restore 模式) |
| C3 | `ops/backup.py:297` | 路径遍历防护仅检查 ".."，未验证 resolve 后的实际路径 | ✅ 已修复 (resolve + prefix check) |
| C4 | `__init__.py:90` | `VectorSearchBridge` 在 `__all__` 中但从未导入，ImportError | ✅ 已修复 (替换为 VectorSearchResult) |

### HIGH (3 项)

| # | 文件 | 问题 | 状态 |
|---|------|------|------|
| H1 | `config/api.py:88` | JWT 密钥无最小长度验证，弱密钥可被暴力破解 | ✅ 已修复 (>=32 chars) |
| H2 | `ingest/ingestor.py:350` | 文件路径清理不含 ".."，可目录遍历 | ✅ 已修复 |
| H4 | `rag/pipeline.py:99` | 用户输入直接插入 prompt 模板，存在 prompt 注入风险 | ✅ 已修复 (正则过滤) |

### MEDIUM (2 项)

| # | 文件 | 问题 | 状态 |
|---|------|------|------|
| M1 | `ingest/dead_letter.py:158` | bare except pass 静默吞异常，数据丢失无感知 | ✅ 已修复 (structlog warning) |
| M2 | `api/routers/datasets.py` | Content-Type 未验证 | ⏭ 跳过 (FastAPI + Pydantic 已覆盖) |

---

## 修改文件清单

1. `arrow_lake/knowledge_graph/client.py` — 扩充 blocked patterns (+4 条)
2. `arrow_lake/query/_db.py` — S3 凭证 save/restore + resolve path 校验
3. `arrow_lake/ops/backup.py` — resolve() + base prefix 路径校验
4. `arrow_lake/__init__.py` — 移除无效 VectorSearchBridge 导出
5. `arrow_lake/ingest/ingestor.py` — safe_stem 增加 ".." 清理
6. `arrow_lake/config/api.py` — jwt_secret_key >=32 chars 验证
7. `arrow_lake/rag/pipeline.py` — prompt 注入正则过滤
8. `arrow_lake/ingest/dead_letter.py` — structlog 替代 silent pass

---

## 验证结果

- `ruff check`: 通过（除既有 pre-existing issues）
- 单元测试: 运行中
- 示例: 未修改示例代码，不影响

---

## 遗留项

- `rag/pipeline.py:328` — `Any` 未导入（既有问题，非本轮引入）
- `ingest/dead_letter.py:25` — StrEnum 迁移建议（既有问题）
- `ops/backup.py:143,382` — contextlib.suppress 建议（既有问题）
- `ingest/ingestor.py:309` — import 排序建议（既有问题）
