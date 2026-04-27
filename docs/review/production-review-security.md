# Arrow Lake v1.2 生产就绪性评估 — 安全工程师

**评估日期**: 2026-04-27
**评估范围**: 认证授权、输入验证、数据保护、API 安全、依赖安全

## 评估总览

| 评审维度 | 评级 | 说明 |
|---------|------|------|
| 1. 认证与授权 | **HIGH** | 多处 HIGH 级问题需发布前修复 |
| 2. 输入验证 | **PASS** | SQL 注入、XSS、路径遍历、命令注入防护到位 |
| 3. 数据保护 | **HIGH** | TLS 缺失，密钥管理待加强 |
| 4. API 安全 | **MEDIUM** | 速率限制有效但多实例不共享 |
| 5. 依赖安全 | **MEDIUM** | 版本约束宽松，CI 扫描不阻断 |

**综合评级: HIGH — 需修复 4 项 HIGH 级问题后方可发布**

---

## 1. 认证与授权

### 1.1 JWT 实现 — HIGH

**优点:** PyJWT + 算法白名单 + 32 字节最短密钥 + access/refresh 双 token

**问题:**
- **[HIGH] 仅支持 HS256 对称算法**: 分布式部署所有实例共享同一 secret，增加泄露风险
- **[HIGH] 空密钥仅警告不阻止**: `AuthService.__init__` 可被直接实例化绕过启动检查
- **[MEDIUM] refresh token 无吊销机制**
- **[MEDIUM] 无 token 绑定**: 不绑定 IP/设备指纹

### 1.2 API Key — MEDIUM

**优点:** `hmac.compare_digest()` 常量时间比较

**问题:**
- **[MEDIUM] 单值静态密钥**: 不支持多 key、轮换、过期
- **[MEDIUM] 无权限隔离**: 所有 key 享有同等权限
- **[LOW] 无 secret manager 集成**

### 1.3 RBAC — MEDIUM

**优点:** ADMIN > EDITOR > VIEWER 层级清晰

**问题:**
- **[MEDIUM] 多个端点缺少 require_role()**: datasets list/get、backup list、olap_query、所有 search、rag_query、kg schema/stats
- **[MEDIUM] API Key 用户默认 ADMIN**: `deps.py:83` API Key 模式下直接返回 ADMIN payload
- **[LOW] ACL 仅存内存**

### 1.4 认证绕过风险 — HIGH

**优点:** JWT + API Key 双模式，OPTIONS 预检正确绕过

**问题:**
- **[HIGH] 双模式中间件分层可能绕过**: `ApiKeyMiddleware` (BaseHTTPMiddleware) 不传播 request.state，与下游 jwt_auth 中间件存在架构风险
- **[HIGH] auth_mode=jwt 时 /api/v2/auth/token 无认证**: 任何未认证请求可获取有效 JWT
- **[MEDIUM] /docs 端点绕过认证**: 忘记关闭 docs_enabled 则暴露完整 API
- **[MEDIUM] /api/v1/version 暴露依赖指纹**

---

## 2. 输入验证

### 2.1 SQL 注入防护 — PASS

集中式 `validation.py`: 危险关键字正则 + 分号检测 + SELECT 强制 + WHERE 验证 + 标识符白名单 + 字面量转义 + LIMIT 上推

### 2.2 XSS 防护 — PASS

JSON API + FastAPI 自动转义

### 2.3 路径遍历防护 — PASS

`..` / `\0` 检查 + 正则白名单 + 绝对路径拒绝

### 2.4 命令注入防护 — PASS

无 subprocess/eval/exec 调用

### 2.5 SSRF 防护 — MEDIUM

**优点:** 私有 IP 阻止列表 + DNS 解析验证 + scheme 限制

**问题:**
- **[MEDIUM] DNS 重绑定 TOCTOU 窗口**
- **[MEDIUM] HugeGraph 硬编码 http:// 明文传输 Basic Auth**

---

## 3. 数据保护

### 3.1 传输加密 — HIGH

