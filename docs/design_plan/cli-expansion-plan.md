# Arrow Lake CLI 扩展计划

**日期**: 2026-04-24
**范围**: 将 CLI 从 7 个命令扩展到 40+ 个命令，覆盖全部功能场景
**文件**: 将 `arrow_lake/cli.py` 拆分为 `arrow_lake/cli/` 包（14 个模块）

## Context

当前 CLI 只有 7 个命令（`serve`, `version`, `status`, `ingest`, `search`, `demo`, `multimodal-demo`），但平台覆盖 15+ 功能领域（摄取、搜索、查询、索引、导出、嵌入、质量、备份、知识图谱、RAG、配置等）。需要扩展 CLI 以覆盖全部功能场景。

## 架构设计

**核心变更**: 将 `arrow_lake/cli.py` (446 行) 转为 `arrow_lake/cli/` 包

- `__init__.py` — 主 Click Group + 共享工具函数 + 保留的顶层命令
- 12 个子模块，每个对应一个功能域
- `--base-uri` 和 `--config` 放在主 Group 上，所有子命令自动继承
- `ctx.obj["base_uri"]` / `ctx.obj["config_path"]` 传递配置
- `_lake()` 工厂函数统一创建 Lake 实例
- `_run_async()` 包装异步调用（KG/RAG 方法为 async）

**向后兼容**: `status` 保留为 `catalog list` 的别名，`serve`/`version`/`demo`/`multimodal-demo` 保留为顶层命令。

## 文件结构

```
arrow_lake/cli/
    __init__.py      # 主 Group + 共享工具 + 顶层命令 (~200行)
    catalog.py       # catalog list/info/delete (~80行)
    ingest.py        # ingest files/http/images/documents/videos (~180行)
    search.py        # search vector/fts/hybrid (~140行)
    index_cmd.py     # index vector/fts (~80行)
    query.py         # query sql/materialize (~80行)
    export_cmd.py    # export (~60行)
    embed.py         # embed text/image (~80行)
    quality.py       # quality dedup/filter (~80行)
    backup.py        # backup create/list/restore/delete (~100行)
    kg.py            # kg build/status/stats/query/neighbors/delete (~120行)
    rag.py           # rag query/templates (~70行)
    config_cmd.py    # config show/init (~80行)
```

`pyproject.toml` 无需修改 — `arrow-lake = "arrow_lake.cli:main"` 自动解析为包的 `__init__.py`。

## 命令清单（40+）

### 保留的顶层命令
| 命令 | 说明 |
|------|------|
| `arrow-lake serve` | 启动 API 服务器 |
| `arrow-lake version` | 版本信息 |
| `arrow-lake status` | 列出数据集（= `catalog list` 别名） |
| `arrow-lake demo` | 交互式演示 |
| `arrow-lake multimodal-demo` | 多模态演示 |

### 新增命令组

#### `arrow-lake catalog` — 数据集管理
| 命令 | 说明 | 委托到 |
|------|------|--------|
| `catalog list [--json]` | 列出所有数据集 | `Lake.list_datasets()` |
| `catalog info <name>` | 数据集详情（schema/行数/列/版本） | `LanceStorageManager.open_dataset()` |
| `catalog delete <name> [--yes]` | 删除数据集 | `Lake.delete_dataset()` |

#### `arrow-lake ingest` — 数据摄取
| 命令 | 说明 | 委托到 |
|------|------|--------|
| `ingest files <dataset> <paths>...` | CSV/JSON/Parquet 文件 | `Lake.ingest()` |
| `ingest http <dataset> <urls>...` | HTTP URL 远程摄取 | `Lake.ingest_http()` |
| `ingest images <dataset> <paths>...` | 图片摄取（缩略图/EXIF） | `Lake.ingest_images()` |
| `ingest documents <dataset> <paths>...` | PDF 摄取（OCR/分块） | `Lake.ingest_documents()` |
| `ingest videos <dataset> <paths>...` | 视频关键帧提取 | `Lake.ingest_videos()` |

#### `arrow-lake search` — 搜索
| 命令 | 说明 | 委托到 |
|------|------|--------|
| `search vector <dataset> --query <text> [--top-k] [--column]` | 向量搜索 | `Lake.search()` + `LocalEmbeddingEncoder` |
| `search fts <dataset> --query <text> [--top-k]` | 全文搜索 | `Lake.text_search()` |
| `search hybrid <dataset> --query <text> [--top-k] [--alpha]` | RRF 混合搜索 | `Lake.hybrid_search()` |

#### `arrow-lake index` — 索引管理
| 命令 | 说明 | 委托到 |
|------|------|--------|
| `index vector <dataset> [--column] [--metric] [--type]` | 创建向量索引 | `Lake.create_vector_index()` |
| `index fts <dataset> [--column]` | 创建全文搜索索引 | `Lake.create_fts_index()` |

#### `arrow-lake query` — 查询分析
| 命令 | 说明 | 委托到 |
|------|------|--------|
| `query sql <dataset> --sql <sql> [--max-rows]` | DuckDB SQL 查询 | `Lake.olap_query()` |
| `query materialize <dataset> --sql <sql> --name <view>` | 物化视图 | `Lake.materialize()` |

#### `arrow-lake export` — 数据导出
| 命令 | 说明 | 委托到 |
|------|------|--------|
| `export <dataset> --output <path> [--format parquet\|csv]` | 导出数据集 | `Lake.export()` |

