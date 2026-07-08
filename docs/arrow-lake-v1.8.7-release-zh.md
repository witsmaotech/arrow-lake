# Arrow Lake v1.8.7 — 发布说明

> **发布范围**：自 `v1.8.6`（`f79da4c`，2026-07-01）至当前 HEAD（`13f45f9`）
> **代号**：Docling 全栈 + Console 上线 + 旗舰展示
> **规模**：29 commits · 109 文件 · +11,588 / −349 行

---

## 一句话：v1.8.7 带来了什么

v1.8.7 把 Arrow Lake 从「能跑的内核」推到「能演示、能交互、能解析全格式」的产品形态：

- **文档解析换引擎**：Docling 全栈替代 kreuzberg，PDF/Office/图片/邮件一键解析，表格结构化 + VLM 版面理解 + 结构感知分块；
- **Console SQL Worksheet 上线**：浏览器里直接写 DuckDB SQL，走产品 OLAP API，CodeMirror 高亮 + 查询历史 + EXPLAIN；
- **旗舰展示前端原型**：17 页控制台 + 三大深度交互王牌（检索宇宙 / 湖仓时间机器 / KG 探索）+ 双线叙事；
- **KG 与内核加固**：per-dataset 图隔离修复、doc_type 贯通、实体抽取 prompt 升级、LLM 重试退避、Gravitino 迁官方 SDK。

---

## v1.8.7 四大主题

### 主题一｜文档解析：Docling 全栈替代 kreuzberg

kreuzberg 在容器镜像层依赖重、中文 OCR 弱、表格还原差。v1.8.7 用 **Docling**（IBM Research 发起、LF AI & Data Foundation 托管）作为 Python SDK 库内嵌解析后端，分四个阶段落地：

| 阶段 | 内容 | Commit |
|------|------|--------|
| P0 | 多格式默认首选 + `artifacts_path` 离线模型预取 | `6c94c0d` |
| P0.2 | 镜像内预取回退 → 运行时 `HF_HOME` 持久卷（解决镜像膨胀） | `37733c2` |
| P1 | 表格优化：`TableFormerMode.ACCURATE` + `do_cell_matching=True` | `f0a617b` |
| P2 | `VlmPipeline`（GraniteDocling）+ `HybridChunker` 结构感知分块 | `381d9f1` |
| GPU | `Dockerfile.gpu` 加 docling extra + GPU 加速器 + 按真实页码拆分（修 `max_pages`/`page_number`） | `2fa6a99` · `a93a868` |
| 基线 | 替代 kreuzberg：Python SDK 库内嵌 + 多 OCR 后端（RapidOCR 中文 / EasyOCR 多语言） | `20aeda2` |

**为什么是 Docling**：统一 `DoclingDocument` 输出，原生支持 PDF/DOCX/PPTX/XLSX/HTML/EPUB/邮件/图片/音频/LaTeX 等 20+ 格式，布局理解 + 表格结构 + 公式 + 代码块识别一条龙，且与 RAG 分块、Markdown 导出无缝衔接。

> ⚠️ 已知约束：纯 CPU 下 docling 解析 >200s/页，552 页规模的业务 PDF **必须用 GPU 镜像**（`Dockerfile.gpu`，需 RTX 级显卡）。

### 主题二｜交互层：Console SQL Worksheet Web UI

把 DuckDB 查询能力从「命令行 / SDK」搬进浏览器，复用产品自带的 `/query/olap` OLAP API（含 RBAC）：

```
浏览器 (CodeMirror SQL 高亮)
   ↓  X-API-Key + Bearer 双层 auth (BOTH 模式)
/console  (静态资源, 免 JWT 拦截)
   ↓
/query/olap  (产品 OLAP API, RBAC 鉴权)
   ↓
DuckDB / Lance 湖仓
```

**能力清单**：查询历史 · `EXPLAIN` 执行计划 · CodeMirror SQL 语法高亮 · 结果表格 · toast 反馈 · `fillSql` 规范化（去结尾分号 + 多语句取首句）· dataset 名自动加双引号（兼容 `api-test`/`smoke-test` 等含 `-` 的名字）。

