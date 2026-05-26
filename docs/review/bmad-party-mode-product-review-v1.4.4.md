# BMAD Party Mode — Arrow Lake v1.4.4 Product Review

**Date:** 2026-05-26
**Mode:** Roundtable (6 agents, all spawned as independent subagents)
**Scope:** Product, architecture, documentation, DX, testing, business strategy

---

## 📊 Mary — Business Analyst

好，让我从商业分析师的角度，用数据说话。

### 一、市场定位 — 你的"谁"和"为什么"

**核心发现：Arrow Lake 试图同时服务三类用户，但价值主张的清晰度不够。**

| 用户画像 | 核心需求 | Arrow Lake 匹配度 |
|---------|---------|------------------|
| **AI/ML 工程师** | 快速构建 RAG pipeline、向量检索、多模型编排 | ★★★★★ 高度匹配 |
| **数据工程师** | 数据管道、ETL、质量门控、血缘追踪 | ★★★★☆ 匹配但差异化不足 |
| **平台/DevOps** | K8s 部署、可观测性、多租户治理 | ★★★☆☆ 功能有但不是核心卖点 |

**建议：** 砍掉模糊的定位，做一个果断的选择。从 v1.4.4 的功能演进看（RAG 质量飞跃 + CLI 性能），产品重心已经明显偏向 **AI/ML 工程师**。那就大胆承认：

> "Arrow Lake — 为 AI/ML 团队打造的一站式数据湖仓，从原始文档到生产级 RAG，5 分钟上线。"

数据管道、血缘、治理是**支撑层**，不是**卖点层**。营销和文档应该围绕 RAG pipeline 展开，其他能力作为"企业级保障"出现。

### 二、竞争优势分析 — 真正的护城河在哪里？

对比同类产品（Milvus、Weaviate、LlamaIndex TPS、Haystack、LangChain/LangGraph）：

**Arrow Lake 的差异化不是某个单点功能，而是"全栈垂直整合"。**

```
竞品 X：向量数据库（只管存储）
竞品 Y：RAG 框架（只管编排）
竞品 Z：数据管道（只管 ETL）
    ↓
Arrow Lake = 数据管道 + 向量/全文/混合检索 + RAG 编排 + 治理 + 部署
            = 从 PDF 到 Answer 的完整闭环，一个 Helm Chart 部完
```

**建议：** 在 README 首屏放一个架构全景图，让潜在用户在 10 秒内理解"为什么选你不选别人"。

### 三、利益相关方价值映射

| 利益相关方 | 核心价值 | 当前 Gap |
|-----------|---------|----------|
| **CTO/VP Engineering** | 降低 AI 基础设施复杂度，减少工具链碎片化 | 缺少 ROI 计算器/TCO 对比文档 |
| **AI 工程师** | 快速 prototype → production 的平滑路径 | CLI 文档分散，onboarding 不够丝滑 |
| **数据治理团队** | 统一元数据、血缘追踪、合规审计 | Gravitino 集成刚完成，实战案例为零 |
| **安全团队** | RBAC、审计、注入防护 | 安全特性 buried 在 changelog 里，没有独立 security 白皮书 |
| **DevOps/SRE** | Helm 一键部署、OTel 全链路可观测 | 缺少生产环境 sizing guide 和容量规划建议 |

### 四、商业模式和 GTM 建议

**定价策略：** MIT 开源 + 商业版双轨模式：

- **Community（免费）：** 核心引擎、CLI、REST API、单集群
- **Enterprise（付费）：** 多租户 RBAC、Gravitino 元数据治理、SSO/SAML、SLA 保障

**GTM 路径：**

1. **Now（v1.4.x）：** 找 3-5 个 design partner（中型 AI 团队），免费部署 + 深度支持
2. **Next（v1.5）：** 发布 production case study
3. **Later（v1.6+）：** 正式推出 Enterprise tier

**生态合作机会：** Gravitino (Datastrato)、NeMo Curator (NVIDIA Inception Program)、Ray (Anyscale ecosystem partner)

