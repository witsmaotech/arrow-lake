# 混合搜索与分面搜索

> RRF 融合向量搜索 + 全文搜索实现混合检索，DuckDB CUBE 实现分面导航，加权 RRF 实现多列 Ensemble 搜索。

***

## 1. 混合搜索

```python
"""混合搜索最小可运行示例"""
from arrow_lake import Lake
import pyarrow as pa
import numpy as np

lake = Lake(base_uri="./lake_demo")

# 写入带文本和 embedding 的 dataset
np.random.seed(42)
titles = ["轻量级跑步运动鞋", "专业篮球鞋高帮款", "商务休闲皮鞋",
          "越野跑鞋防滑", "夏季透气运动凉鞋", "女士瑜伽健身鞋"]
embeddings = np.random.randn(len(titles), 128).astype(np.float32).tolist()

products = pa.table({
    "id": list(range(1, len(titles) + 1)),
    "title": titles,
    "category": ["running", "basketball", "casual", "running", "casual", "fitness"],
    "brand": ["Nike", "Adidas", "Clarks", "Salomon", "Teva", "Lululemon"],
    "text_content": [f"{t}，品牌：{b}" for t, b in zip(titles,
        ["Nike", "Adidas", "Clarks", "Salomon", "Teva", "Lululemon"])],
    "text_embedding": embeddings,
})
lake.create_dataset("products", products)

# 创建索引
lake.create_vector_index("products", vector_column="text_embedding")
lake.create_fts_index("products", fts_column="text_content")

# 混合搜索
query_vec = np.random.randn(128).astype(np.float32).tolist()
result = lake.hybrid_search(
    "products",
    query_vector=query_vec,
    query_text="轻量级运动鞋",
    top_k=5,
)
print(f"混合搜索 -> {result.row_count} 条结果 (rrf_k={result.rrf_k})")
for i in range(result.table.num_rows):
    doc_id = result.table.column("id")[i].as_py()
    title = result.table.column("title")[i].as_py()
    score = result.table.column("_rrf_score")[i].as_py()
    print(f"  [{doc_id}] {title}  (rrf_score={score:.6f})")

lake.shutdown()
```

***

## 2. RRF 融合原理

Reciprocal Rank Fusion 在 `HybridSearchBridge._rrf_fuse()` 中实现：

```text
score(doc) = SUM( 1 / (rank(doc, list_i) + k) )
```

* `rank(doc, list_i)`: 文档在第 i 个排序列表中的排名 (从 1 开始)
* `k`: 平滑常数，默认 60 (论文推荐值)

```text
向量搜索 top 3:              全文搜索 top 3:
  rank 1: 越野跑鞋              rank 1: 轻量级跑步运动鞋
  rank 2: 登山徒步鞋            rank 2: 儿童减震跑步鞋
  rank 3: 篮球鞋                rank 3: 夏季透气凉鞋

           +-- RRF 融合 (k=60) --+
                      |
  rank 1: 轻量级跑步运动鞋  (1/(1+60) + 1/(1+60) = 0.0328)
  rank 1: 越野跑鞋            (1/(1+60) + 0 = 0.0164)
```

| rrf\_k      | 效果        | 推荐场景   |
| ----------- | --------- | ------ |
| 30-50       | 高排名结果权重更大 | 重视精确匹配 |
| **60** (默认) | 平衡融合      | 通用推荐   |
| 100-200     | 排名差异被拉平   | 重视多样性  |

***

## 3. 混合搜索 API

```python
def hybrid_search(
    self,
    dataset_name: str,
    query_vector: list[float],          # 查询 embedding 向量 (位置参数)
    query_text: str,                    # 查询文本 (用于 FTS, 位置参数)
    *,
    top_k: int | None = None,           # 返回数量
    vector_column: str = "text_embedding",  # 向量列名
    fts_column: str | None = None,      # FTS 列名
    where: str | None = None,           # 元数据过滤
    version: int | None = None,         # 数据集版本 (时间旅行查询)
) -> HybridSearchResult: ...
```

### 返回类型：HybridSearchResult

```python
@dataclass(frozen=True)
class HybridSearchResult:
    table: pa.Table               # 结果表，含 _rrf_score 列
    row_count: int                # 结果数量
    query_text: str               # FTS 查询文本
    query_vector_dim: int         # 向量维度
    top_k: int                    # 请求的最大返回数
    rrf_k: int                    # RRF 常数
    max_rrf_score: float | None   # 最高 RRF 分数
```

### 配置参数