**安全**：XSS 修复 + apache-arrow 版本 pin + **移除 stream 模式**消除 apache-arrow CDN 供应链依赖（方案 A，零外部 CDN）。

**登录**：换双栏原型样式（方案 A），配套多用户 RBAC 设计文档（方案 B）。

> 部署：`docker compose -p arrow-lake -f deploy/docker-compose.prod_minimal.yml up -d api`；开发端口 5189。

### 主题三｜可视化：旗舰展示前端原型

不再用 PPT 讲架构，而是做**可反复把玩的深度交互**。两个产出：

**① `showcase.html` — 三王牌 + 架构全景**
- 架构全景五层视图 + ⟂ 横切侧面（横切面放侧栏）；
- 王牌一 **检索宇宙**：向量空间 2D 投影 + 实时查询漏斗；
- 王牌二 **湖仓时间机器**：Lance 版本 timeline slider + 两版 diff；
- 王牌三 **KG 探索**：per-dataset 分图力导向 + GraphRAG 子图。

**② `narrative.html` — 双线叙事**（搁置，作备选）

**17 页控制台原型**：dashboard / datasets / dataset-detail / ingest / olap / kg / rag / search / embeddings / governance / lineage / tasks / audit / system / admin / login / index。

**验证方式**：`.venv` + Playwright + chromium-1217 像素级验证（不肉眼看截图——CDN 缓存会骗眼），本地 5180 起静态服务。

### 主题四｜内核加固：KG 质量 + Gravitino + 部署

| 领域 | 修复 | Commit |
|------|------|--------|
| KG 隔离 | per-dataset 图隔离修复——换 rocksdb 单机 + 每图独立 `data_path` | `d9dbff7` |
| KG 路由 | `doc_type` 贯通：ingest 自动判定 + classifier 修复 + he 路由 | `cbe995b` |
| KG auth | HugeGraph 1.7+ 开 auth 解锁 per-dataset KG + RAG `text_column` 自动探测 | `64e5366` |
| KG 质量 | 升级实体抽取 system prompt，提升建图质量 | `8cf35d6` |
| KG 韧性 | extractor LLM 调用 retry + 指数退避，避免瞬时错误拖垮整批 `kg_build` | `0c1a53b` |
| Gravitino | `_request` 异常分类 + retry，fileset/schema 迁**官方 SDK** | `953102e` |
| Ingest | `blob_key` 容忍 CJK 文件名——非 ASCII 字符替换为下划线 | `28685fa` |
| 部署 | minimal 生产栈 compose + `make prod-minimal` target | `43e62e6` |
| HugeGraph | entrypoint 加 `hugegraph:graph` 别名（gremlin 绑定缺失修复） | （deploy） |

---

## v1.8.7 完整特性清单（29 项）

> 按提交时间倒序，每项标注 commit 短哈希。

### 🎨 旗舰展示与前端（2）
- `13f45f9` **feat(frontend)** 旗舰展示前端原型（17 页控制台 + showcase 三王牌 + narrative 叙事）
- `cffacc1` **feat(console)** login 换原型双栏样式(A) + 多用户 RBAC 设计文档(B)

### 📄 Docling 文档解析（7）
- `a93a868` **fix(docling)** GPU 加速器 + 按真实页码拆分（修 `max_pages`/`page_number`）
- `2fa6a99` **feat(docling)** GPU Full 镜像（`Dockerfile.gpu` 加 docling extra）
- `381d9f1` **feat(docling)** P2 `VlmPipeline`(GraniteDocling) + `HybridChunker` 结构感知分块
- `37733c2` **revert(docling)** P0.2 镜像内预取回退 → 运行时 `HF_HOME` 持久卷
- `f0a617b` **feat(docling)** P1 表格优化（`TableFormerMode.ACCURATE` + `do_cell_matching`）
- `6c94c0d` **feat(docling)** P0 多格式默认首选 + `artifacts_path` 离线模型预取
- `20aeda2` **feat(docling)** 替代 kreuzberg——Python SDK 库内嵌 + 多 OCR 后端