### 五、两个核心业务风险

**风险 1：功能蔓延导致"瑞士军刀"困境**

> 如果什么都能做，用户就不知道你最好做什么。

建议：未来两个版本严格遵循 **"一个 Hero Feature + 配套改进"** 模式。

**风险 2：文档债务正在累积**

- README 有 4 个版本，维护成本高且容易不一致
- Cookbook 只有 1 篇
- 没有 API reference（Swagger/OpenAPI）

### 六、优先行动建议（按 ROI 排序）

1. **重写 README** — 10 秒电梯演讲 + 架构图 + 3 分钟 quickstart
2. **补 2-3 个 production case study**
3. **发布 API reference** — 40+ endpoints 没有 Swagger 文档是不可接受的
4. **写一份 Security Whitepaper**
5. **找 design partners** — 3-5 个真实用户

**一句话总结：Arrow Lake 的产品力已经到了"技术上准备好了"的阶段，但"商业上准备好了"还差临门一脚 — 那就是讲好故事、找到声音、拿到验证。**

---

## 📚 Paige — Technical Writer

### 做得好的地方

1. **双语 README 结构清晰** — README.en.md 和 README.zh.md 并行维护，CHANGELOG.md 版本纪事清晰
2. **文档分层有意识** — docs/ 下 quickstart、usage-guide、cookbook、design_plan 四层结构

### 需要改进的地方

#### 严重度：HIGH — 缺失的文档直接挡路

**3. API Reference 是最大的缺口**

40+ REST 端点没有独立的、可机器读取的 API 文档。

具体建议：
- 补充 OpenAPI 3.1 规范文件（`openapi.yaml`），放在 `docs/api/` 下
- 用 spec 自动生成可交互的文档页面（Redoc 或 Scalar）
- 每个 endpoint 必须包含：请求/响应示例、错误码表、认证说明
- 考虑集成到 CI 里做 contract testing

> 40+ 个 API 端点没有 OpenAPI spec，就像一栋 40 间房的大楼没有门牌号。

**4. CLI 参考文档缺失**

CLI 有 15 个命令组，用户需要：
- 每个命令的用途说明
- 完整的参数表
- 实际使用场景的示例

建议用 `sphinx-click` 或类似工具从代码自动生成。

**5. 配置参考是"藏"在代码里的**

建议创建 `docs/reference/configuration.md`，表格列出每个配置项。

#### 严重度：MEDIUM — 信息架构可以更优雅

**6. Cookbook 还太"薄"**

建议补充（按优先级）：RAG 端到端流水线、混合搜索调参指南、知识图谱构建实战、数据质量门禁配置、多租户 RBAC 配置

**7. 架构图需要可视化**

用 Mermaid 或 Excalidraw 画一张组件关系图。

**8. 文档间的交叉引用不足**

各文档之间缺少明确的链接导航。

### 优先级总结

| 优先级 | 行动项 | 预估工作量 |
|--------|--------|-----------|
| P0 | OpenAPI 3.1 spec + 自动文档生成 | 3-5 天 |
| P0 | 配置参考文档 | 1 天 |
| P1 | CLI 参考文档（自动生成） | 1-2 天 |
| P1 | 架构可视化图 | 0.5 天 |
| P1 | Cookbook 扩充（5+ 篇） | 3-5 天 |
| P2 | 文档交叉引用和导航优化 | 1 天 |
| P3 | CONTRIBUTING.md | 0.5 天 |

---

## 📋 John — Product Manager

### 一、最大的问题：你到底在卖给谁？

v1.0 到 v1.4.4 密集发了 16 个版本，每版都在加能力。但：

**谁会因为缺少 Arrow Lake 而痛苦？那个痛苦的 Job-to-be-Done 具体是什么？**

- ML 工程师需要 vector search + RAG？直接用 LangChain/LlamaIndex + Pinecone/Weaviate
- 数据工程师需要 OLAP？直接用 DuckDB + dbt
- 平台团队需要元数据治理？直接用 Gravitino + Atlas

