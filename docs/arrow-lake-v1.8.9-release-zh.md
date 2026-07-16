# Arrow Lake v1.8.9 发布说明

> 2026-07-16 · tag `v1.8.9`（commit `a3d64b0`）· 自 v1.8.8 以来 **17 commits / 46 文件（+2258/−233）**
>
> 本版聚焦三件事：**RAG 重排器回归可用 + 安全加固**、**KG 双阶段 LLM + 增量 KA**、以及一轮覆盖 **P0 缺陷 + 内容哈希 + 解析/查询缓存 + 性能杂项** 的架构/缺陷/性能审计。

---

## 1. RAG 重排器（Reranker）

v1.8.8 之前重排器实际**未生效**——`_lake_rag` 没把 reranker 透传给检索管线，恒为 `Noop`。

- **新增 `OllamaReranker`**（`arrow_lake/rag/reranker.py`）：基于 Qwen3-Reranker 的 yes/no 判官重排，本地 ollama 推理（`dengcao/Qwen3-Reranker-0.6B:F16`）。
- **设为默认**：`RAGConfig.reranker="ollama"`、`reranker_model="dengcao/Qwen3-Reranker-0.6B:F16"`、`reranker_top_n=10`。reranker 基类族现为 `BaseReranker / NoopReranker / CrossEncoderReranker / LLMReranker / OllamaReranker`。
- **三连缺陷修复**：死配置（透传缺失）/ `LLMReranker` async-sync 错配崩溃 / `_parse_score("10"→1)` 反转。
- **安全加固**：`LLMReranker` 加 SSRF scheme 校验（仅允许 http/https）+ prompt-injection 过滤。

## 2. KG 双阶段 LLM + 增量 KA

- **双阶段 LLM 拆分**：抽取阶段（`HugeGraphConfig.he_extract_llm`，默认 ministral，轻量快）与问答阶段（`he_qa_llm`，默认 deepseek-v3@百炼，强推理 + 中文生成）独立配置。抽取要结构化输出（`.parse()`），问答要生成质量，两者对模型诉求不同。
- **增量 KA/KG**：`build_dataset_ka` 支持增量——`fed_chunks` 改记内容哈希 sidecar，只喂**新/变更** chunk，未变 chunk 复用既有实体（KG 写入幂等 upsert）。REST + CLI `--incremental` 暴露。
- **KA 版本管理**：每次 kg_build 前归档当前 dump（`archive/list/rollback/prune`）；v1.8.9 起归档**跳过可重建的 FAISS `index/`**（仅 data.json + metadata.json），查询路径 `_ensure_ka_index` 缺失自动重建。

## 3. Ingest 多格式

- **`/ingest/documents`** 放开**全部 kreuzberg 文档类型**（PDF/DOCX/PPTX/XLSX/HTML/MD/邮件/图片…，非仅 PDF），并支持 **append 到已存数据集**。

## 4. 架构 / 缺陷 / 性能审计（P0 + Step2~4 + P2）

**P0 三连（真 bug）：**
- **stderr 永久泄漏**：`_suppress_tesseract_noise` 的恢复行是 `dup2(fd,fd)` no-op，且未 `dup` 保存原 fd → 首次 kreuzberg 解析后进程 stderr 永久→/dev/null，长跑容器所有 traceback 静默丢失。
- **KG 默认模板改 strict**：default/paper/report 指向项目本地 `concept_graph.yaml`（type/relation 枚举 + **definition required**），弃 gallery `general/concept_graph` 自由类型。实测定义覆盖 **0%→100%**、类型噪声 80+→干净枚举。
- **type-enum 竞态**：`_current_type_enum` 在 `extract_batch` 的 gather 下被并发覆盖 → 改局部显式传递。

**Step2（append 漏刷新派生结构）：**
- FTS jieba 新行 NULL → 检测 `_fts_segmented` 列 NULL 自动重建索引；`_has_null_segmented` 兼容 LanceDB Table API（原只认 pyarrow，live 永不触发）。
- OLAP 查询缓存 + facets CUBE 结果缓存，ingest 后 `invalidate_dataset` 失效。

**Step3（内容哈希三连）：**
- `doc_id` 用文件**内容**哈希（非路径）→ 重命名/重路径不再产生重复行。
- `fed_chunks` 记内容哈希 → 行数不变内容变时可检测（增量基石）。
- 解析内容哈希 LRU 缓存（进程级，32 条）→ re-ingest 未改文件跳过重解析 + 重 OCR。

**Step4-B：** `feed_text` 退避重试（3 次，1s/2s 指数）——防大语料 LLM 瞬时失败/限流静默丢 chunk。

**P2 杂项：**
- `max_tokens` 走 `cfg.max_tokens`（原硬编码 8192 使 env 失效）。
- 向量 SQL `query_vector` finite-float 校验（闭裸插值）。
- docling `DocumentConverter` **进程级单例**（按 config 签名 key + 每 converter RLock）→ 省每请求 10-30s 模型重载。
- IVF `nprobes` clamp 到 `[1, min(max_nprobes, num_partitions)]`，`max_nprobes` 配置生效。
- 移除 `_normalize_type` 死代码（生产从未调用，dict 仅英文 key 会塌缩中文 type）。

## 5. 测试

- `kg / query / ingest / rag` 全绿；新增大量 TDD 测试（向量校验 / docling 单例 / nprobes / reranker / KA 版本 / 内容哈希 等）。
- 芜湖 552 页 PDF 全量 E2E（ministral-3:3b，1202 chunks / 18045 顶点 / 21600 边）作为 KG 质量回归基线。

## 6. 升级 / 部署备注

- **版本号**：`pyproject.toml` / `arrow_lake/_version.py` / `deploy/docker-compose.prod_minimal.yml` 均已 bump 至 `1.8.9`。
- **部署**：compose 现指向镜像 `arrow-lake:1.8.9`；首次 `up -d api` 前需 `docker compose ... build api` 构建该 tag。
- **reranker 默认变更**：升级后默认启用 ollama reranker；若 ollama 端点不可达会 latch 回 `Noop`（不阻塞检索）。如需关闭，设 `ARROW_LAKE__RAG__RERANKER=none`。
- **KG 模板默认变更**：无需手动配置即享 strict 模板质量提升；显式设 `he_default_template="general/concept_graph"` 会**回退**到 0% 定义覆盖，不建议。
