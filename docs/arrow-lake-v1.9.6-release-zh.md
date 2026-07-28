# Arrow Lake v1.9.6 发布说明

> 发布日期:2026-07-28 · 上一版:v1.9.5
> 本版主题:**RAG 质量 + KG 质量/性能 + 治理兑现 + 架构 refactor + 安全加固**

v1.9.6 是 v1.9.x 系列的治理与质量收口版本:把 RAG 从"能答"提升到"可溯源防幻觉",把 KG 抽取噪声压下去、查询延迟砍半,兑现血缘可视化与 masking 治理两个旗舰治理功能,并顺带完成一批架构 refactor 与安全加固(fail-closed + 注入防护)。

---

## 一、RAG 质量闭环(批1)

### P0-1 防幻觉(faithfulness verify)
生成后校验答案每句是否被检索上下文支撑,标注 `unsupported`,返回 `support_ratio`。
- 轻量版(embedding cosine,默认):复用 extract encoder,阈值可配 `verification_threshold`(默认 0.6)。
- LLM judge 版(opt-in):逐句 vs context 单次 LLM 调用。
- 开关:`rag.enable_verification`(默认关,opt-in)。

### P0-2 cross-encoder reranker
默认从 Ollama binary 切到 **bge-reranker-v2-m3**(连续分,排序质量提升)。
- `reranker_device`(auto/cpu/cuda)+ `reranker_warmup_on_init`(启动预热)。
- HF_HOME 预下载避免运行时拉模型。

### P1-9 流式补帧
流式 QA 首帧带 `citations`,末帧带 `latency`/`verification`,前端流式可溯源。

---

## 二、KG 质量 + 性能(批2)

### P0-3 KG 抽取质量
- **snap 编辑距离归一化**:噪声类型(「架构组件」→「组件」)snap 到最近枚举。
- **strict 过滤**:空 definition 实体不入图(`he_strict_definition` 开关)。
- **enum 解析**:正则解析模板 description「之一:A/B/C」取合法枚举(向后兼容回退)。

### P0-4 GraphRAG 性能
- **三路并行**:`_graphrag_retrieve` 改 `asyncio.gather`(vector + search_ka + neighbor),延迟 -40~50%。
- **KA LRU 缓存**:`load_ka_for_query` 按 dump mtime 失效,大图省 ~60s/次重建。
- **QuestionEntityCache monotonic**:防 NTP 时钟跳变致 TTL 批量失效。

---

## 三、治理兑现(批3)

### P0-5 血缘可视化(lineage.html)
- 新 `console/lineage.html`:vis-network 渲染(按 target/source/derived 着色)+ 数据集选择器 + 节点点击查 `/lineage/history` 展示**列级血缘**。
- `trace_full_graph` 加 `max_nodes`(默认 500)截断 + `stats.truncated`,防大图 OOM。
- `LineageEvent.column_lineage` 端到端打通(`/history` 返结构化 dict,非 repr 字符串)。

### P0-6 masking 治理
- **4 函数暴露**:`redact`/`hash`/`partial`/`nullify`(policy 层 + governance 下拉)。
- **HMAC fail-fast**:缺 `ARROW_LAKE__MASKING__HMAC_KEY` 启动阻断;`ALLOW_MISSING_KEY=1` opt-in 降级。
- **mask-preview**:`POST /datasets/{name}/quality/mask-preview` 读前 5 行返 before/after。
- **audit**:复用 Lance `audit_record`(零新表)。

---

## 四、架构评审 refactor(并行会话)

- **#1** RAG 检索阶段收口 `RAGQueryPlan` + score 列(等价 `resolve_score_column`)。
- **#4** `ingest_documents_and_index` 收口 facade(parse→store→embed→FTS→vector 一条龙,SDK=HTTP 对齐)。
- **#6** GraphRAG 改用模板方法钩子(`_extra_context_task`),`super().query()` 单一模板。
- **#7** reranker 统一 async 契约(`_retrieve_ranked` 抽取)。

---

## 五、安全加固(净大幅正向)

| 类别 | 加固 |
|---|---|
| masking | HMAC fail-fast + opt-in;hash 128 位([:32]) |
| RBAC | `_apply_masking`/`_apply_row_filter`/`_fetch_rules` 全 **fail-closed**(空表/raise,不泄露未脱敏) |
| mask-preview | 列名 SQL 注入修复(标识符白名单)+ ADMIN 收紧(防绕 column ACL) |
| lineage | XSS esc(vis title + DOT/Mermaid 渲染转义 + `/record` source_datasets 校验) |
| Gravitino | 写端点 POST body 去 `?body=` query param(PII 不入 URL 日志) |

⚠️ **生产部署必配 `ARROW_LAKE__MASKING__HMAC_KEY`**(fail-fast 否则阻断启动)。

---

## 升级

```bash
# 镜像
docker compose --project-directory deploy -p arrow-lake \
  -f deploy/docker-compose.prod_minimal.yml build api  # → arrow-lake:1.9.6

# 必配 env(deploy/.env 或 compose environment)
ARROW_LAKE__MASKING__HMAC_KEY=<your-secret-key>

# 起
docker compose --project-directory deploy -p arrow-lake \
  -f deploy/docker-compose.prod_minimal.yml up -d
```

- 批1/2/3 测试全绿(批1 RAG / 批2 KG / 批3 治理 + 安全),零回归。
- 集成测试:lineage_store / quality_pipeline 过;audit_trail 1 fail = pre-existing(audit HMAC key 环境)。
- 详见 `docs/v1.9.6-impl-plan.md`(验收全 ✅)、`docs/v1.9.6-batch3-impl-plan.md`、`CHANGELOG.md`。