"All-in-One" 在产品史上是一个危险信号 — 每一条战线都在打，但没有一条战线有压倒性的优势。

### 二、价值主张自相矛盾

README 说 "No Docker. No config files. From pip install to first result in under a minute."

但 `ray[default]` 一个依赖就要拉几百 MB，`sentence-transformers` 要下模型。**"一分钟" 这个 promise 在光网环境下都不一定能兑现。**

### 三、两个产品挤在一个 README 里

- **Local-first SDK** (pip install, 本地开发)
- **Platform** (K8s deployment, 分布式, 企业级治理)

建议：明确分两条 Product Line。

### 四、Documentation 质量参差不齐

- `quickstart.md` 里用了 `lake._get_storage()` — 用 private API 做教学是 code smell
- `usage-guide.md` 里去重示例与实际数据不一致

### 五、CHANGELOG 节奏暴露产品节奏问题

v1.4.0 到 v1.4.4：16 天内发了 5 个版本 — **feature creep 的经典症状。**

### 六、竞争定位的致命问题

说 "multimodal data lakehouse"，但 audio/video 处理在 quickstart 和 cookbook 里完全没出现。

**不要 claim 你做不到的事。**

### 建设性建议 (按优先级)

1. **确定 ONE target persona**
2. **修复 PyPI 发布**
3. **统一 README 的 narrative**
4. **重写 quickstart** — 不用 private API
5. **multimodal 的 claim 要么兑现要么删除**
6. **放慢 feature 节奏** — v1.5 定为 "polish release"
7. **做 benchmark** — 和竞品用数据说话

**最后一个问题：你的第一个真实用户是谁？他在用什么替代方案？他抱怨最多的三件事是什么？**

---

## 🎨 Sally — UX Designer

### 1. 首次体验：demo 跑完之后呢？

demo 结束时输出 "next steps" 面板：
- `arrow-lake init ./my_lake --template=rag`
- `arrow-lake connect --source=postgres://...`
- `arrow-lake import ./data/ --format=auto`

### 2. SDK 设计：8 个 Mixin = 8 扇门

没有任何一个门上写着"先走这扇"。

**建议：**
- `lake.status()` 交互式欢迎信息
- `Lake.wizard()` 用 3-5 个问题引导用户
- Mixin 文档按使用频率排序

### 3. CLI 的 15 个命令组：可发现性问题

**建议：**
- `arrow-lake find "embedding"` 模糊匹配
- 裸命令直接展示最常用 5 个
- `arrow-lake debug` 交互式诊断

### 4. 错误体验

建议错误翻译层：
- 原始：`daft.exceptions.ColumnNotFoundError: Column 'embedding_vector' not found`
- 翻译后：`❌ Column 'embedding_vector' doesn't exist. Available: [id, text, metadata]. Did you mean 'embedding'?`

### 5. REST API 可学性

- API Journey Maps — 3-5 个常见用户旅程
- Swagger tag 按用户目标分组
- 提供 Postman collection

### 6. 渐进式披露

引入复杂度分级：
- 🟢 Starter: 本地单机
- 🟡 Professional: Docker Compose
- 🔴 Enterprise: K8s Helm

### 7. 退出体验

提供 `arrow-lake export --format=lance|parquet|jsonl --include-metadata`。让离开变得容易，用户反而更信任你。

**一句话总结：Arrow Lake 的功能深度已经很出色了，现在的瓶颈不是"能不能做"，而是"用户能不能发现和学会"。**

---

## 🏗️ Winston — System Architect

### 一、依赖风险 — 最大隐患

**daft 0.7.8 + ray 2.54.1 + metaflow 2.19.22** 三者共存意味着同时引入三个大规模分布式计算框架。调度模型、资源管理、Python 版本约束会互相打架。

**建议：** 明确每个框架的职责边界。如果有重叠，选一个砍掉另一个。

**lancedb 0.30.2** — API 稳定性还不够成熟，要做好升级时的数据迁移测试。

