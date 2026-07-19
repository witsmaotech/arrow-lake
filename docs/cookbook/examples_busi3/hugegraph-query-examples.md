# HugeGraph 图查询示例（kg_jd_ddd · 由浅入深）

> 针对 Arrow Lake per-dataset 图 `kg_jd_ddd`（DDD 知识图谱）的图查询示例，**全部实测可用**。
> HugeGraph 1.7.0 · `127.0.0.1:8089` · auth `admin:pa`。宿主连容器（8089→8080）。

---

## 0. 关键前提（实测避坑，必读）

本部署 per-dataset 动态图的 gremlin 绑定有个大坑，先讲清楚再查：

- Gremlin 端点只有 `POST /gremlin`，其 `g` 绑定的是**空默认图 `hugegraph`**
  （`g.V().groupCount().by(label)` 返回 `{}`）。
- per-dataset 图 `kg_jd_ddd` **不在全局 gremlin 绑定里**——alias 重绑也报
  `Could not rebind [g] to [kg_jd_ddd] ... not in the Graph or TraversalSource global bindings`，
  且无 `/graphs/{g}/gremlin` 路由。
- → **per-dataset 图查询走 REST + Traversers API**（HugeGraph 原生图查询，第 1~4 节）。
  Gremlin 语法见第 5 节（仅在默认图/已正确绑定环境可用，作参考）。

**实测 schema**：

| 顶点标签 (8) | `concept` · `event` · `document` · `chunk` · `entity` · `person` · `organization` · `location` |
|--------------|--------------------------------------------------------------------------------------------------|
| 边标签 (9)   | `contains_chunk` · `references` · `next_chunk` · `related_to` · `part_of` · `belongs_to` · `located_in` · `participates_in` · `depicts` |

顶点 id 形如 `"2:0"`(chunk)、`"1:聚合根"`(实体)。取真实起点 id：

```bash
curl --compressed -u admin:pa "http://127.0.0.1:8089/graphs/kg_jd_ddd/graph/vertices?label=concept&limit=3"
```

> `--compressed` 必加（响应是 gzip）。下面 `source` 里的 `1:聚合根` 仅为示例，换成你查到的真实 id。

---

## 1. L1 · 顶点 / 边 / schema（基础检索）

```bash
# 顶点采样
curl --compressed -u admin:pa "http://127.0.0.1:8089/graphs/kg_jd_ddd/graph/vertices?limit=10"

# 按标签 + 属性过滤（聚合根这类 concept；properties 需 URL 编码 JSON）
curl --compressed -u admin:pa "http://127.0.0.1:8089/graphs/kg_jd_ddd/graph/vertices?label=concept&properties=%7B%22name%22%3A%22%E8%81%9A%E5%90%88%E6%A0%B9%22%7D"

# 边采样
curl --compressed -u admin:pa "http://127.0.0.1:8089/graphs/kg_jd_ddd/graph/edges?limit=10"

# schema（顶点标签 / 边标签 / 索引）
curl --compressed -u admin:pa "http://127.0.0.1:8089/graphs/kg_jd_ddd/schema/vertexlabels"
curl --compressed -u admin:pa "http://127.0.0.1:8089/graphs/kg_jd_ddd/schema/edgelabels"
curl --compressed -u admin:pa "http://127.0.0.1:8089/graphs/kg_jd_ddd/schema/indexlabels"
```

---

## 2. L2 · 邻居 / K 步可达（拓扑展开）

```bash
# kneighbor：从起点出发的 1~max_depth 跳邻居集合（去重）
curl --compressed -u admin:pa 'http://127.0.0.1:8089/graphs/kg_jd_ddd/traversers/kneighbor?source="1:聚合根"&max_depth=2'

# kout：恰好走 max_depth 步到达的顶点
curl --compressed -u admin:pa 'http://127.0.0.1:8089/graphs/kg_jd_ddd/traversers/kout?source="1:聚合根"&max_depth=3'

# 限定边方向/标签（推荐：缩小搜索空间）
curl --compressed -u admin:pa 'http://127.0.0.1:8089/graphs/kg_jd_ddd/traversers/kneighbor?source="1:聚合根"&max_depth=2&direction=OUT&label=related_to'
```

---

## 3. L3 · 路径（最短路 / 任意路径 / 环路）