#### `arrow-lake embed` — 向量生成
| 命令 | 说明 | 委托到 |
|------|------|--------|
| `embed text <text> [--model]` | 生成文本向量 | `LocalEmbeddingEncoder` |
| `embed image <path> [--model]` | 生成图片向量 | `CLIPImageEncoder` |

#### `arrow-lake quality` — 数据质量
| 命令 | 说明 | 委托到 |
|------|------|--------|
| `quality dedup <dataset> --strategy exact\|perceptual\|both --action flag\|remove` | 数据去重 | `Lake.deduplicate()` |
| `quality filter <dataset> --filters <names> [--mode all\|any]` | 质量过滤 | `Lake.quality_filter()` |

#### `arrow-lake backup` — 备份恢复
| 命令 | 说明 | 委托到 |
|------|------|--------|
| `backup create [--datasets ...] [--backup-id]` | 创建备份 | `BackupManager.create_backup()` |
| `backup list` | 列出备份 | `BackupManager.list_backups()` |
| `backup restore <id> [--datasets ...]` | 恢复备份 | `BackupManager.restore_backup()` |
| `backup delete <id>` | 删除备份 | `BackupManager.delete_backup()` |

#### `arrow-lake kg` — 知识图谱
| 命令 | 说明 | 委托到 |
|------|------|--------|
| `kg build <dataset>` | 构建知识图谱 | `Lake.kg_build()` |
| `kg status <task_id>` | 构建进度 | `Lake.kg_build_status()` |
| `kg stats` | 图谱统计 | `Lake.kg_stats()` |
| `kg query <gremlin>` | Gremlin 查询 | `Lake.kg_query()` |
| `kg neighbors <entity_id> [--depth]` | 邻居遍历 | `Lake.kg_get_neighbors()` |
| `kg delete [--yes]` | 删除图谱 | `Lake.kg_delete_graph()` |

#### `arrow-lake rag` — RAG 问答
| 命令 | 说明 | 委托到 |
|------|------|--------|
| `rag query <dataset> <question> [--top-k] [--strategy]` | RAG 问答 | `Lake.rag_query()` |
| `rag templates` | 列出提示词模板 | `PromptRegistry.list_templates()` |

#### `arrow-lake config` — 配置管理
| 命令 | 说明 | 委托到 |
|------|------|--------|
| `config show` | 显示当前配置 | `Lake._config.model_dump()` |
| `config init [--output file.yaml]` | 生成配置模板 | `ArrowLakeConfig()` + YAML 序列化 |

## 关键文件

- `arrow_lake/cli.py` — 当前 CLI，转包为 `__init__.py`
- `arrow_lake/__init__.py` — Lake 类（8 个 mixin 组合）
- `arrow_lake/_lake_ingest.py` — 摄取 mixin（方法签名）
- `arrow_lake/_lake_search.py` — 搜索 mixin（方法签名）
- `arrow_lake/_lake_query.py` — 查询 mixin（方法签名）
- `arrow_lake/_lake_kg.py` — KG mixin（方法签名，async）
- `arrow_lake/_lake_rag.py` — RAG mixin（方法签名，async）

## 实现步骤

### Phase 1: 包结构重构
1. 删除旧 `arrow_lake/cli.py`
2. 创建 `arrow_lake/cli/__init__.py`，包含共享基础设施和保留的顶层命令
3. 保留 `--base-uri` 为顶层选项
4. 验证现有 7 个命令正常运行

### Phase 2: 高频命令（ingest + catalog + search）
5. 创建 `catalog.py`：list/info/delete
6. 创建 `ingest.py`：files/http/images/documents/videos
7. 创建 `search.py`：vector/fts/hybrid
8. 删除旧的顶层 `ingest` 和 `search` 命令

### Phase 3: 查询与索引
9. 创建 `index_cmd.py`：vector/fts
10. 创建 `query.py`：sql/materialize
11. 创建 `export_cmd.py`：export

### Phase 4: 数据操作
12. 创建 `embed.py`：text/image
13. 创建 `quality.py`：dedup/filter

### Phase 5: 高级功能
14. 创建 `backup.py`：create/list/restore/delete
15. 创建 `kg.py`：build/status/stats/query/neighbors/delete
16. 创建 `rag.py`：query/templates

### Phase 6: 配置与收尾
17. 创建 `config_cmd.py`：show/init
18. 为所有输出命令添加 `--json` 标志
19. 运行完整测试验证

## 验证

```bash
# 1. 验证 CLI 包结构
arrow-lake --help  # 显示所有命令组
arrow-lake catalog --help
arrow-lake ingest --help
arrow-lake search --help

# 2. 验证向后兼容
arrow-lake --base-uri ./data/lake status
arrow-lake serve --port 8000
arrow-lake version

# 3. 验证新命令
arrow-lake catalog info <dataset>
arrow-lake ingest files <dataset> data.csv data.json
arrow-lake ingest images <dataset> img1.jpg img2.png
arrow-lake index vector <dataset> --column text_embedding
arrow-lake search fts <dataset> --query "machine learning"
arrow-lake query sql <dataset> --sql "SELECT COUNT(*) FROM table"
arrow-lake export <dataset> --output result.parquet

# 4. 运行测试
.venv/bin/python -m pytest tests/unit/media/test_cli*.py -q
```