### 二、架构分层

**好的部分：** Client → Gateway → REST API 链路标准。Middleware 层独立于业务逻辑。SDK Facade + 8 Mixins 灵活。

**需要关注：**

1. **15 个 Router** — API 表面积大。做访问热力图分析，冷门 endpoint 考虑合并。
2. **LLM Provider 抽象** — 五家 provider 行为不对称。建议加 **model capability registry**。
3. **Redis 单点** — session/JWT/semaphore 都压在 Redis 上。Redis 挂了，降级策略是什么？

### 三、单点故障

1. **LanceDB 冷启动** — K8s HPA 扩缩容时，新 pod 加载索引的 P99 延迟影响？
2. **HugeGraph criticality** — RAG pipeline 对它是 hard 还是 soft dependency？
3. **OTel** — 有没有定义 SLO？可观测性不是装了 agent 就完了。

### 四、数据一致性

- LanceDB + DuckDB 之间的一致性延迟窗口怎么处理？
- 跨存储数据生命周期管理 — 原始文件清理后引用会不会变悬空指针？

### 优先级排序

| 优先级 | 建议 | 理由 |
|--------|------|------|
| P0 | 绘制依赖升级兼容性矩阵 | 三大框架共存，升级风险最高 |
| P0 | 定义核心 SLO + 降级策略 | 生产环境必须有 |
| P1 | Router 流量热力图分析 | 收缩 API 攻击面 |
| P1 | 外部依赖 criticality 分级 | 明确挂了怎么办 |
| P2 | LanceDB 冷启动性能基准 | 影响 K8s 扩缩容 |
| P2 | 跨存储一致性方案 | DuckDB + LanceDB 同步延迟 |

---

## 💻 Amelia — Senior Software Engineer

### 一、测试策略

2872 tests, 80% coverage。数字好看，但：

1. **测试分层清晰度** — unit / integration / e2e 各占多少？建议 CI 报告拆分三维度独立出覆盖率。
2. **RAG 引擎测试** — Reranking pipeline、HyDE/MultiQuery 需要固定 seed 确定性测试 + Mock LLM contract test + 端到端 snapshot test。
3. **Circuit breaker 测试** — 半开状态恢复路径、并发状态机正确性、与 Redis semaphore 竞态测试。

### 二、代码质量

4. **Facade pattern 膨胀风险** — `Lake` + 8 Mixins，每个 Mixin > 200 行就是上帝对象。建议接口抽成 Protocol（PEP 544）。
5. **Pydantic v2 一致性** — 检查 v1 `@validator` 残留，必须零残留。
6. **结构化日志测试** — structlog 输出格式需要在测试中 assert。

### 三、性能瓶颈

7. **Connection pool leak test** — DuckDB / Lance / Redis 连接池泄漏测试，生产 bug 高发区。
8. **LRU cache** — eviction 有没有 metric？内存水位线有没有保护？
9. **Auto-maintenance scheduler** — 调度并发冲突、graceful shutdown、失败 retry with backoff 测试覆盖。

### 四、CI/CD

10. 分层 coverage gate：unit >= 90%, integration >= 80%, e2e >= 60%
11. 2872 tests 跑完多久？> 5 min 就需要 `pytest-xdist` 并行 + `--durations=20` 优化。

### 优先级排序

| 优先级 | 项目 | 理由 |
|--------|------|------|
| P0 | Connection pool leak test | 生产 OOM 零容忍 |
| P0 | Circuit breaker 并发测试 | 弹性核心，bug 直接级联 |
| P1 | RAG contract/snapshot test | 核心业务质量 |
| P1 | Coverage 分层 gate | 80% 总量掩盖低质量区域 |
| P2 | Pydantic v2 残留清理 | 技术债 |
| P2 | Test execution 优化 | 影响迭代速度 |

**一句话总结：2872 tests 是好的起点，但数量不等于质量。下一步：分层覆盖率 + 核心路径 contract test + 资源泄漏防护。**