```bash
# 最短路径（聚合根 → 仓储）
curl --compressed -u admin:pa 'http://127.0.0.1:8089/graphs/kg_jd_ddd/traversers/shortestpath?source="1:聚合根"&target="1:仓储"'

# 两点间所有路径（限深）
curl --compressed -u admin:pa 'http://127.0.0.1:8089/graphs/kg_jd_ddd/traversers/paths?source="1:聚合根"&target="1:值对象"&max_depth=4'

# 环路检测（找概念间循环依赖，必带 max_depth）
curl --compressed -u admin:pa 'http://127.0.0.1:8089/graphs/kg_jd_ddd/traversers/rings?source="1:聚合根"&max_depth=5'
```

---

## 4. L4 · 高级遍历（射线 / 交叉点 / 全最短路 / 自定义）

```bash
# 射线：从起点出发的所有有向路径（看“聚合根 关联出去的所有概念链”）
curl --compressed -u admin:pa 'http://127.0.0.1:8089/graphs/kg_jd_ddd/traversers/rays?source="1:聚合根"&max_depth=4'

# 交叉点：多起点的共同邻居（两概念的共同关联）
curl --compressed -u admin:pa 'http://127.0.0.1:8089/graphs/kg_jd_ddd/traversers/crosspoints?sources=%5B%221%3A%E8%81%9A%E5%90%88%E6%A0%B9%22%2C%221%3A%E5%AE%9E%E4%BD%93%22%5D&max_depth=3'

# 同前缀还有：/all-shortest-paths · /weighted-shortest · /single-source · /multi-node · /jaccard-similar
# 详见 GET http://127.0.0.1:8089/versions 或 HugeGraph Traversers 文档
```

**Arrow Lake 封装（带 RBAC，应用层推荐）**——`POST /api/v1/kg/traversers/<api>`，
header `X-API-Key: dev-api-key-for-local-testing-only`，body 带 `dataset`/`source`/`target`/`max_depth`：

```
POST /api/v1/kg/traversers/rays            # /rays /rings /crosspoints /all-shortest-paths
POST /api/v1/kg/traversers/weighted-shortest  # /single-source /multi-node /customized
```

---

## 5. Gremlin 等价语法（参考 · per-dataset 本部署不可用）

> 仅在**默认图**或已正确绑定 traversal source 的环境（如 HugeGraph 控制台 `:8182`）下可用；
> 本项目 per-dataset 图用上面第 1~4 节的 REST。语义由浅入深：

```groovy
// L1 基础
g.V().hasLabel('concept').limit(5)
g.V().has('concept', 'name', '聚合根')                 // 精确名（图查询“按字面”）

// L2 邻居 / K 跳
g.V().has('concept', 'name', '聚合根').out('part_of', 'related_to')          // 出边邻居
g.V().has('concept', 'name', '聚合根').repeat(out()).times(2).dedup()         // 2 跳去重

// L3 路径
g.V().has('concept', 'name', '聚合根')
  .repeat(out()).until(has('name', '仓储')).simplePath().limit(1).path()      // 最短路
g.V().has('concept', 'name', '聚合根').repeat(out()).times(3).emit().path()   // 所有路径

// L4 聚合 / 共同邻居 / hub
g.V().groupCount().by(label)                                                          // 标签分布
g.V().hasLabel('concept').order().by(outE().count(), desc).limit(10)                  // hub 概念（按出度）
g.V().has('concept', 'name', '聚合根').out('related_to')
  .where(__.in('related_to').has('name', '实体'))                                     // 共同关联
```

---

## 6. 性能要点

1. **先过滤再遍历**：`has(...)` 早下推，避免 `g.V().out().has(...)` 全图扫描。
2. **限深限量**：`repeat().times(N)` / `limit()` / `range()`；环路、射线务必带 `max_depth`。
3. **靠索引**：`name` 等高频过滤字段要有 secondary/range index（`/schema/indexlabels`）；
   用 `.profile()` 看是否命中索引。

---

## 附：与 RAG(KA 语义检索) 的分工

| 维度 | HugeGraph 图查询（本文） | RAG（KA 语义检索 `/kg/search` `/kg/ask`） |
|------|--------------------------|---------------------------------------------|
| 召回方式 | 按**字面**（精确 name）+ **拓扑**（邻居/路径） | 按**意思**（FAISS over 定义） |
| 擅长 | 找关系 / 路径 / 全局结构 / hub | 找定义 / 回答开放问题 |
| 典型问 | “聚合根的 1 跳邻居？” “两概念共同关联？” | “聚合根的核心设计原则？” |

两者互补，非替代——见 `dashboard.html` 的「RAG vs 图查询」面板与 `04_compare.json`。
