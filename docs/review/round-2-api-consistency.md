# Round 2: API 一致性 + 错误处理规范化

**日期**: 2026-04-27
**范围**: 类型注解精度、异常类型统一、代码规范

---

## 发现问题 (7 项)

### HIGH (3 项)

| # | 文件 | 问题 | 状态 |
|---|------|------|------|
| H1 | `_lake_ingest.py:226,277` | `append_dataset` 和 `upsert` 使用 `ValidationError` 但未导入 | ✅ 已修复 |
| H2 | `_lake_audit.py:12` | `_get_audit_trail` 返回 `Any`，应为 `AuditTrail` | ✅ 已修复 |
| H3 | `_lake_audit.py:79` | `audit_query` 返回 `list[Any]`，应为 `list[AuditEntry]` | ✅ 已修复 |

### MEDIUM (4 项)

| # | 文件 | 问题 | 状态 |
|---|------|------|------|
| M1 | `_lake_search.py:68,166,246` | 嵌套 `with` 语句可合并 (SIM117) | ✅ 已修复 |
| M2 | `_lake_audit.py:109` | `audit_analyze` 使用 `hasattr` 检查，应直接用 `AnomalyRecord` | ✅ 已修复 |
| M3 | `api/routers/query.py:19-64` | OLAP/Daft 端点无 asyncio 超时保护，阻塞事件循环 | ✅ 已修复 |
| M4 | `_lake_kg.py` | KG 方法重复错误检查模式 (22 处) | ⏭ 跳过 (模式已充分集中化) |

---

## 审查结论

### KG 错误集中化 (M4) — 跳过原因

`_lake_kg.py` 有 26 个 KG 方法，其中 22 个包含 guard 模式。审查结论：

1. **已有三层集中化**：`_ensure_kg_enabled()` + `_get_kg_client()`/`_get_vermeer_client()`/`_get_kg_builder()` + 组件工厂
2. **guard 仅 3-4 行**，且每种 guard 有不同的错误码和消息
3. **进一步集中化会降低可读性**，违反 Python 显式优于隐式原则

### config/search.py mutable default — 无问题

所有 list 字段均为 Pydantic BaseModel 字段，Pydantic 自动处理可变默认值。

---

## 修改文件清单

1. `arrow_lake/_lake_ingest.py` — `upsert` 方法添加 `ValidationError`/`ErrorCode` 导入
2. `arrow_lake/_lake_audit.py` — 精确化类型注解 (`AuditTrail`, `AuditEntry`, `AnomalyRecord`)
3. `arrow_lake/_lake_search.py` — 合并嵌套 `with` 语句 (3 处 SIM117)
4. `arrow_lake/api/routers/query.py` — 添加 `asyncio.wait_for` 超时保护 + `run_in_executor`

---

## 验证结果

- `ruff check`: 全部通过
- 单元测试: 运行中

---

## 遗留项

- `api/routers/query.py` — `lake` 参数缺少类型注解（依赖注入模式，可接受）
- `_lake_audit.py:98` — `audit_export` 返回类型仍为 `dict[str, Any]`（底层实现约束，无法进一步精确化）