---

## Orchestrator Summary — Consensus & Tensions

### 共识（所有 agent 几乎一致）

1. **文档是最大短板** — 缺 OpenAPI spec、CLI reference、Cookbook 不够
2. **产品定位不够锐利** — "All-in-One" 叙事需要收窄到 AI/ML 工程师的 RAG 场景
3. **Feature 节奏太快** — v1.5 应该是 polish release
4. **PyPI 发布状态** — 需要验证包是否已发布

### 分歧（有价值的张力）

- **John vs Mary** — John 说"先确定 ONE persona 再做任何事"，Mary 说"先找 design partner 再验证 persona"
- **Winston vs Amelia** — 依赖风险 vs 测试质量，是一枚硬币的两面

### Suggested Next Steps

- 让 Mary 和 John 就"先定位还是先找用户"辩一轮
- 让 Winston 和 Amelia 就"依赖风险 vs 测试覆盖"深入讨论
- 让 Sally 展开 `Lake.wizard()` 具体设计

---

## Round 2: 定位辩论 — Mary vs John (先定位还是先找用户)

### 📊 Mary 的立场：先找用户，用数据收敛定位

John,你的三个论点都指向同一个假设——**"定位 = 确定性"**。但你手里的证据恰恰不支持这个假设。

**第一，没有 talk track 怎么聊？**

好的用户访谈不需要 polished 的 talk track——它需要一个好的问题清单：

- "你处理 multimodal 数据时，最让你痛苦的一个环节是什么？"
- "上一次你想把 embedding 和结构化数据 join 在一起查，你实际做了什么？"
- "如果有一个工具能解决你 80% 的问题，你愿意放弃什么？"

**你不是没有 talk track，你是把"不确定性"当成了"没准备好"。**

**第二，砍功能需要证据，证据来自用户。**

**第三，如果先定位错了呢？** 先定位的沉没成本远大于先访谈的沉没成本。

**核心论点：在信息不足的情况下，先收集信息的期望值永远高于先做决策。** 这是贝叶斯推理。

### 📋 John 的立场：先定位，再用用户验证

Mary，你说"定位是在用户对话中发现的"——**发现的不是定位，是验证。** 定位是假设，用户对话是验证手段。没有假设就出门聊天，聊回来的不是 insight，是噪音。

你说"有目标地选 2-3 个候选 persona 的代表"——**这不就是我说的"先定位"吗？**

Porter 的五力分析第一步是什么？**定义行业边界和竞争范围。**

**一个粗糙但有争议的定位假设，比零定位加十场散聊值钱一百倍。** 验证是修正假设，不是生成假设。

### 辩论收敛点

两人的分歧不是"要不要定位"，而是**定位的精度要求**：
- John：要一个明确的、有争议的 positioning hypothesis，再出门验证
- Mary：要一个松散的候选集，用访谈来收敛

**折中方案：** 花半天写一个 one-page positioning hypothesis 作为访谈框架，但保持它足够宽泛可以被推翻，然后立刻出门找 5 个用户。

---

## Round 3: 产品愿景澄清后的重新评估

**用户澄清了产品愿景：**
> arrow-lake 的主要目标就是打造一个端到端的统一的多模态数据湖平台，支撑 AI 以及传统业务数据全生命周期管理，提供 RAG、知识图谱等前沿的 AI 服务能力，打造企业生产级的数据湖平台+知识工程。

**核心变化：** "全"不是 feature creep，是设计意图本身。Arrow Lake 是一个 platform play，不是 point solution。

### 📊 Mary — 认知修正

收回"功能蔓延"批评。但转移到三个精准问题：

1. **认知锚定** — 用户能在 3 秒内说出 Arrow Lake 是什么吗？Databricks 起步是 Spark 即服务，Snowflake 起步是云数仓。Arrow Lake 的切入叙事是什么？
2. **竞品对标升级** — 定位企业级全栈平台意味着对标 Databricks、Snowflake 这一级。SLA、SSO/SAML、合规认证的成熟度够吗？
3. **平台边界** — 自建 vs 集成的分界线用户能感知到吗？

