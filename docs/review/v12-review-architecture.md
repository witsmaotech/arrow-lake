# Arrow Lake v1.2.1 架构维度评审报告

## 评分: 7.5/10

## 优势 (5 点)

1. **Mixin 门面模式设计清晰，水平切分合理** — Lake 类通过 8 个 Mixin 按业务领域水平切分，公共 API 方法命名直观，`_get_component` 延迟初始化控制启动成本
2. **四层配置系统成熟** — 28 个子配置模块化，代码默认值 -> `.env` -> 环境变量 -> YAML 四层覆盖，YAML 深度合并，启动时强制校验安全配置
3. **异常体系完善** — `ArrowLakeError` 基类 + 17 子类 + 90+ `ErrorCode` 枚举值，三元组 (`error_code` + `message` + `context`)
4. **RAG/GraphRAG 管道具备优雅降级能力** — GraphRAG 在 KG 不可用时自动降级为纯向量 RAG
5. **可观测性基础设施完备** — Prometheus 30+ 指标，`_QueryTimer` 上下文管理器，DuckDBSessionManager session pool stats

## 待改进

### [HIGH] H1: `_get_component` 返回 Any，全链路丧失类型安全

`Lake._get_component` 签名返回 `Any`，导致所有组件访问点都没有静态类型检查。

### [HIGH] H2: Mixin 间隐式共享状态，缺乏接口契约

8 个 Mixin 通过 `self._config` / `self._get_storage()` / `self._get_component()` 直接访问共享状态，没有显式接口约束，依赖 MRO 顺序。

### [HIGH] H3: LanceStorageManager 1017 行，职责过重

承担本地/S3 存储、CRUD、schema evolution、version 管理、vector index、SQL where 解析和安全过滤。

### [HIGH] H4: `Lake.shutdown()` 未完全关闭异步资源链

异步客户端 (`httpx.AsyncClient`) 的 `close()` 是 async 的，同步 `shutdown()` 无法 `await`。

### [P1] P1-1: 查询 Bridge 组件每次方法调用都重新创建

重复创建逻辑，违反 DRY。

### [P1] P1-2: Ingestor 非线程安全但使用了 ThreadPoolExecutor

内部标注 "NOT thread-safe" 但无外部同步机制。

### [P1] P1-3: `sentence-transformers>=3.3` 作为核心依赖过重

会拉取 PyTorch (2GB+)，API embedding 场景被迫安装。

### [P1] P1-4: KG Mixin 大量重复的 guard + client 获取模式

30+ 个方法重复 5 行模板代码。

### [P1] P1-5: AnthropicProvider 的 `_ANTHROPIC_VERSION` 硬编码为 `"2023-06-01"`

### [P2] P2-1: `backup_*` 方法每次调用都创建新的 BackupManager

### [P2] P2-2: `_lake_rag.py` 直接访问 `pipeline._session_store` 私有属性

### [P2] P2-3: 配置子模块缺少运行时校验交叉约束

### [P2] P2-4: `_lake_search.py` 部分方法未使用缓存 bridge

### [P2] P2-5: `ApiEmbeddingEncoder._fallback_cache` 类级别变量非线程安全

### [P2] P2-6: HugeGraphClient 缺少连接池配置

## 架构建议 (长期)

| 编号 | 建议 |
|------|------|
| A1 | 引入 Protocol/ABC 层显式化组件契约 |
| A2 | 事件驱动解耦 Ingest 和后续处理 |
| A3 | 存储后端可插拔化 (StorageAdapter 接口) |
| A4 | 缓存策略统一化 |
| A5 | RAG retriever 策略可插拔化 |
