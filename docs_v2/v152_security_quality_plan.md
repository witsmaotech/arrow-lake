# Arrow Lake v1.5.2 — 安全加固 & 代码质量改进方案

> 基于 v1.5.1 全量安全 & 质量审查，共发现 68 项问题（8 CRITICAL / 22 HIGH / 18 MEDIUM / 20 LOW）。
> 本方案聚焦 v1.5.2 可落地的修复，按优先级分 Phase 实施。

## 版本信息

| 项目 | 值 |
|------|-----|
| 当前版本 | v1.5.1 |
| 目标版本 | v1.5.2 |
| 主题 | Security Hardening & Quality Fix |
| 审查日期 | 2026-06-01 |

---

## Phase 0: 自动清理（ruff --fix）

一键修复全部 F401（未使用导入）和 I001（import 排序）：

```bash
ruff check --fix arrow_lake/ flows/
```

预计消除 ~31 项 lint 问题（18 F401 + 13 I001）。

---

## Phase 1: CRITICAL 修复（8 项，必须完成）

### S1. JWT 空密钥不阻止启动

**文件**: `arrow_lake/api/auth_service.py:220`
**问题**: HS256 + 空 `secret_key` 仅打 warning，攻击者可用空字符串伪造任意 token
**修复**:
```python
# _validate_config() 中
if self._algorithm == "HS256" and not self._secret_key:
    raise ValueError("JWT secret_key is required for HS256 algorithm. Set ARROW_LAKE__AUTH__JWT_SECRET_KEY")
```

### S2. Kerberos principal 命令注入

**文件**: `arrow_lake/catalog/gravitino_auth.py:126-131`
**问题**: `principal` 通过 f-string 拼接到 subprocess 的 Python `-c` 脚本，可注入任意代码
**修复**: 改用 Python gssapi API 直接调用，不拼接命令行
```python
def _get_spnego_token(self) -> str:
    import gssapi
    name = gssapi.Name(self._principal)
    ctx = name.initiate_context()
    token = ctx.step()
    return base64.b64encode(token).decode()
```

### S3. SQL 注入 — gravitino_stats.py

**文件**: `arrow_lake/catalog/gravitino_stats.py:51-76`
**问题**: f-string 拼接 table name 到 DuckDB 查询
**修复**: 使用参数化查询 + 标识符校验
```python
from arrow_lake.validation import validate_identifier

def collect_table_stats(self, name: str, conn: Any) -> dict[str, Any]:
    validate_identifier(name)  # 白名单: ^[a-zA-Z0-9_-]+$
    cols = conn.execute(
        "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = ?",
        [name],
    ).fetchall()
    row = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()
    row = conn.execute(
        "SELECT sum(file_size) / 1024.0 / 1024.0 FROM parquet_metadata(?||'/**/*.parquet')",
        [name],
    ).fetchone()
```

### S4. Redis 默认密码硬编码

**文件**: `deploy/docker-compose.prod.yml:129-130, 350, 369`
**问题**: `${REDIS_PASSWORD:-redisprod}` 提供弱默认密码
**修复**: 移除默认值，强制从 .env 读取
```yaml
ARROW_LAKE__REDIS__URL: "redis://:${REDIS_PASSWORD:?REDIS_PASSWORD must be set}@redis:6379/0"
ARROW_LAKE__REDIS__PASSWORD: "${REDIS_PASSWORD:?REDIS_PASSWORD must be set}"
```

### Q1. @staticmethod 中使用 self — lineage.py

**文件**: `arrow_lake/catalog/lineage.py:248-249`
**问题**: `_notify_gravitino_version` 声明为 `@staticmethod` 但引用 `self`
**修复**: 移除 `@staticmethod` 装饰器

### Q2. `Any` 未导入 — cli/search.py

**文件**: `arrow_lake/cli/search.py:18`
**问题**: `dict[str, Any]` 中 `Any` 未导入
**修复**: 添加 `from typing import Any`

### Q3. `t.Any` 未定义 — ingest/schema.py

**文件**: `arrow_lake/ingest/schema.py:118`
**问题**: `_NARROWING_CHECKS: list[tuple[t.Any, t.Any]]` 中 `t` 不存在
**修复**: 改为 `list[tuple[Any, Any]]`（文件已导入 `Any`）

### Q4. `timedelta` 字符串注解但未导入 — ingest/storage.py

**文件**: `arrow_lake/ingest/storage.py:366`
**问题**: 类型注解中引用 `timedelta` 但未导入
**修复**: 在文件顶部添加 `from datetime import timedelta`

---

## Phase 2: HIGH 修复（22 项）

### 安全加固（13 项）