### 🖥️ Console SQL Worksheet（9）
- `78d2c25` **feat(console)** 增强：查询历史 + EXPLAIN + CodeMirror SQL 高亮 + Dockerfile 打包
- `7a62f0d` **refactor(console)** 移除 stream 模式，消除 apache-arrow CDN 供应链依赖（方案 A）
- `4039da3` **fix(console)** 安全审查修复——XSS + apache-arrow 版本 pin
- `3855b20` **feat(console)** DuckDB SQL Worksheet Web 界面（走产品 OLAP API）
- `97cf45c` **fix(console)** `fillSql` 规范化——去结尾分号 + 多语句取首句
- `7841f01` **fix(console)** dataset 名默认加双引号（兼容含 `-` 的名字）
- `a9a50a8` **fix(console)** 前端双层 auth——请求同时带 X-API-Key + Bearer（BOTH 模式）
- `2c296a3` **fix(api)** `/console` mount 路径 `parent.parent` → `parents[2]`
- `a066da8` / `548dd9d` **fix(api)** api_key_middleware 放行 `/console` + `_JWT_PUBLIC_PREFIXES` 加 `/console`

### 🧠 知识图谱（5）
- `d9dbff7` **fix(kg)** per-dataset 图隔离修复——换 rocksdb 单机 + 每图独立 `data_path`
- `cbe995b` **fix(kg)** `doc_type` 贯通——ingest 自动判定 + classifier 修复 + he 路由
- `64e5366` **fix(kg)** HugeGraph 1.7+ 开 auth 解锁 per-dataset KG + RAG `text_column` 自动探测
- `8cf35d6` **feat(kg)** 升级实体抽取 system prompt，提升建图质量
- `0c1a53b` **fix(kg)** extractor LLM 调用 retry + 指数退避

### 🗄️ 元数据 / Ingest / 部署（3）
- `953102e` **fix(gravitino)** `_request` 异常分类 + retry，fileset/schema 迁官方 SDK
- `28685fa` **fix(ingest)** `blob_key` 容忍 CJK 文件名——非 ASCII 替换为下划线
- `43e62e6` **feat(deploy)** minimal 生产栈 compose + `make prod-minimal` target

### 📚 业务案例 / Cookbook / 测试（4）
- `3256577` **feat(cookbook)** `examples_busi2` 基于当前框架的端到端集成测试
- `e9f370a` **feat(cookbook)** 芜湖城市生命线 v1.8.6 端到端业务案例 + dashboard
- `f5df41d` / `59662e6` **fix(cookbook)** RAG/GraphRAG `_add_vectors` 改用 `read → append_column → restore`
- `fadf2d0` **test(kg)** lock customized step direction + multi-node list cap validation

---

## 关键数字

| 指标 | 数值 |
|------|------|
| 提交数 | 29 |
| 变更文件 | 109 |
| 新增 / 删除 | +11,588 / −349 行 |
| Docling 落地阶段 | 4（P0 → P1 → P2 → GPU） |
| Console 页面 | 17（控制台）+ showcase + narrative |
| KG 修复项 | 5（隔离 / 路由 / auth / 质量 / 韧性） |
| 新增测试文件 | `test_docling_p2.py`(390) · `test_kg_router_acl.py`(26) · 等 |

---

## 代码落点

### Docling 解析链
```
arrow_lake/ingest/document.py        # Docling 配置入口
arrow_lake/ingest/ingestor.py        # 解析编排
arrow_lake/ingest/chunker.py         # HybridChunker 结构感知分块
arrow_lake/ingest/_ingest_files.py   # 多格式分发
arrow_lake/config/document.py        # Docling 配置项
deploy/Dockerfile / Dockerfile.gpu   # 镜像层（含 docling extra）
```

