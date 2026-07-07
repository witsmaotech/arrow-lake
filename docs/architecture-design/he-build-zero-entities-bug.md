# RESOLVED: per-dataset KG 图隔离失效（曾被误诊为 he 0-实体 build bug）

> **状态：** ✅ RESOLVED（2026-07-07）
> **原始误诊：** he kg_build 提交环节丢实体 → **错**。he build 路径完全正常。
> **真根因：** HugeGraph per-dataset 图隔离在 hstore/PD 后端下根本不生效（+ 1.7.0 多图 bug），导致跨数据集数据串台 / 验证查询读错图。

---

## 1. he build 路径是好的（不是 bug）

干净环境下用 1 个合成 chunk 跑完整 he build（插桩），全链路正常：

```
[extract] → entities=9 relations=7
[add_vertices] entity n=9 → returned 9 ids
BUILD STATUS: COMPLETED entities=9 err=None
POST-BUILD GRAPH: {chunk:1, entity:9, concept:9, document:1}   # 正好 20，无串台
```

「提交环节丢实体」的假设被证伪。问题在**图隔离 + 验证查询**，不在 build。

## 2. 真根因 A：hstore/PD 后端 per-dataset 图不隔离

v1.8.6「per-dataset 分图」在原部署（PD + hstore）下**从未真正隔离**：

| 现象 | 实锤 |
|------|------|
| 三张不同图返回**一模一样**的数据 | `kg_busi2_full` / `kg_busi2_attention` / `kg_dbg_he1` 都是 `{chunk:119, entity:9, concept:9, document:2}`——我只给 `kg_dbg_he1` 建了 1 chunk，9 实体瞬间出现在另两张图 |
| 每张图 conf 有独立 `store=` | 但 hstore 后端**无视 `store`**，所有 `kg_*` 共享同一份存储 |
| 跨数据集 chunk id 撞车 | chunk id = 行号 `"0".."118"`，不同数据集互相覆盖 |
| 调试时 `clear` 清掉所有数据集 | 「顶点数飘 285→22→空」「clear 反复 400」的真凶 |

### 为什么 v1.8.6 验收能过？
验收用 `kg_query("g.V()...")`，而它读的是**默认空图**（见根因 B）→ 看起来「隔离了」也看起来「0 实体」。验收本身被同一个 query bug 骗了。

## 3. 真根因 B：`kg_query("g.V()")` 读默认图

`_lake_kg.py:kg_query` 把 gremlin 原样转发，无 `dataset_name`、不把 `g.` 重写成 `{kg_数据集}.traversal()`。所有用 `g.V()` 的验证/查询都读默认图 `hugegraph`（空的）。这是「0 实体」测量假象的主要来源。

## 4. 为什么没修 PD 配置而是换后端（决策记录）

PD 模式 `rest-server.properties` 缺 `usePD=true`（已修，entrypoint 按 backend 条件开启），但 HugeGraph **1.7.0 PD 模式有未修服务端 bug**：建第 1 张图后，建第 2 张图必 NPE——

```
NullPointerException at GraphManager.isExistedGraphNickname(GraphManager.java:2141)
```

nickname 字段、旧 API、graphspace 别名都绕不过。PD 建图还慢（~30s/图，分布式任务开销）。**1.7.0 PD 做不了多图隔离**，不是配置能救的。

## 5. 最终修复：rocksdb 单机后端 + 每图独立 data_path

1. **后端换 rocksdb 单机**（`prod_minimal.yml`：去掉 hg-pd/hg-store，hg-server `HG_SERVER_BACKEND=rocksdb`，`SKIP_STORE_GRPC_WAIT=1`，entrypoint 对 rocksdb 不开 `usePD`）。
2. **每图独立 `rocksdb.data_path`**（关键）：`ensure_graph` 建图时给每张图设独立目录 `{rocksdb_data_path}/graphs/{name}`，否则所有图撞在默认 `rocksdb-data/data/` 上 → 第 2 张图 RocksDB lock 冲突（`lock hold by current process ... No locks available`）。
3. **建图 body 修正**：`gremlin.graph=HugeFactoryAuthProxy`（开 auth 必需，裸 `HugeFactory` 实例化失败）+ `task.scheduler_type=local`（rocksdb 单机）。
4. **kg_query 按数据集路由**：加 `dataset_name` 参数，`g.` → `kg_{ds}.traversal()` 重写（_lake_kg + API + CLI）。
5. **list_graphs PD 兼容**：PD 模式 `GET /graphs` 返空，回退查 `/graphspaces/DEFAULT/graphs`。
6. **HugeGraphConfig.backend** 字段（默认 rocksdb），`ensure_graph` 按它选 body。

### 验证（全 PASS）
- **隔离**：建 `kg_iso_a`/`kg_iso_b` 各写一个实体 → a 只有 Alpha、b 只有 Beta，不串。✅
- **he build 端到端**：合成 chunk → 9 实体落库，图正好 20 顶点无串台。✅
- **REST 按数据集查询**：`kg_stats(kg_dbg_he1)={total:20,edges:17}`、`find_vertices('entity')→[GPU,Adam,Training]`、`kg_stats(kg_iso_a)={total:1}`。✅

## 6. 已知限制

- **raw gremlin `kg_query` 按数据集查询**：1.7.0 不自动把动态图绑成 gremlin traversal source（只有默认 `hugegraph` 由 entrypoint 绑定），`kg_{ds}.traversal()` 会 `MissingPropertyException`。按数据集读请用 REST 接口（`kg_stats` / `kg_find_entities` / `kg_get_neighbors`），它们走 `/graphs/{name}/...` 图作用域，无需绑定。
- **失 HA**：rocksdb 单机无 PD 集群的分布式/高可用。
- **API 容器需 rebuild**：源码改动在宿主；线上 `arrow-lake-api` 镜像未挂源码，要 rebuild 才生效。

## 7. 改动文件

- `deploy/scripts/entrypoint-hugegraph.sh`：`usePD` 按 backend 条件开（hstore 开、rocksdb 关）。
- `deploy/docker-compose.prod_minimal.yml`：hg-server → rocksdb 单机；删 hg-pd/hg-store 及卷。
- `arrow_lake/config/rag.py`：`HugeGraphConfig.backend`（默认 rocksdb）。
- `arrow_lake/knowledge_graph/client.py`：`ensure_graph`（按 backend 选 factory/scheduler + 每图 data_path）、`list_graphs`（PD 回退）。
- `arrow_lake/_lake_kg.py`：`kg_query` 加 `dataset_name` + `_scope_gremlin_to_graph` 重写。
- `arrow_lake/api/models/knowledge_graph.py`、`api/routers/knowledge_graph.py`、`cli/kg.py`：透传 `dataset`。

关联：[[issue_hugegraph_auth_graphspace]]、[[project_v186_per_dataset_kg]]、[[project_v17_hyperextract]]
