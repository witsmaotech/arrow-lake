# Arrow Lake v1.2.1 技术维度评审报告

## 评分: 7.5/10

## 优势 (5 点)

1. 认证体系设计完善 — API Key (hmac.compare_digest) + JWT (HS256/RS256/ES256) + Bootstrap Token
2. SQL 注入防护全面 — 集中式 validation.py，DANGEROUS_SQL_KEYWORDS_RE + SAFE_IDENTIFIER_RE
3. 可观测性基础设施完备 — structlog + correlation_id + Prometheus 30+ 指标 + OpenTelemetry
4. 资源治理与可靠性 — DuckDBSessionManager 信号量并发 + CircuitBreaker + tenacity 重试 + per-dataset RLock
5. SSRF 防护到位 — scheme 白名单 + DNS 解析后私有 IP 范围检查

## 待改进

**[HIGH] flush_metrics() 函数不存在但被调用** — api/app.py:53-56 调用但 core/metrics.py 未定义，被 try/except 静默吞掉
**[HIGH] require_role() 无 auth_service 时默认给 ADMIN 权限** — api/deps.py:77-79 绕过所有 RBAC
**[HIGH] 环境变量中 S3 凭证泄露风险** — query/_db.py:132-147 写入 os.environ 有 TOCTOU 窗口
**[HIGH] JWT access token 默认给固定 user_id** — api/routers/auth.py:111 硬编码 "api-user"

**[P1] OLAP EXPLAIN 端点的 SQL 注入风险** — query/olap.py:293 f-string 拼接
**[P1] PyJWT 是可选依赖但 auth_mode=jwt 时不校验安装** — pyproject.toml:66
**[P1] Rate Limit 仅限单进程内存** — api/rate_limit.py 多 worker 失效
**[P1] BaseHTTPMiddleware 未完全迁移** — 3 个中间件仍有 state 传播风险

**[P2] 安全头缺少 Permissions-Policy**
**[P2] CSP 配置默认为空**
**[P2] Audit HMAC Secret 验证在运行时而非启动时**
**[P2] delete_dataset 使用 shutil.rmtree 无额外安全检查**
**[P3] Metrics 未暴露连接池空闲/活跃比**
**[P3] TokenPayload 缺少 jti 声明，refresh token 不可撤销**

## 安全发现

| 类别 | 发现 | 严重程度 |
|------|------|----------|
| 认证 | require_role() 无 auth_service 时默认 ADMIN | HIGH |
| 认证 | JWT 未作为必需依赖 | P1 |
| 凭证管理 | S3 凭证写入 os.environ 有泄露窗口 | HIGH |
| 凭证管理 | API Key 换 JWT 时 user_id 硬编码 | HIGH |
| SQL 注入 | EXPLAIN sql f-string 拼接 | P1 |
| SSRF | 防护完善 | OK |
| 路径遍历 | 标识符正则有效阻止 | OK |
| 硬编码密钥 | 未发现 | OK |
| 不安全反序列化 | 仅使用 json.loads 和 yaml.safe_load | OK |
| 命令注入 | 未发现 subprocess/os.system 调用 | OK |
| CORS | allow_credentials=False | OK |
| 安全头 | 缺少 Permissions-Policy | P2 |
| 速率限制 | 单进程内存实现 | P1 |
| 中间件 | 3 个仍用 BaseHTTPMiddleware | P1 |
| Token 安全 | 无 jti 字段 | P3 |
