# SDK & CLI Optimization Plan — Local Stability & High Performance

> Based on full code review of `arrow_lake/` (SDK facade, 8 mixins), `arrow_lake/cli/` (16 command groups), `arrow_lake/core/` (http, metrics), and `arrow_lake/query/session_manager.py`.

---

## 1. SDK — Resource Lifecycle

### 1.1 Lake 实例不支持 Context Manager

**File**: `arrow_lake/__init__.py:96-221`

`Lake` 类有 `shutdown()` 方法但没有 `__enter__`/`__exit__`。用户必须手动调用 `shutdown()`，忘记调用则 DuckDB 连接、httpx 客户端等资源泄漏。

```python
# 当前：手动管理
lake = Lake(base_uri="./data")
try:
    lake.ingest(...)
finally:
    lake.shutdown()

# 改进后：自动管理
with Lake(base_uri="./data") as lake:
    lake.ingest(...)
```

**Fix**: 增加 `__enter__`/`__exit__`，同时增加 `__del__` 发出 ResourceWarning。

### 1.2 `_get_component()` 非线程安全

**File**: `arrow_lake/__init__.py:135-139`

```python
def _get_component(self, key: str, factory: Callable[[], Any]) -> Any:
    if key not in self._components:
        self._components[key] = factory()
    return self._components[key]
```

多线程同时首次访问同一 component 时，factory 可能被调用多次。DuckDBSessionManager 的创建不是幂等的——会注册 Redis 实例、创建信号量。

**Fix**: 加 `threading.Lock` 或用 `dict.setdefault` + 锁。

### 1.3 `shutdown()` 异步清理产生孤儿任务

**File**: `arrow_lake/__init__.py:178-181`

```python
if asyncio.iscoroutinefunction(close_method):
    loop = asyncio.get_running_loop()
    _cleanup_task = loop.create_task(close_method())  # noqa: RUF006
```

在已有事件循环中 `create_task` 创建的任务被丢弃（变量名 `_cleanup_task`）。如果 `shutdown()` 在 FastAPI lifespan 期间被调用，这些任务可能永远不会被 await。

**Fix**: 收集所有异步清理任务，用 `asyncio.gather()` 等待完成。或者统一 shutdown 为 async 方法。

### 1.4 Storage Manager 的 per-dataset RLock 无上限

**File**: `arrow_lake/ingest/storage.py:75`

```python
self._dataset_locks: dict[str, threading.RLock] = defaultdict(threading.RLock)
```

每个 dataset name 创建一个 RLock，永远不会被清理。长期运行中，如果有大量临时数据集，锁对象会持续累积。

**Fix**: 改用 `functools.lru_cache` 包装的锁工厂，或定期清理不再使用的数据集锁。

---

## 2. SDK — 连接与 HTTP

### 2.1 无共享 HTTP 连接池

**File**: `arrow_lake/core/http.py:51-70`

`create_http_client()` 和 `create_async_http_client()` 每次调用创建新的 `httpx.Client`。整个项目中：
- `OpenAICompatibleProvider` 创建 1 个
- `AnthropicProvider` 创建 1 个
- `ApiEmbeddingEncoder` 创建 1 个
- `HugeGraphClient` 创建 1 个
- `GravitinoClient` 创建 1 个
- `VermeerClient` 创建 1 个

每个 client 独立配置 `max_connections=10, max_keepalive_connections=5`，总计可能占用 60+ 连接。

**本地模式影响**：连接数不是主要问题，但 DNS 解析、TLS 握手、keepalive 探测都重复进行。

**Fix**: 在 `Lake` 中维护一个共享的 `httpx.AsyncClient`，各组件通过 `_get_component("http_client", ...)` 复用。

### 2.2 API Embedding Encoder 使用同步 httpx

**File**: `arrow_lake/embed/encoder.py:248`

```python
self._client = create_http_client(...)  # 同步 httpx.Client
```

在 async 上下文中调用 `encode()` 会阻塞事件循环。RAG pipeline 通过 `run_in_executor` 绕开了，但 Embedding API router 可能直接调用。

**Fix**: 提供 `AsyncApiEmbeddingEncoder` 变体，或让 `encode()` 检测当前事件循环并自动走 executor。

---

## 3. SDK — DuckDB Session Manager

### 3.1 Idle Pool 无后台回收

**File**: `arrow_lake/query/session_manager.py:167`