```python
from arrow_lake.config import HybridSearchConfig

config = HybridSearchConfig(
    rrf_k=60,                    # RRF 平滑常数
    default_top_k=10,             # 最终返回数量
    vector_top_k_multiplier=3,    # 向量候选池 = top_k * 3
    fts_top_k_multiplier=3,       # FTS 候选池 = top_k * 3
    reranker_type="none",         # 重排器：none / cross_encoder（默认 none，RRF 粗排即最终结果）
    reranker_model="BAAI/bge-reranker-v2-m3",  # cross-encoder 精排模型
)
```

Arrow Lake 自动选择执行路径：优先 DuckDB 原生 `lance_hybrid_search()`，失败时回退为子 Bridge 分别搜索再融合。

> **重排是配置驱动，非请求参数**：`reranker_type` / `reranker_model` 在 `HybridSearchConfig` 中全局设定，搜索端点（`POST /{name}/search/hybrid`）不接受 per-request 重排参数。设为 `cross_encoder` 时用 `reranker_model`（默认 `BAAI/bge-reranker-v2-m3`）对 RRF 粗排结果做连续分精排。

***

## 4. 分面搜索

`faceted_search` 在向量搜索基础上，通过 DuckDB `GROUP BY CUBE` 计算各维度分面计数，适用于电商/内容平台分类导航。

```python
query_vec = encoder.embed_text("运动鞋")

result = lake.faceted_search(
    "products",
    query_vector=query_vec,
    facets=["category", "brand"],
    top_k=10,
)

# 搜索结果
print(f"搜索结果：{result.row_count} 条")
for i in range(result.table.num_rows):
    print(f"  - {result.table.column('title')[i].as_py()}")

# 分面计数
facet_dict: dict[str, dict[str, int]] = {}
for f in result.facets:
    facet_dict.setdefault(f.name, {})[f.value] = f.count

for dim, values in facet_dict.items():
    print(f"\n  [{dim}]")
    for val, cnt in sorted(values.items(), key=lambda x: -x[1]):
        print(f"    {val}: {cnt}")
# 输出：
#   [category]
#     running: 2
#     casual: 2
#     ...
#   [brand]
#     Nike: 1
#     Adidas: 1
#     ...
```

### API 签名

```python
def faceted_search(
    self,
    dataset_name: str,
    query_vector: list[float],       # 查询 embedding 向量 (位置参数)
    *,
    facets: list[str] | None = None, # 分面维度列名
    top_k: int = 10,
    vector_column: str = "embedding",
    where: str | None = None,        # 元数据过滤
    version: int | None = None,      # 数据集版本 (时间旅行查询)
) -> FacetedSearchResult: ...
```

### 返回类型

```python
@dataclass(frozen=True)
class FacetCount:
    name: str     # 分面维度 (如 "category")
    value: str    # 分面值 (如 "running")
    count: int    # 记录数

@dataclass(frozen=True)
class FacetedSearchResult:
    table: pa.Table               # 向量搜索结果
    row_count: int
    facets: list[FacetCount]      # 分面计数列表
    total_facets: int             # 分面值总数
    query_vector_dim: int
    top_k: int
```

### 前端联动

分面搜索的核心用途是 "搜索结果 + 分类筛选导航" 联动：

```python
# 1. 用户搜索 -> 展示分面选项 (侧边栏)
result = lake.faceted_search("products", query_vector=query_vec,
                              facets=["category", "brand"])

# 2. 用户点击 "running" 筛选 -> 分面计数自动更新
result = lake.faceted_search("products", query_vector=query_vec,
                              facets=["category", "brand"],
                              where="category = 'running'")
```

### 标量索引加速

对分面维度列建标量索引可显著加速 `GROUP BY CUBE` 聚合。`FacetedSearchConfig.scalar_index_type_map` 按列基数自动选择索引类型（低基数如 `modality`/`source`/`doc_type` → `BITMAP`，其余 → `BTREE`）。批量建索引：

```python
# 对默认分面列建标量索引（按 scalar_index_type_map 选 BTREE/BITMAP）
lake.create_facet_indexes("products")
# 或对单列建索引
lake.create_scalar_index("products", column="category")
```

***

## 5. Ensemble 多列搜索

`ensemble_search` 在多个 embedding 列上执行向量搜索，通过加权 RRF 融合。适用于多模态 embedding 场景。

