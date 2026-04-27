# Round 3: 示例质量 + 代码规范

**日期**: 2026-04-27
**范围**: 全项目 ruff 修复、代码规范、类型注解

---

## 审查发现 (18 项)

### 已修复 (15 项)

| # | 文件 | 问题 | 状态 |
|---|------|------|------|
| 1 | `api/errors.py:85` | `Any` 未导入 (F821) | ✅ |
| 2 | `rag/pipeline.py:328` | `Any` 未导入 (F821) | ✅ |
| 3 | `_lake_query.py:84` | 嵌套 `with` 语句 (SIM117) | ✅ |
| 4 | `config/__init__.py:46` | 未使用的 `_build_merged_update` 导入 (F401) | ✅ |
| 5 | `cli/embed.py:44` | 未使用的 `pyarrow as pa` 导入 (F401) | ✅ |
| 6 | `cli/embed.py:54` | if-else 可简化为三元 (SIM108) | ✅ |
| 7 | `cli/catalog.py:96` | 嵌套 if 可合并 (SIM102) | ✅ |
| 8 | `cli/kg.py:125` | 嵌套 if 可合并 (SIM102) | ✅ |
| 9 | `ops/backup.py:317` | N806 `_CHUNK` → `_chunk_size` | ✅ |
| 10 | `query/fts.py:211` | N806 `_CHUNK_SIZE` → `_chunk_size` | ✅ |
| 11 | `query/session_manager.py:273` | 未使用的 `now` 变量 (F841) | ✅ |
| 12 | `kg/extractor.py:179` | 歧义变量名 `l` → `line` (E741) | ✅ |
| 13 | `embed/ray_serve_encoder.py:158` | 不可达的重复 except 子句 (B025) | ✅ |
| 14 | `api/errors.py:80` | N806 `_SENSITIVE_CONTEXT_KEYS` → `_sensitive_context_keys` | ✅ |
| 15 | `api/models/embedding.py:67` | N806 `_MAX_BASE64_LEN` → `_max_base64_len` | ✅ |

### 跳过 (3 项)

| # | 文件 | 问题 | 原因 |
|---|------|------|------|
| S1 | `examples/*` | `_get_storage()` 调用 (11 文件) | 大部分调用的功能无公开 API 等价物（版本管理、标签、压缩等） |
| S2 | `examples/*` | bare assert (33 处) | 示例代码中 assert 用于演示验证，合理用法 |
| S3 | `server.py` | E402 imports after warn | 有意为之：deprecation warning 必须在副作用之前触发 |

### ruff 统计

- **修复前**: 70 errors
- **修复后**: 11 errors (全部为 pre-existing 风格建议)
- **自动修复**: 37 项 (ruff --fix)
- **手动修复**: 15 项

### 遗留 11 项 (pre-existing，非本轮引入)

| 类型 | 数量 | 说明 |
|------|------|------|
| SIM105 | 5 | contextlib.suppress 建议（备份、session_manager） |
| B905 | 3 | zip() strict= 建议（kg builder） |
| UP042 | 1 | StrEnum 迁移建议（dead_letter） |
| RUF001 | 1 | CJK 全角标点（chunker 正则表达式） |
| B007 | 1 | 未使用的循环变量（connectors_http） |

---

## 修改文件清单

1. `arrow_lake/api/errors.py` — 添加 `Any` 导入，N806 修复
2. `arrow_lake/rag/pipeline.py` — 添加 `Any` 导入
3. `arrow_lake/_lake_query.py` — 合并嵌套 with (SIM117)
4. `arrow_lake/config/__init__.py` — 移除未使用导入，RUF022 抑制
5. `arrow_lake/cli/embed.py` — F401 修复，SIM108 三元
6. `arrow_lake/cli/catalog.py` — SIM102 合并 if
7. `arrow_lake/cli/kg.py` — SIM102 合并 if
8. `arrow_lake/ops/backup.py` — N806 变量重命名
9. `arrow_lake/query/fts.py` — N806 变量重命名
10. `arrow_lake/query/session_manager.py` — F841 移除未使用变量
11. `arrow_lake/knowledge_graph/extractor.py` — E741 变量重命名
12. `arrow_lake/embed/ray_serve_encoder.py` — B025 合并重复 except
13. `arrow_lake/api/models/embedding.py` — N806 变量重命名
14. `arrow_lake/server.py` — E402 noqa 注释
15. `arrow_lake/config/search.py` — ruff --fix 自动修复

---

## 验证结果

- `ruff check arrow_lake/`: 11 errors (pre-existing)
- 单元测试: 运行中
