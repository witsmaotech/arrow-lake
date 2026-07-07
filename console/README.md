# Arrow Lake Console · SQL Worksheet

DuckDB SQL Web 界面,集成进 Arrow Lake 湖仓。**走产品已有的 OLAP 端点**,完全复用 RBAC / SELECT 安全校验 / 行级 ACL / SSE 流式。

设计文档:`docs/architecture-design/duckdb-sql-worksheet.md`

## 为什么不用现成 DuckDB UI

- DuckDB 官方 `ui` extension:UI 不开源 + 本地单用户 + 绕过 RBAC
- duckdb-wasm:数据下沉浏览器,敏感数据不安全
- DbGate/CloudBeaver 直连型:绕过产品安全边界

本方案:UI 调产品 API(`/api/v1/datasets/{name}/query/olap`),不直连 DuckDB。

## 结构

```
console/
├── index.html            # 入口路由(已登录→olap,未登录→login)
├── login.html            # X-API-Key 换 JWT(auth_mode=BOTH)
├── olap.html             # ★ SQL Worksheet 主页
├── assets/
│   ├── tokens.css        # 设计真值源(复制自 prototype)
│   ├── app.css           # 组件库(复制自 prototype)
│   ├── console.css       # 增量样式(editor/table/toast/login)
│   └── console-layout.js # 精简 shell + icon + API 抽屉
└── src/                  # ES module(零构建)
    ├── auth.js           # token 存取 + login/logout
    ├── api.js            # fetch 封装 + 401 自动 refresh
    ├── ui/{toast,table}.js
    └── olap/{editor,results,stream,worksheet}.js
```

## 开发模式

```bash
cd console
python3 -m http.server 5180
# 浏览器:http://localhost:5180/login.html
```

开发时前端在 5180、API 在 8000(跨域),需 API 侧 `cors_origins` 加 `http://localhost:5180`。
`src/auth.js` / `api.js` 自动检测 5180 → 走 `http://127.0.0.1:8000/api/v1`。

## 生产部署(FastAPI 同源 mount)

`arrow_lake/api/app.py` 已挂载:
```python
app.mount("/console", StaticFiles(directory="<repo>/console", html=True), name="console")
```
访问:`http://<api-host>:8000/console/` → 同源、免 CORS、复用 8000 端口。
`config/api.py` `exempt_paths` 已含 `/console`(让 login.html 在 JWT 模式免拦)。

## 用法

1. 登录:输入产品 API Key(`X-API-Key` 换 JWT)
2. 选数据集(自动从 `/datasets` 拉取)
3. 输入 SELECT 语句 → `⌘/Ctrl+Enter` 运行
4. 结果:小结果 JSON 渲染 + CSV 导出;大结果勾 `stream` 走 SSE 增量

## 安全

- 所有请求自动带 `Authorization: Bearer <jwt>`,401 自动 refresh
- SQL 走后端 `validate_sql_safety`(SELECT only)+ 行级 ACL,前端无法绕过
- `toast` 用 `textContent`;单元格值经 `escapeHtml` 防注入

## 依赖

- 零运行时构建(静态 HTML + 浏览器原生 ES module)
- 仅 `stream` 模式动态加载 `apache-arrow`(esm.sh CDN,importmap 声明);离线时降级为非流式 JSON
- 编辑器为 MVP textarea + 行号;CodeMirror 6 升级见设计文档 Phase 4