```python
# 假设 products 有 text_embedding 和 image_embedding 两列
result = lake.ensemble_search(
    "products",
    query_vector=query_vec,
    columns=["text_embedding", "image_embedding"],
    weights={"text_embedding": 0.7, "image_embedding": 0.3},
    top_k=10,
)
print(f"搜索列：{result.columns_searched}, 融合：{result.fusion_method}")

for i in range(result.table.num_rows):
    doc_id = result.table.column("id")[i].as_py()
    score = result.table.column("_ensemble_score")[i].as_py()
    title = result.table.column("title")[i].as_py()
    print(f"  [{doc_id}] {title}  (score={score:.6f})")
```

### API 签名

```python
def ensemble_search(
    self,
    dataset_name: str,
    query_vector: list[float],              # 查询向量 (所有列同维度)
    *,
    columns: list[str] | None = None,       # embedding 列名
    weights: dict[str, float] | None = None,# 各列权重
    top_k: int | None = None,               # 返回数量
    where: str | None = None,               # 元数据过滤
    version: int | None = None,             # 数据集版本 (时间旅行查询)
) -> EnsembleSearchResult: ...
```

不指定 `columns` 时自动检测所有与查询向量维度匹配的 `fixed_size_list` 列。

加权 RRF 公式：`score(doc) = SUM( weight_i / (rank(doc, list_i) + k) )`

### 多模态以图搜图

CLIP 嵌入把文本和图像映射到同一向量空间，支持「以文搜图」「以图搜图」。`Lake.encode_text_clip()` 对查询文本编码，结果与 `POST /api/v1/embed/image` 返回的图像嵌入同源（L2 归一化），可直接用于向量搜索：

```python
# 文本 → 图像 embedding，与 /embed/image 同空间
query_vec = lake.encode_text_clip("红色运动鞋")
results = lake.search("products", query_vector=query_vec, vector_column="image_embedding")
```

***

## 6. 搜索策略选择指南

```text
需要搜索?
  |
  +-- 精确关键词匹配 ------> text_search()
  +-- 语义相似度 ---------> search() (向量)
  +-- 分类筛选导航 -------> faceted_search()
  +-- 多种 embedding -----> ensemble_search()
  +-- 语义 + 关键词 ------> hybrid_search()
```

### 策略对比

| 策略       | API                      | 输入           | 适用场景             |
| -------- | ------------------------ | ------------ | ---------------- |
| 向量搜索     | `lake.search()`          | embedding 向量 | 语义检索、RAG、相似推荐    |
| 全文搜索     | `lake.text_search()`     | 文本字符串        | 精确关键词、标识符搜索      |
| 混合搜索     | `lake.hybrid_search()`   | 向量 + 文本      | 兼顾语义和关键词         |
| 分面搜索     | `lake.faceted_search()`  | 向量 + 分面列     | 电商/内容分类导航        |
| Ensemble | `lake.ensemble_search()` | 向量 + 多列      | 多模态 embedding 融合 |

### 场景推荐

| 业务场景       | 推荐策略              | 原因                |
| ---------- | ----------------- | ----------------- |
| 文档问答 (RAG) | `hybrid_search`   | 语义 + 关键词提升召回      |
| 电商商品搜索     | `faceted_search`  | 向量召回 + 品牌分类导航     |
| 日志/错误码搜索   | `text_search`     | 精确匹配编码和标识符        |
| 多模态搜索      | `ensemble_search` | 融合文本和图像 embedding |
| 技术文档站      | `hybrid_search`   | 标题精确匹配 + 内容语义     |

***

## 7. REST API 参考

```bash
# 混合搜索
curl -X POST http://localhost:8000/api/v1/datasets/products/search/hybrid \
  -H "Content-Type: application/json" \
  -d '{"query_vector": [0.1, 0.2], "query_text": "轻量级运动鞋", "top_k": 10}'

# 分面搜索
curl -X POST http://localhost:8000/api/v1/datasets/products/search/faceted \
  -H "Content-Type: application/json" \
  -d '{"query_vector": [0.1, 0.2], "facets": ["category", "brand"]}'

# Ensemble 搜索
curl -X POST http://localhost:8000/api/v1/datasets/products/search/ensemble \
  -H "Content-Type: application/json" \
  -d '{"query_vector": [0.1, 0.2], "columns": ["text_embedding", "image_embedding"],
       "weights": {"text_embedding": 0.7, "image_embedding": 0.3}}'
```

| 端点                             | 请求模型                    | 响应模型                     |
| ------------------------------ | ----------------------- | ------------------------ |
| `POST /{name}/search/hybrid`   | `HybridSearchRequest`   | `HybridSearchResponse`   |
| `POST /{name}/search/faceted`  | `FacetedSearchRequest`  | `FacetedSearchResponse`  |
| `POST /{name}/search/ensemble` | `EnsembleSearchRequest` | `EnsembleSearchResponse` |
