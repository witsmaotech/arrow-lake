# 多用户 RBAC 用户体系 · 设计文档(B 规划)

> **版本基线**:v1.11.0.3 | **状态**:已实施（v1.9.0 system_db 接管 RBAC/identity/personal_token + v1.9.6 fail-closed 安全加固，见 §10）| **日期**:2026-07-08（初稿）/ 2026-08-03（v1.10.4 校准）
> **关联**:[duckdb-sql-worksheet.md](./duckdb-sql-worksheet.md)、[00-architecture-design.md](./00-architecture-design.md) §5 横切关注点(RBAC)
> **语言约定**:中文正文、英文图注

---

## 目录

- [1. 背景与动机](#1-背景与动机)
- [2. 现状(已具备 vs 缺失)](#2-现状已具备-vs-缺失)
- [3. 目标与非目标](#3-目标与非目标)
- [4. 后端设计](#4-后端设计)
- [5. 前端设计](#5-前端设计)
- [6. 实施阶段](#6-实施阶段)
- [7. 决策记录(ADR)](#7-决策记录adr)
- [8. 风险与对策](#8-风险与对策)
- [9. 验证方案](#9-验证方案)

---

## 1. 背景与动机

SQL Worksheet console(`console/login.html`)当前登录走 `X-API-Key` → `POST /api/v1/auth/token`,在 auth_mode=BOTH 下后端**固定签发 `api-user / EDITOR`** 的 JWT(`arrow_lake/api/routers/auth.py:97`)。

后果:所有登录者同身份(api-user)、同 role(EDITOR)。产品的 RBAC 三档(VIEWER/EDITOR/ADMIN)和行级 ACL(`checker.apply_table_filter`)虽然在跑,但**没有用户维度** —— 无法区分"谁在查"、"ADMIN 才能管的 ACL 谁来管"。

**动机**:让 RBAC 真正按用户生效 —— 不同用户登录拿不同 role,行级 ACL 按用户身份生效,admin 的 `/acl` 管理(已存在)有真正的 ADMIN 操作者。

## 2. 现状(已具备 vs 缺失)

| 已具备 | 位置 |
|----|------|
| ✅ role 三级(VIEWER/EDITOR/ADMIN,JWT payload) | `arrow_lake/api/auth_models.py` |
| ✅ 行级 ACL 过滤 | `checker.apply_table_filter` |
| ✅ ACL 管理端点(dataset/schema,ADMIN only) | `arrow_lake/api/routers/admin.py` |
| ✅ token 全生命周期(token/refresh/logout) | `arrow_lake/api/routers/auth.py` |
| ✅ 限流 + JWT 黑名单 | `rate_limit.py`、`auth.py` |
| ✅ 用户列表(只读?) | `GET /admin/users`(ADMIN) |

| 缺失 |
|----|
| ❌ 用户存储(用户名 / 密码哈希 / role) |
| ❌ 用户名密码登录端点(OAuth2PasswordRequestForm) |
| ❌ 用户 CRUD(ADMIN 创建/改 role/禁用) |
| ❌ Bootstrap ADMIN(首次部署 seed) |

## 3. 目标与非目标

### 目标
1. 用户名密码登录 → 查用户 → 签发对应 role 的 JWT
2. ADMIN 可管理用户(CRUD)+ ACL(已有)
3. 行级 ACL 按登录用户的 role 生效
4. **保持 API Key 兼容**(作为服务账号,CI/SDK 继续用)

### 非目标
- ❌ 单点登录 / SSO / OAuth2 第三方(留后续)
- ❌ 多租户(当前单租户)
- ❌ 细粒度资源权限(表级/列级,留后续;当前 role + dataset ACL 够用)

## 4. 后端设计

### 4.1 用户存储

MVP **SQLite**(`users.db`,零运维,进程内;与 DuckDB 同进程无冲突):

```python
# arrow_lake/auth/users.py(新建)
users(id TEXT PK, username TEXT UNIQUE, password_hash TEXT,
      role TEXT CHECK(role IN ('VIEWER','EDITOR','ADMIN')),
      is_active INT DEFAULT 1, created_at TEXT)
```

规模大或要多实例并发 → 迁 Postgres(走现有 Gravitino/存储抽象)。

### 4.2 密码安全

`passlib[bcrypt]`(`CryptContext(schemes=["bcrypt"])`),不存明文,登录走 `pwd.verify`。
传输层 TLS(部署负责);登录端点复用现有 `rate_limit`(防爆破,burst 已配)。

### 4.3 新端点

| 端点 | 说明 |
|------|------|
| `POST /auth/login` | OAuth2PasswordRequestForm(username/password)→ 查 users → 验密码 → 签 JWT(role from users)|
| `GET /admin/users` | 列用户(ADMIN)— 已有,可能需补字段 |
| `POST /admin/users` | 创建用户(ADMIN) |
| `PUT /admin/users/{id}` | 改 role / 禁用(ADMIN) |
| `DELETE /admin/users/{id}` | 删除(ADMIN) |

`/auth/token`(API Key)保留,作服务账号;`/auth/login`(用户名密码)为人员账号。两者都签 JWT,role 来源不同(前者固定 EDITOR,后者查 users)。

### 4.4 Bootstrap ADMIN

首次部署:env `INITIAL_ADMIN_USERNAME` / `INITIAL_ADMIN_PASSWORD` → lifespan 启动时若 users 表空则 seed 一个 ADMIN。或提供 CLI:`arrow-lake admin create-user --username sysop --role ADMIN`(交互输密码)。

### 4.5 现有 auth 改动(最小)

- `auth.py`:新增 `login_password(username, password)` 查 users + 验密码 + 签 JWT
- `auth.py`:保持 `exchange_token`(API Key)不变
- 不改 `checker.apply_table_filter`(ACL 按 role 不变;用户身份维度未来可加)

## 5. 前端设计

- `console/login.html`:启用"用户名/密码" tab(去 disabled),调 `/auth/login`
- `src/auth.js`:`login(apiKey)` → `/auth/token`;`loginPassword(username, password)` → `/auth/login`(双路径)
- `console-layout.js`:header 用户菜单显示当前 `user_id` + `role`(已从 JWT decode)
- admin 页面(后续):`/console/admin.html` 用户管理(ADMIN only)

## 6. 实施阶段

| 阶段 | 内容 | 工时 |
|------|------|------|
| 1 | 后端:users 存储(SQLite)+ `/auth/login` + bootstrap ADMIN | 1 天 |
| 2 | 后端:用户 CRUD(`admin.py` 扩展)+ 测试 | 0.5 天 |
| 3 | 前端:login 启用密码 tab + 用户菜单 | 0.5 天 |
| 4 | 端到端验证(三档 role + ACL)+ 文档 | 0.5 天 |

合计 ≈ 2.5 天。

## 7. 决策记录(ADR)

- **ADR-1**:用户存储 MVP 用 SQLite(零运维);规模大迁 Postgres
- **ADR-2**:保留 API Key 作服务账号(向后兼容 CI/SDK,不破坏)
- **ADR-3**:密码用 bcrypt(passlib);登录复用 rate_limit 防爆破
- **ADR-4**:ACL 继续按 role(`checker.apply_table_filter` 不变);用户身份维度(`user_id` 细粒度)留后续

## 8. 风险与对策

| 风险 | 对策 |
|------|------|
| 密码泄露 | bcrypt 哈希;不记日志;传输 TLS |
| 破坏现有 API Key 流程 | `/auth/token` 不变,双端点共存 |
| 首次无 ADMIN 卡死 | bootstrap seed(INITIAL_ADMIN_*)或 CLI |
| 用户表并发 | SQLite WAL(单写多读);大规模迁 Postgres |
| 密码找回 | MVP 不做(ADMIN 重置);后续邮箱找回 |

## 9. 验证方案

1. **ADMIN 登录** → `GET /admin/users` 看列表;`POST /admin/users` 建用户
2. **EDITOR 登录** → `POST /datasets/{name}/query/olap` 跑 SQL(走行级 ACL)
3. **VIEWER 登录** → 只读;试写操作应 403
4. **API Key 登录**(服务账号)→ 仍 EDITOR,向后兼容
5. **限流防爆破** → 错误密码连续打 → 触发 429

---

**下一步**:本设计审批后,按阶段 1→4 实施。阶段 1 后端就绪即可解锁前端密码 tab(去掉 `console/login.html` 的 `disabled`)。

---

## 10. v1.9.6 fail-closed 安全加固矩阵

v1.9.6 把 RBAC 与脱敏路径统一收敛为 **fail-closed**:信任边界出错时向安全一侧失败,绝不向数据泄露一侧失败。

| 路径 | 失败场景 | 行为(fail-closed) |
|---|---|---|
| 脱敏引擎 `_apply_masking` | 引擎抛错(脱敏失败/未知函数/hash 缺密钥) | 返回空表 `slice(0,0)`,不泄露未脱敏源 |
| 行级过滤 `_apply_row_filter` | 表达式不可解析 / 列缺失 / 类型不匹配 | 返回空表,不返回未过滤行 |
| 脱敏策略加载 `_fetch_rules` | Gravitino 拉规则失败 | `raise RuntimeError`(C1),非返空规则集 |
| 启动 | `ARROW_LAKE__MASKING__HMAC_KEY` 缺失 | 启动阻断 `RuntimeError`;`ALLOW_MISSING_KEY=1` opt-in 降级 |
| mask-preview | 列名非法 | 标识符白名单拒绝(防 SQL 注入);端点收紧为 ADMIN |
| lineage 图谱 | 节点/边标签 | HTML 转义(vis title + DOT/Mermaid),防 XSS |

**原则**:隐私或授权路径上的任何错误,宁可返回空结果,也不泄露数据。这与 §4 RBAC 的"默认拒绝"一脉相承。
