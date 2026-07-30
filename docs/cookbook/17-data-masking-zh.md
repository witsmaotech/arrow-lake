# 数据脱敏（Data Masking）

> 读取路径上的列级隐私控制。策略把敏感列映射到四种脱敏函数之一，对 VIEWER 角色透明
> 强制执行，fail-closed，并经 Lance 审计轨迹记录。

脱敏由 `MaskingEngine`（`arrow_lake/quality/masking_engine.py`）治理，经 Gravitino 策略
层 + 预览端点暴露。它**启动时必须有 HMAC 密钥** —— 缺失则服务拒绝启动。

## 1. 配置 HMAC 密钥（必需）

引擎对 `hash` 输出签名，并在密钥缺失时 fail-closed。启动 API 前设置密钥：

```bash
# deploy/.env 或 compose environment
ARROW_LAKE__MASKING__HMAC_KEY=<your-secret-key>
```

密钥缺失时，启动抛 `RuntimeError`，容器退出。仅开发环境可 opt-in 降级：

```bash
ARROW_LAKE__MASKING__ALLOW_MISSING_KEY=1   # 仅 dev；此时 hash() 在调用时抛错
```

## 2. 创建脱敏策略

策略命名一组列及要应用的函数：

| 函数 | 行为 |
|---|---|
| `redact` | 替换为固定哨兵值（默认） |
| `hash` | HMAC-SHA256，128 位（`[:32]` 十六进制）—— 确定性、可关联 |
| `partial` | 保留首 2 尾 2，中间脱敏（如 `13812345678` → `13*******78`） |
| `nullify` | 替换为 NULL |

```bash
curl -X POST http://127.0.0.1:8000/api/v1/gravitino/policies/masking \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"name": "pii_mask", "columns": ["phone", "email"], "function": "partial"}'
```

策略创建本身会被审计（见 §5）。

## 3. 发布前预览

在提交规则前用真实数据验证：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/datasets/customers/quality/mask-preview \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"columns": ["phone"], "function": "partial"}'
```

返回前几行的脱敏前/后对比：

```json
{"phone": {"before": ["13812345678"], "after": ["13*******78"]}}
```

预览为 **ADMIN 专属**（非 `EDITOR`），以防绕过列级 ACL；列名经标识符白名单校验
以拒绝 SQL 注入。

## 4. 强制执行与 Fail-Closed

策略指向某数据集后，RBAC 层对 VIEWER/EDITOR 角色在读取时透明应用脱敏；**ADMIN
角色完全跳过脱敏**（返回原始数据）。强制执行为 **fail-closed**：若脱敏引擎抛错
（脱敏错误、`hash` 缺密钥、未知函数，或 Gravitino 故障拉取策略失败），查询返回
**空表**，而非泄露未脱敏的源数据。未知函数名在策略**创建时**即被校验拒绝
（HTTP 400）；即便漏网，执行时 `_mask_column` 仍会抛 `ValueError` 兜底。

## 5. 审计

策略创建经 Lance 审计轨迹记录（零新表）：

```bash
curl "http://127.0.0.1:8000/api/v1/audit/query?event_type=masking_policy_created" \
  -H "X-API-Key: $KEY"
```