```python
self._idle_pool: deque[_IdleConnection] = deque()
```

空闲连接只在 `acquire()` 时检查过期（`_acquire_connection` 中的 age/lifetime 判断）。如果没有新的查询请求，过期的空闲连接不会被回收，持续占用内存和文件句柄。

**本地模式影响**：长时间空闲后突然发查询，可能拿到 stale connection，需要 `_health_check` 失败后重试，增加首次延迟。

**Fix**: 增加后台线程，定期（每 60s）扫描 idle pool 并清理过期连接。

### 3.2 连接创建失败只重试一次

**File**: `arrow_lake/query/session_manager.py:386-403`

```python
for attempt in range(2):
    try:
        session = DuckDBSession(...)
        ...
    except duckdb.Error:
        session.__exit__(None, None, None)
        if attempt == 0:
            logger.warning("connection_creation_failed_retrying")
            continue
        raise
```

本地模式下 DuckDB 连接创建通常很快，但如果磁盘 I/O 抖动或内存压力，一次重试可能不够。

**Fix**: 可配置重试次数（默认 2，生产环境可调高），加指数退避。

### 3.3 无预热机制

DuckDB 首次查询需要加载扩展、读元数据、构建缓存。对于冷启动场景（CLI 命令、定时任务），首次查询延迟可能 2-5 秒。

**Fix**: 增加 `warmup()` 方法，在 `Lake.__init__` 后可选调用，预先创建 1 个连接并执行 `SELECT 1`。

---

## 4. CLI — 性能

### 4.1 每个命令创建新 Lake 实例（核心问题）

**File**: `arrow_lake/cli/__init__.py:55-67`

```python
def _lake(base_uri, config_path):
    from arrow_lake import ArrowLakeConfig, Lake
    config = ArrowLakeConfig.from_yaml(config_path) if config_path else None
    return Lake(base_uri=base_uri, config=config)
```

每个 CLI 子命令都调用 `_get_lake(ctx)` 创建新的 Lake 实例。这意味着：
- 每次 `Lake()` 初始化 metrics
- 每次 `_get_storage()` 创建新 `LanceStorageManager`
- 每次 `get_session_manager()` 创建新 `DuckDBSessionManager`
- **embedding 模型每次重新加载**（最严重）

在 `arrow-lake search vector` 中，模型加载需要 3-10 秒（本地 SentenceTransformer）。

**Fix**: 在 Click context 中缓存 Lake 实例：

```python
def _get_lake(ctx: click.Context):
    if "lake" not in ctx.obj:
        ctx.obj["lake"] = _lake(ctx.obj["base_uri"], ctx.obj.get("config_path"))
    return ctx.obj["lake"]
```

### 4.2 Search 命令每次重新加载 Embedding 模型

**File**: `arrow_lake/cli/search.py:18-29`

```python
def _get_query_vector(text: str, model_name: str, column: str):
    encoder = LocalEmbeddingEncoder(model_name=model_name)  # 每次新建
    raw = encoder._load_model().encode([text], normalize_embeddings=True)
```

每次搜索命令都创建新的 `LocalEmbeddingEncoder` 并加载模型。Qwen3-Embedding-0.6B 模型加载需要 3-10 秒。

**Fix**: 在模块级别缓存 encoder 实例：

```python
_encoder_cache: dict[str, LocalEmbeddingEncoder] = {}

def _get_encoder(model_name: str) -> LocalEmbeddingEncoder:
    if model_name not in _encoder_cache:
        _encoder_cache[model_name] = LocalEmbeddingEncoder(model_name=model_name)
    return _encoder_cache[model_name]
```

### 4.3 `_run_async()` 每次创建新事件循环

**File**: `arrow_lake/cli/__init__.py:75-79`

```python
def _run_async(coro):
    import asyncio
    return asyncio.run(coro)
```

`asyncio.run()` 每次创建并销毁一个事件循环。如果同一个命令中调用多次（如 batch RAG），会创建/销毁多个循环。

**Fix**: 复用事件循环，或把多次 async 调用合并到一个 `asyncio.run()` 中。

### 4.4 长操作无进度反馈

Ingest、Index 创建、KG Build 等耗时操作只有开始/结束状态，没有进度条。用户面对大量文件导入时，无法判断是卡住了还是正在处理。

**Fix**: 使用 Rich Progress 为长操作添加进度条：