| ID | 文件 | 修复方案 |
|----|------|----------|
| S5 | `rbac.py:126` | 使用 `Role.ADMIN.value` 替代硬编码 `"admin"` |
| S6 | `auth_service.py:141` | refresh 成功后将旧 refresh token jti 加入黑名单 |
| S7 | `gravitino_auth.py:88` | 校验 `token_url` 必须以 `https://` 开头 |
| S8 | `gravitino_auth.py:97` | 异常日志不包含请求体，使用 `exc_info=False` |
| S9 | `docker-compose.prod.yml:116` | `--forwarded-allow-ips` 改为 Docker 子网 IP |
| S10 | `docker-compose.prod.yml:273,347,388` | MinIO/Redis/Ray 端口加 `127.0.0.1:` 前缀 |
| S11 | `docker-compose.prod.yml:551-890` | 所有监控端口加 `127.0.0.1:` 前缀 |
| S12 | `_lake_admin.py:384` | urlopen 前校验 URL scheme + hostname |
| S13 | `rag/graph_rag.py:41,48` | MD5 改为 SHA-256 或直接用字符串做 dict key |
| S14 | `validation.py:26-34` | SQL blocklist 增加 `ATTACH/DETACH/PRAGMA/LOAD/CALL/SET` |
| S15 | `api/routers/knowledge_graph.py` | Gremlin 白名单移除 `union`，增加查询长度限制 |
| S16 | `api/routers/query.py` | OLAP SQL 添加与 lineage query 同等的 `_validate_sql` |
| S17 | `config/api.py:104` | JWT 空 key 在 `_validate_auth_config` 也抛异常 |

### 质量修复（9 项）

| ID | 文件 | 修复方案 |
|----|------|----------|
| Q5 | `__init__.py:228` | 存储 `create_task` 返回值，添加 done callback |
| Q6 | `rbac.py:459` | 闭包用默认参数绑定 `lambda p=p: str(p)` |
| Q7 | `rbac.py:395` | 添加 `ClassVar` 注解 |
| Q8 | `federated_engine.py:43` | 添加 `ClassVar` 注解 |
| Q9 | `rag.py:96,181` | 添加 `raise ... from None` |
| Q10 | `storage.py:284,397` | `except: pass` 改为 `except: logger.warning(...)` |
| Q11 | `ingest_embed.py:173` | 推断失败添加 `logger.debug` |
| Q12 | `lineage.py:257,328` | Gravitino 失败日志从 `debug` 提升为 `warning` |
| Q13 | `lineage_hooks.py` | 缓存 `LineageStore` 实例避免重复创建 |

---

## Phase 3: MEDIUM 修复（选做，v1.5.3 继续）

- ACL 持久化存储 + 审计日志
- Gravitino 不可达时告警而非静默降级
- SSRF 防护: `connection_url`/`urls`/`target_uri` 校验私有 IP
- `backup_id` 路径格式校验
- 错误响应不泄露 `str(exc)` 内部信息
- 认证端点速率限制 (slowapi)
- `docs_enabled` 默认改为 `False`
- Redis 连接失败添加 warning 日志
- `create_app` 拆分为子函数
- `list.pop(0)` 改用 `deque.popleft()`
- Lineage 全表过滤改用 PyArrow `table.filter()`

---

## 验收标准

- [ ] Bandit 0 个 HIGH / CRITICAL 问题
- [ ] Ruff 0 个 F821（undefined name）
- [ ] Ruff F401 清零
- [ ] `_version.py` = `"1.5.2"`, `pyproject.toml` = `"1.5.2"`
- [ ] `pytest tests/` 全绿
- [ ] `py.typed` 和 `__init__.py` 版本一致

## 变更范围

```
arrow_lake/api/auth_service.py          # S1, S6
arrow_lake/api/rbac.py                  # S5, Q6, Q7
arrow_lake/api/routers/rag.py           # Q9
arrow_lake/catalog/gravitino_auth.py    # S2, S7, S8
arrow_lake/catalog/gravitino_stats.py   # S3
arrow_lake/catalog/lineage.py           # Q1, Q12
arrow_lake/catalog/lineage_hooks.py     # Q13
arrow_lake/cli/search.py                # Q2
arrow_lake/ingest/schema.py             # Q3
arrow_lake/ingest/storage.py            # Q4, Q10
arrow_lake/ingest/ingest_embed.py       # Q11
arrow_lake/rag/graph_rag.py             # S13
arrow_lake/_lake_admin.py               # S12
arrow_lake/__init__.py                  # Q5
arrow_lake/query/federated_engine.py    # Q8
arrow_lake/validation.py                # S14
arrow_lake/api/routers/knowledge_graph.py # S15
arrow_lake/api/routers/query.py         # S16
arrow_lake/config/api.py                # S17
arrow_lake/_version.py                  # 版本号
pyproject.toml                          # 版本号
deploy/docker-compose.prod.yml          # S4, S9, S10, S11
```