### Console + OLAP
```
console/                             # 18 文件：HTML + assets + src(olap/ui/api/auth)
arrow_lake/api/app.py                # /console 静态挂载 (parents[2])
arrow_lake/api/auth.py / jwt_auth.py # 双层 auth + /console 放行
```

### 知识图谱
```
arrow_lake/knowledge_graph/client.py       # per-dataset data_path
arrow_lake/knowledge_graph/doc_type_router.py  # doc_type 贯通
arrow_lake/knowledge_graph/extractor.py    # LLM retry + 退避 + system prompt
arrow_lake/knowledge_graph/he_extractor.py # he 路由
arrow_lake/cli/kg.py                       # KG CLI
deploy/scripts/entrypoint-hugegraph.sh     # gremlin 绑定修复
```

### 元数据与 RAG
```
arrow_lake/catalog/gravitino_bridge.py  # 官方 SDK + 异常分类 retry
arrow_lake/rag/context.py               # text_column 自动探测
arrow_lake/_lake_ingest.py              # blob_key CJK 容忍
```

### 部署
```
deploy/docker-compose.prod_minimal.yml  # minimal 生产栈
deploy/Makefile                         # prod-minimal target
```

---

## 破坏性变更与升级注意

1. **Docling 替代 kreuzberg**：镜像层依赖变更，需重新 build；CPU 镜像解析大 PDF 极慢，生产用 `Dockerfile.gpu`。
2. **Console 双层 auth（BOTH 模式）**：前端请求须**同时**携带 `X-API-Key` 和 `Bearer` 两个 header，否则 401。
3. **HugeGraph 开 auth**：per-dataset KG 必须开 auth（`PASSWORD` env），切换 auth 需重置集群；drop 图有坑。
4. **`/console` 路径**：JWT 公共前缀需包含 `/console`，否则前端被 JWT 拦截。
5. **Console 改为只读镜像层**：在容器内改前端必须 rebuild（Dockerfile 需 `COPY console`）。

---

## 工程质量声明

- **新增测试**：`tests/unit/ingest/test_docling_p2.py`（390 行，覆盖 P2 VlmPipeline + HybridChunker）、`tests/unit/api/test_kg_router_acl.py`（KG router ACL）、`tests/unit/catalog/test_gravitino_bridge.py`（官方 SDK 迁移回归）、cookbook 端到端集成测试。
- **cookbook 验证**：芜湖 552 页城市生命线 PDF 全链路（ingest → chunk → embed → KG → RAG）跑通，含 5 大踩坑记录归档。
- **前端验证**：Playwright + chromium 像素级对比，非肉眼截图。
- **供应链**：移除 apache-arrow 外部 CDN 依赖，静态资源全部自托管。

---

## 部署速查

```bash
# minimal 生产栈（推荐，精简）
make prod-minimal
# 等价于
docker compose -p arrow-lake -f deploy/docker-compose.prod_minimal.yml up -d api

# 开发模式（Console）
# 开发端口 5189；静态原型验证 5180
```

> WSL2 注意：共享 anchor 镜像只 build 一个服务（BuildKit quirk）；滚动更新 gate 健康检查；API 仅 `127.0.0.1:8000`。

---

## 下一步（v1.8.8 候选）

- **Docling GPU live 验收**：`gpu-full` 镜像构建 + RTX 3090 测速（552 页 PDF 当前卡 CPU 性能）。
- **旗舰前端 P2**：确认字族 / 强调色 / 图标库后进入旗舰页生产化（当前为 mock 静态原型）。
- **narrative 叙事**：当前搁置，待 showcase 验收后决定是否合入。
- **KG 质量**：he build 0 实体 bug 在干净集群上复测（隔离抽取正常 7-14/chunk，bug 疑在提交环节）。
- **版本号同步**：✅ `pyproject.toml` + `arrow_lake/_version.py` 已同步至 `1.8.7`，tag `v1.8.7` 已发布并推送 gitee。

---

*v1.8.7 由 trunk-based 工作流直接提交至 master 并推送 gitee；tag `v1.8.7` 已发布（含领先提交 `13f45f9`、`28685fa` + 版本对齐 commit）。*