```python
from rich.progress import Progress, SpinnerColumn, BarColumn

with Progress(SpinnerColumn(), BarColumn(), console=console) as progress:
    task = progress.add_task("Ingesting...", total=len(files))
    for report in lake.ingest_iter(dataset, files):
        progress.advance(task, advance=1)
```

### 4.5 CLI 命令不调用 `lake.shutdown()`

所有 CLI 命令在操作完成后直接退出，没有调用 `lake.shutdown()`。DuckDB 连接和 httpx 客户端的清理依赖进程退出时的 GC，可能导致：
- Lance 文件句柄未正确关闭
- WAL 日志未刷盘
- 临时文件未清理

**Fix**: 使用 Click 的 `ctx.call_on_close()` 注册清理：

```python
def _get_lake(ctx: click.Context):
    if "lake" not in ctx.obj:
        lake = _lake(ctx.obj["base_uri"], ctx.obj.get("config_path"))
        ctx.obj["lake"] = lake
        ctx.call_on_close(lake.shutdown)
    return ctx.obj["lake"]
```

---

## 5. CLI — 可用性

### 5.1 无 `--verbose` / `--quiet` 控制

所有命令的输出级别固定。批量操作时大量 Rich 表格输出影响性能，调试时又需要更多细节。

**Fix**: 在 `main` group 增加 `--verbose/-v` 和 `--quiet/-q` 选项，控制日志级别和输出格式。

### 5.2 泛异常捕获丢失上下文

144 处 `except Exception as exc: _print_error(f"...: {exc}")` 将所有异常信息压缩为一行字符串。结构化的 `ArrowLakeError` 中的 `error_code` 和 `context` 字段全部丢失。

**Fix**: 捕获 `ArrowLakeError` 时提取结构化信息：

```python
except ArrowLakeError as exc:
    _print_error(f"{exc.message}")
    if verbose and exc.context:
        console.print(f"[dim]  code={exc.error_code.value} context={exc.context}[/dim]")
    raise SystemExit(1) from None
except Exception as exc:
    _print_error(f"Unexpected: {exc}")
    raise SystemExit(1) from None
```

### 5.3 大结果集无分页

`_format_results()` 和 SQL query 默认 `max_rows=100`，但没有 `--offset` / `--page` 参数。用户无法翻页查看后续结果。

**Fix**: 增加 `--offset` 和 `--limit` 参数，或使用 Rich 的 `Pager` 支持交互式浏览。

### 5.4 缺少 `--output` 格式选项

只有部分命令支持 `--json`。缺少 `--format csv`、`--format table` 等统一输出格式控制，不利于管道操作和脚本集成。

**Fix**: 在 main group 增加 `--format` 选项（table/json/csv），统一所有命令的输出。

---

## 6. SDK — 本地模式稳定性

### 6.1 无 DuckDB 内存上限保护

本地模式下默认 `max_query_memory_mb` 如果没有显式配置，DuckDB 可能占用全部系统内存，导致 OOM。

**Fix**: 在 `OlapConfig` 中设置合理的默认值（如系统内存的 50%），或在 `DuckDBSessionManager` 初始化时检测系统内存并自动设置。

### 6.2 Lance 写入无 wal_timeout 保护

Lance 写入在进程异常退出时可能留下不完整的 fragment。CLI 命令 Ctrl+C 后尤其容易触发。

**Fix**: 在 CLI 的 `_get_lake` 中注册 signal handler，确保 `lake.shutdown()` 在 SIGINT/SIGTERM 时被调用。

### 6.3 首次导入延迟

`Lake.__init__` 中 `from arrow_lake.core.metrics import system_uptime_seconds` 触发 Prometheus client 加载。在不需要 metrics 的纯本地模式下，这是不必要的开销。

**Fix**: Metrics 注册延迟到 `_get_component("metrics", ...)` 首次访问时，或通过配置 `metrics_enabled: false` 完全跳过。

---

## 7. SDK — 可扩展性

### 7.1 Mixin 继承链过深

`Lake` 继承 8 个 mixin，总代码量超过 3000 行。新功能只能通过增加 mixin 实现，Lake 类签名持续膨胀。

**Fix**: 考虑组合模式替代继承。核心 `Lake` 只保留存储和会话管理，其他功能通过 `lake.search`、`lake.rag` 等命名空间访问：

```python
lake = Lake(base_uri="./data")
lake.search.vector(...)    # 而非 lake.search(...)
lake.rag.query(...)        # 而非 lake.rag_query(...)
```

