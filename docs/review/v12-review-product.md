# Arrow Lake v1.2.1 产品维度评审报告

## 产品维度评审报告

### 评分: 7.2/10

---

### 一、API 设计质量 (7/10)

**优势:**
- RESTful 命名总体规范，端点分组清晰，15 个路由器涵盖完整业务域
- Pydantic v2 模型全覆盖请求/响应，含字段级验证 (路径穿越防护、SSRF 防护、SQL 注入过滤)
- 统一错误响应格式 (`ErrorResponse`: success + error + message + context)，异常码到 HTTP 状态码的映射完整 (70+ ErrorCode 枚举)
- 响应格式双模式 (`arrow_ipc` / `json`)，大数据量走 Arrow IPC 二进制编码
- OpenAPI 自动文档 + OpenAPI Tags 分组，开箱即用

**待改进:**
- **[HIGH] API 版本割裂** — v1 和 v2 混用，缺乏明确策略。datasets/search/query/export/quality/lineage/audit/backup 在 `/api/v1/`，而 rag/kg/auth/admin 在 `/api/v2/`。同一产品存在两套版本前缀，用户需要猜测端点版本。建议统一为 `/api/v1/` 或制定清晰的版本演进策略。
- **[P1] 缺少全局分页标准化** — `list_datasets` 用 `limit/offset`，但 `audit_query` 和 `lineage_history` 没有分页参数。大型数据集下存在性能和内存风险。
- **[P1] `audit_verify` 用 POST 但无请求体** — `audit_verify(audit_id: str)` 从路径参数取值是 GET 语义，却用 POST 方法。
- **[P1] `backup/restore` 参数传递不一致** — `backup_id` 通过路径参数 (`/{backup_id}`)，但 `list_backups` 用 `/list` 而非 GET `/`。
- **[P2] GraphRAG 返回裸 dict** — `graphrag_query` 端点返回 `dict[str, Any]` 而非 Pydantic 模型，与其他端点的类型安全风格不一致。
- **[P2] 缺少 OpenAPI 示例值** — 请求模型无 `json_schema_extra/examples`，Swagger UI 上用户需要猜测字段格式。

### 二、功能完整性 (8/10)

**优势:**
- **数据摄入**: 覆盖文件/HTTP/图片/视频/PDF 文档/混合模态，6 种摄入端点，支持路径穿越和 SSRF 防护
- **搜索**: 向量搜索/全文搜索/混合搜索(RRF融合)/分面搜索/集成搜索，5 种搜索模式完备
- **查询**: OLAP SQL(DuckDB) + 元数据查询 + Daft DataFrame，SQL 注入防护到位
- **RAG**: 查询 + SSE 流式 + 实体提取 + 模板管理 + 会话历史，支持 4 种 LLM 提供商 (OpenAI/Anthropic/vLLM/Ollama)
- **知识图谱**: 构建(异步任务) + Gremlin 查询 + 邻居遍历 + 统计 + GraphRAG，危险操作黑名单防护
- **数据质量**: 质量过滤 + 报告 + 内容去重 (精确/perceptual/both)，支持 NeMo Curator
- **数据导出**: 异步任务 + 状态查询 + 下载，Parquet/CSV 双格式
- **备份恢复**: 创建/恢复/列表/删除完整生命周期
- **可观测性**: 健康探针(liveness/readiness) + Prometheus metrics + OpenTelemetry + 审计日志(含 HMAC 完整性)

**待改进:**
- **[HIGH] CLI 入口点断裂** — `pyproject.toml` 声明 `arrow-lake = "arrow_lake.cli:main"`，但 `cli.py` 在当前 HEAD 中不存在。`pip install arrow-lake` 后执行 `arrow-lake` 命令会报 `ModuleNotFoundError`。
- **[P1] 集群管理/API 端点缺失** — `AutoscaleConfig` 和 `WorkflowConfig` 有完整配置，但没有 REST API 端点暴露集群状态、伸缩操作。
- **[P1] Admin 端点是占位符** — `GET /api/v2/admin/users` 返回空列表和 "User management not yet implemented"。
- **[P2] 缺少增量摄入 API** — 所有摄入端点均为全量摄入，没有增量/追加 (append) 的显式语义。
- **[P2] 导出不支持筛选** — `ExportRequest` 只有 `columns` 和 `version`，缺少 `where` 过滤条件。
- **[P2] 嵌入端点独立于数据集** — `/api/v1/embed/text` 无法直接将结果写入数据集。

### 三、用户体验 (7/10)

**优势:**
- **配置系统设计精良** — 4 层优先级，25+ 配置节覆盖所有子系统
- **文档覆盖广** — 13 篇 Cookbook 约 9600 行
- **错误信息分级清晰** — 异常层次 (19 个子类)、ErrorCode、message、敏感信息过滤

**待改进:**
- **[HIGH] CLI 已删除但文档引用仍在**
- **[P1] 缺少 `.env.example` 模板**
- **[P1] 无生产配置示例**
- **[P2] 质量过滤器名称无枚举约束**
- **[P2] SQL 注入防护为白名单而非参数化**

### 四、版本管理与兼容性 (6.5/10)

**优势:**
- 语义化版本 + CHANGELOG.md + 版本 API + FastAPI OpenAPI 版本

**待改进:**
- **[HIGH] API 版本无演进策略**
- **[P1] 无弃用机制**
- **[P2] pyproject.toml 版本与 CHANGELOG 不一致**
- **[P2] 配置节动态扩展无版本控制**

### 五、总体优势总结

1. 架构设计成熟
2. 安全防护深入 (SSRF / SQL 注入 / 路径遍历 / Gremlin 注入 / JWT 消息脱敏 / HMAC 审计完整性)
3. 多模态覆盖完整
4. 可观测性体系完备
5. 测试基础设施健全 (1673+ 测试、82%+ 覆盖率)

### 六、待改进汇总

| 级别 | 问题 | 影响 |
|------|------|------|
| **HIGH** | CLI 入口点断裂 | pip install 后命令不可用 |
| **HIGH** | API 版本割裂 | 用户无法预测端点版本 |
| **HIGH** | CHANGELOG 与 pyproject.toml 版本不一致 | 安装包版本与文档不匹配 |
| **P1** | 缺少 .env.example 和生产 YAML 示例 | 新用户配置体验差 |
| **P1** | Admin 端点是占位符 | 暴露未实现接口 |
| **P1** | 分页/过滤未标准化 | 大数据量下有内存风险 |
| **P1** | 集群管理无 API 端点 | 配置存在但无法通过 API 操作 |
| **P1** | 审计验证端点方法语义错误 | POST 无 body 不符合 REST 规范 |
| **P1** | GraphRAG 端点返回裸 dict | 类型安全风格不统一 |
| **P2** | 无 API 弃用机制 | 未来变更缺乏平滑过渡 |
| **P2** | 质量过滤器名称无枚举约束 | 用户需猜测有效值 |
| **P2** | 导出缺少 where 过滤 | 无法导出数据子集 |
| **P2** | 嵌入计算与数据集写入未打通 | 需两步操作 |

**关键结论**: 架构设计、安全防护和功能广度达到生产级水准。最紧迫的 3 个问题: (1) 修复 CLI 入口点断裂 (2) 统一 API 版本策略 (3) 同步版本号。