- **[HIGH] API 无内置 TLS**: SECURITY.md 声称 `tls_enabled` 但代码中不存在此配置项
- **[HIGH] S3 允许 HTTP 连接**: `http://` endpoint 时设置 `s3_use_ssl=false`
- **[MEDIUM] HugeGraph 始终 HTTP**

### 3.2 存储加密 — LOW

依赖 S3 服务端加密和文件系统加密

### 3.3 敏感数据处理 — PASS

错误 context 过滤敏感字段 + JWT 验证仅 debug 日志

### 3.4 密钥管理 — MEDIUM

- **[MEDIUM] 配置对象持有所有密钥明文**
- **[MEDIUM] S3 密钥嵌入 DuckDB SET 语句**
- **[LOW] 无 secret manager 集成**

---

## 4. API 安全

### 4.1 速率限制 — MEDIUM

**优点:** 默认启用 + asyncio.Lock + 429 + Retry-After

**问题:**
- **[MEDIUM] 纯内存，多实例不共享**
- **[MEDIUM] override_per_endpoint 配置存在但未使用**

### 4.2 CORS — PASS

凭证关闭 + 方法/头白名单

### 4.3 安全响应头 — PASS

X-Content-Type-Options + X-Frame-Options + Referrer-Policy + HSTS + 可配置 CSP

### 4.4 请求大小限制 — PASS

Content-Length 检查 + SQL/RAG/Gremlin 长度限制

### 4.5 文件上传 — PASS

API 不接受 multipart，接受路径列表 + 验证

---

## 5. 依赖安全

### 5.1 已知漏洞 — MEDIUM

- **[MEDIUM] 外围依赖 `>=` 约束无精确版本**
- **[MEDIUM] CI 扫描使用 `|| true` 不阻断**
- **[MEDIUM] 无锁文件**

### 5.2 供应链 — MEDIUM

- **[MEDIUM] 无依赖哈希校验**

### 5.3 最小权限 — MEDIUM

- **[MEDIUM] S3 密钥空值在非 LOCAL 模式下行为不一致**
- **[MEDIUM] LLM API Key 默认空字符串**

---

## 6. SECURITY.md 一致性

| 声称 | 代码现状 | 一致性 |
|------|---------|--------|
| Rate Limiting via slowapi | 实际为自实现 RateLimitMiddleware | **不一致** |
| HTTPS enforced (tls_enabled) | 代码中无 tls_enabled 配置 | **不一致** |
| pip-audit . | CI 中 pip-audit \|\| true | **部分一致** |
| File uploads: os.path.basename + whitelist | 实际为 `..` 检查 + 正则白名单 | **部分一致** |
| 其余 9 项声称 | 代码实现匹配 | 一致 |

---

## 修复优先级汇总

### HIGH (4 项)

| # | 问题 | 位置 |
|---|------|------|
| H1 | SECURITY.md 声称 tls_enabled 但代码不存在 | `api/app.py`, `SECURITY.md` |
| H2 | JWT 仅 HS256，分布式密钥管理风险 | `config/api.py:89` |
| H3 | JWT/API Key 双模式中间件分层绕过风险 | `api/app.py:182-222` |
| H4 | auth_mode=jwt 时 token 端点无认证 | `api/routers/auth.py:40-41` |

### MEDIUM (10 项)

| # | 问题 |
|---|------|
| M1 | refresh token 无吊销机制 |
| M2 | API Key 不支持轮换和多 key |
| M3 | 多个读写端点缺少 require_role() |
| M4 | API Key 用户默认 ADMIN |
| M5 | DNS 重绑定 TOCTOU |
| M6 | HugeGraph 明文 HTTP |
| M7 | 速率限制 override_per_endpoint 未使用 |
| M8 | S3 密钥嵌入 DuckDB SET |
| M9 | CI 安全扫描不阻断 |
| M10 | 依赖版本约束宽松无锁文件 |

### PASS (6 项)

SQL 注入防护 / XSS 防护 / 路径遍历防护 / 命令注入防护 / CORS 配置 / 安全响应头