**新机会：** "中小企业的数据湖 + AI 平台一体化方案"——大厂有拼图但中小团队买不起。Arrow Lake 在一个部署单元里交付数据湖 + RAG + 知识图谱 + 治理，全球几乎没有直接竞品。

### 📋 John — 三个追问

承认之前用 point solution 的尺度量 platform play，尺子拿错了。但：

1. **"统一"的边界怎么划？** "传统业务数据"范围极大，边界不划清楚变成"什么都沾一点什么都不深"。
2. **v1.4.4 用户心智里 Arrow Lake 等于什么？** Snowflake 早期三词：simple, powerful, pay-as-you-go。Arrow Lake 的三个词？
3. **Adoption barrier。** 从零到用起来需要几步？落地门槛会把 90% 潜在用户挡在门外。

**核心质问：这个 vision 在 v1.4.4 变成了什么可以交付的用户价值？**

### 🏗️ Winston — 架构评估

平台 vs 工具，架构根本区别：

| | 点工具 | 真正的平台 |
|---|---|---|
| 核心假设 | 一个二进制解决所有问题 | 清晰内核 + 可插拔扩展 |
| 失败模式 | 功能膨胀 | 集成复杂度爆炸 |
| 成熟度度量 | 功能数量 | 集成面清晰度 + 扩展便利性 |

**三处需要加固：**

1. **内核-壳边界不够清晰** — 建议划分 Kernel Layer（LanceDB + 存储抽象）→ Service Layer（RAG、图谱、SQL）→ Application Layer（CLI、SDK、API Gateway），每层只依赖下一层接口。
2. **知识工程需要独立的能力平面** — 定义 Knowledge Plane 和 Data Plane 平行，通过 Gravitino 关联但不互相依赖内部实现。
3. **运维复杂度是最大架构决策** — 用户能不能只用 Data Plane 不用 Knowledge Plane？能不能替换 LanceDB？

**v1.5 建议：不加新功能，让已有能力体系化——画清层级边界，标准化扩展点。**

### Round 3 共识

三位 agent 形成新共识：方向正确，需要从"堆功能"转向"画边界"。

- **Mary** 关注 GTM：中小企业一体化方案是独特位置，需要锐利切入叙事
- **John** 关注交付：vision 之下，v1.4.4 的用户到底能用什么？
- **Winston** 关注架构：内核-壳边界、Knowledge Plane 独立化、扩展点标准化

三人指向同一个 v1.5 方向：**不是加功能，而是体系化现有能力——架构分层 + 文档 + 用户旅程。**

---

## Round 4: 平台 DX / 测试 / 文档 — Sally + Amelia + Paige

### 🎨 Sally — 平台级渐进披露

**从"工具感"到"平台感"——体验范式需要根本转变。** 平台用户的第一反应是"我该从哪里开始？"——这是焦虑，不是好奇。

**场景分级（替代功能分级）：**

- **🟢 "第一次约会"** — 零配置完成第一个有意义场景，预选最短路径。底下仍然跑完整三层架构，但用户不需要知道。
- **🟡 "磨合期"** — 场景模板库 + 渐进式控制面板 + "刚才发生了什么"回顾
- **🔴 "长期关系"** — 治理全景图、多团队工作空间、CTO 级成本仪表盘

**三个 UX 模式：**

1. **场景为入口** — 导航按用户目标（查找信息 / 建设知识库 / 连接数据源 / 治理），不是按技术模块
2. **"推荐下一步"引擎** — 预定义决策树，可测试
3. **"透明度旋钮"** — 最小/标准/最大三档，同一套功能的不同展示粒度

### 💻 Amelia — 双平面正交测试

**平台化对测试的三条硬要求：**

1. **双平面正交** — Knowledge Plane 和 Data Plane 测试不交叉依赖，CI 检测跨平面引用
2. **层间合约测试** — 每对相邻层必须有 interface contract test
3. **覆盖率分层达标** — Kernel 95% / Service 90% / Application 80%（集成）/ 60%（E2E）