### 7.2 Bridge 工厂参数不一致

不同 mixin 中 bridge 的创建方式不统一：

```python
# _lake_search.py
def _bridge_kwargs(self) -> dict[str, Any]:
    return {"storage_config": ..., "lance_scan_mode": ..., "session_manager": ...}

# _lake_query.py
def _get_olap_bridge(self):
    return OlapSearchBridge(
        session_manager=self.get_session_manager(),
        storage_config=self._config.storage,
        olap_config=self._config.olap,
    )
```

每个 bridge 的构造函数参数不同，添加新 bridge 需要理解每个的初始化模式。

**Fix**: 统一 bridge 工厂接口，所有 bridge 通过 `_get_component(key, factory)` 创建。

### 7.3 无插件/扩展机制

无法在不修改源码的情况下添加自定义：
- 自定义文件格式解析器（ingest）
- 自定义搜索策略（search）
- 自定义质量过滤器（quality）

**Fix**: 增加基于 entry_points 的插件注册机制：

```python
# 第三方包注册自定义 ingester
[project.entry-points."arrow_lake.ingesters"]
xml = "my_package.xml_ingest:XmlIngester"
```

---

## Priority Matrix

| Priority | Item | Impact |
|---|---|---|
| **P0** | 4.1 CLI Lake 实例缓存 | 性能：消除重复初始化，搜索命令提速 3-10s |
| **P0** | 4.2 Embedding 模型缓存 | 性能：消除模型重复加载 |
| **P0** | 4.5 CLI shutdown 清理 | 稳定性：防止数据损坏 |
| **P0** | 1.1 Lake context manager | 稳定性：资源自动管理 |
| **P1** | 2.1 共享 HTTP 连接池 | 性能：减少连接开销 |
| **P1** | 3.3 DuckDB 预热机制 | 性能：消除冷启动延迟 |
| **P1** | 4.4 长操作进度条 | 可用性：用户体验 |
| **P1** | 6.2 Signal handler 保护 | 稳定性：防止 Ctrl+C 数据损坏 |
| **P1** | 6.3 Metrics 延迟加载 | 性能：纯本地模式减少启动开销 |
| **P2** | 1.2 _get_component 线程安全 | 稳定性：多线程场景 |
| **P2** | 1.3 shutdown 异步清理 | 稳定性：异步资源泄漏 |
| **P2** | 3.1 Idle pool 后台回收 | 稳定性：连接泄漏 |
| **P2** | 5.1 --verbose/--quiet | 可用性：输出控制 |
| **P2** | 5.2 结构化错误输出 | 可用性：调试友好 |
| **P2** | 6.1 DuckDB 内存保护 | 稳定性：OOM 防护 |
| **P2** | 2.2 Async Embedding Encoder | 架构：一致性 |
| **P3** | 1.4 Storage lock 清理 | 可扩展性：长期运行 |
| **P3** | 3.2 连接重试可配置 | 可扩展性：灵活性 |
| **P3** | 4.3 事件循环复用 | 性能：微优化 |
| **P3** | 5.3 结果分页 | 可用性 |
| **P3** | 5.4 统一输出格式 | 可用性 |
| **P3** | 7.1 组合替代继承 | 架构：可维护性 |
| **P3** | 7.2 Bridge 工厂统一 | 架构：一致性 |
| **P3** | 7.3 插件扩展机制 | 可扩展性 |

---

## Implementation Notes

### 最小改动方案（P0 items）

P0 项的改动量最小、收益最大：

1. **CLI Lake 缓存** — 改 `_get_lake()` 为 5 行代码 + `ctx.call_on_close(lake.shutdown)`
2. **Embedding 缓存** — 改 `_get_query_vector()` 为模块级 dict 缓存
3. **Lake context manager** — 增加 `__enter__`/`__exit__`，3 行代码
4. **CLI shutdown** — 在 `_get_lake` 中注册 `ctx.call_on_close`

这 4 项改动总计约 30 行代码，但能解决 CLI 本地运行中最突出的性能和稳定性问题。

### 向后兼容

所有改动保持 API 兼容：
- Lake 新增 `__enter__`/`__exit__` 不影响现有用法
- CLI 缓存对用户透明
- embedding 缓存对调用者不可见
- 新增 `--verbose`/`--quiet` 为可选参数，默认行为不变
