# Round 4: 测试补全 + 边界场景

**日期**: 2026-04-27
**范围**: 新增安全相关单元测试、修复既有测试回归

---

## 新增测试文件 (5 个，33 个测试用例)

| 文件 | 测试数 | 覆盖内容 |
|------|--------|----------|
| `tests/unit/auth/test_jwt_validation.py` | 7 | JWT 密钥强度验证 (H1) |
| `tests/unit/kg/test_gremlin_safety.py` | 6 | Gremlin blocked patterns 完整性 (C1) |
| `tests/unit/rag/test_prompt_injection.py` | 8 | Prompt 注入过滤 (H4) |
| `tests/unit/ingest/test_path_validation.py` | 6 | 文件路径清理 (H2) |
| `tests/unit/ops/test_backup_path_validation.py` | 6 | 备份路径遍历防护 (C3) |

## 既有测试修复 (1 个文件)

| 文件 | 修改内容 |
|------|----------|
| `tests/unit/facade/test_lake_facade.py` | `TypeError` → `ValidationError` (2 处，匹配 Round 2 源码修改) |

## 代码重构 (1 个文件)

| 文件 | 修改内容 |
|------|----------|
| `arrow_lake/rag/pipeline.py` | `PROMPT_INJECTION_RE` 提升为模块级常量（从方法内局部变量），支持直接测试 |

---

## 验证结果

- 新增测试: 33/33 passed
- `ruff check arrow_lake/`: 11 errors (pre-existing)
- 全量单元测试: 运行中

---

## 遗留项

- Ray Serve embedding fallback 测试 (3 failed) — pre-existing，与本次修改无关
- `upsert` 方法的非 Table 类型拒绝测试未添加（与 create/append 模式一致）