**当前缺口：**
- `storage.py` 回归测试 — P0
- CLI 集成测试 — P0
- RAG 质量度量 — P1
- 熔断器并发测试 — P1
- 连接池泄漏测试 — P2

### 📚 Paige — 四维文档矩阵

**从"工具文档"（菜谱）到"平台文档"（厨房设计指南）。**

```
docs/
├── data-plane/          # 数据平面 — 数据工程师主场
│   ├── ingest/
│   ├── storage/
│   └── catalog/
├── knowledge-plane/     # 知识平面 — AI工程师主场
│   ├── indexing/
│   ├── retrieval/
│   ├── quality/
│   └── context/
├── compute-plane/       # 计算平面 — 平台SRE主场
│   ├── deploy/
│   ├── observe/
│   └── scale/
└── concepts/
    ├── architecture/
    └── glossary/
```

四维：Plane × 角色 × 成熟度（🟢🟡🔴）× 内容类型（Concept / How-to / Reference / Troubleshooting）

文档三词：**Findable · Actionable · Trustworthy**

---

## Round 5: 🏗️ Winston 架构师总结

### 一、共识底座

定位已锁定：Arrow Lake 是全栈平台，广度本身就是战略。

> **核心矛盾不是"做不做全栈"，而是"全栈怎么让人信"。**

### 二、四大主题

**主题 1：架构分层 — 系统还没反映出平台的真实结构**

```
┌─────────────────────────────────────────┐
│  Application Layer                      │
│  RAG / 搜索 / 可视化 / 场景化入口       │
│  ← Sally 的 Scene-Based Navigation      │
├─────────────────────────────────────────┤
│  Service Layer                           │
│  Ingest Pipeline / Knowledge Graph /    │
│  LLM Gateway / Metadata (Gravitino)     │
│  ← John 的 "v1.4.4 三个关键词" 层      │
├─────────────────────────────────────────┤
│  Kernel Layer                            │
│  Storage Abstraction / Connectors /     │
│  Vector Engine / Compute Runtime         │
│  ← Amelia 的 95% 覆盖率目标层           │
└─────────────────────────────────────────┘
         ↕ Knowledge Plane (并行于 Data Plane)
```

**关键约束：每层只依赖下一层接口，不穿透。** 文档、测试、CLI 都按这个结构组织。

**主题 2：依赖风险 — 最大的技术债**

Daft + Ray + Metaflow = 三个有状态分布式系统共存。

| 风险 | 紧迫度 |
|------|--------|
| 版本兼容性矩阵缺失 | P0 |
| Redis 单点（Session/JWT/信号量） | P0 |
| LanceDB 冷启动 | P1 |
| LanceDB ↔ DuckDB 一致性 | P1 |
| LLM Provider 能力注册缺失 | P2 |

**主题 3：开发者体验 — 从"功能列表"到"场景旅程"**

```
🟢 Starter     → 一条路径，一个场景，跑通为止
🟡 Professional → 按角色分发的参考文档
🔴 Enterprise   → SLO、降级策略、安全白皮书
```

**主题 4：质量体系 — 80% 总覆盖率是假象**

| 层级 | 目标 | 理由 |
|------|------|------|
| Kernel | 95% | 底层不可变，必须绝对可靠 |
| Service | 90% | 业务逻辑核心 |
| Application (集成) | 80% | 层间交互验证 |
| E2E | 60% | 关键用户旅程即可 |

### 三、v1.5 路线图 — 不加功能，系统化现有能力

#### P0 — 必须完成（阻塞生产信任）

| 编号 | 事项 | 交付物 |
|------|------|--------|
| A1 | 依赖兼容性矩阵 | `DEPENDENCY_COMPATIBILITY_MATRIX.md` |
| A2 | Redis 去单点 | 高可用 Session 方案设计 |
| A3 | Kernel 层 95% 覆盖率 | 测试缺口补齐 |
| A4 | README 重写 | 场景化叙事 + 角色入口页 |
| A5 | OpenAPI 3.1 Spec | `/docs/api/openapi.yaml` |
| A6 | storage.py 回归保护 | CI 集成测试 |

#### P1 — 应该完成

| 编号 | 事项 | 交付物 |
|------|------|--------|
| B1 | 三层架构文档 | `docs/architecture/` 三平面 |
| B2 | CLI 场景化导航 | 按场景重组 CLI help |
| B3 | LanceDB 冷启动优化 | K8s HPA 就绪方案 |
| B4 | RAG 质量度量体系 | 基准测试 + 回归门禁 |
| B5 | 渐进式文档结构 | docs/ 四维重组 |
| B6 | 安全白皮书 | 安全架构 + 威胁模型 |
| B7 | 设计伙伴招募 | 3-5 个验证伙伴 |

#### P2 — 布局 v2.0

| 编号 | 事项 |
|------|------|
| C1 | LanceDB ↔ DuckDB 一致性保障 |
| C2 | LLM Provider 能力注册表 |
| C3 | 竞品基准测试 |
| C4 | 多语言文档同步策略 |

### 四、STOP / START / CONTINUE

**STOP（立即停止）：**
- 在 v1.5 加新功能 — 已有功能未被系统化交付
- 用总覆盖率 80% 当质量指标 — 掩盖关键路径不足
- README 同时讲 SDK 和 K8s 平台 — 信息过载
- 16 天发 5 个版本 — 速度不是问题，节奏是
- 文档按文件系统组织 — 用户按场景思考

**START（立即开始）：**
- 维护依赖兼容性矩阵 — 三个分布式框架共存的生存级需求
- 按三层架构重组所有用户触点 — 平台定位需要清晰结构映射
- 测试分层覆盖率追踪 — 比总覆盖率更能反映真实质量
- 编写 OpenAPI Spec — 没有 API 合同就没有可信集成层
- 找设计伙伴用 Starter 路径实测 — 所有 DX 假设都需要验证

**CONTINUE（保持）：**
- 全栈平台定位 — 这是核心价值，不是缺陷
- Gravitino 元数据治理集成 — 投资已完成，继续深化
- 安全加固节奏 — v1.4.2 成果显著
- LanceDB 作为向量引擎 — 选型合理，优化而非替换
- 2872 测试基数 — 分层优化的好基础

### 五、一句话

> Arrow Lake v1.4.4 的技术广度已经足够支撑平台定位。v1.5 的全部任务不是"做得更多"，而是**让已有的能力变得可靠（质量）、可发现（体验）、可信赖（文档）**。三层架构不是重构，是让平台的真实结构变得可见。先把这一步走稳，v2.0 再谈扩展边界。

---

## 行动项补充：架构可视化

**7. 架构图需要可视化**

当前 `docs/design_plan/architecture-overview.md` 中的架构图以 ASCII art 和文字描述为主，缺少直观的可视化组件关系图。对于一个定位为"端到端统一多模态数据湖平台 + 知识工程"的产品，架构图是用户理解产品全貌的第一入口。

**具体要求：**

- 使用 **Mermaid** 在 Markdown 中内嵌（GitHub/Gitee 原生渲染，维护成本低）
- 同时生成一份 **Excalidraw** 文件供详细编辑和演示（`.excalidraw` 放在 `docs/design_plan/` 下）
- 图中应清晰展示：
  - **三层架构**：Kernel Layer → Service Layer → Application Layer
  - **双平面**：Data Plane 与 Knowledge Plane 并行关系
  - **核心组件**：LanceDB、DuckDB、Gravitino、HugeGraph、Redis、MinIO/S3
  - **数据流向**：Ingestion → Storage → Indexing → Retrieval → RAG → Answer
  - **外部接口**：CLI、SDK、REST API 三条用户入口
- 优先级：**P0**（架构图是 README 重写和三层架构文档的前置依赖）
