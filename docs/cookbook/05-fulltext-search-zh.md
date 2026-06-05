# 全文搜索 (BM25)

> 基于 LanceDB Tantivy 全文索引 + jieba 中文分词的 BM25 检索。

***

## 1. 快速示例

```python
"""全文搜索最小可运行示例"""
from arrow_lake import Lake
import pyarrow as pa

lake = Lake(base_uri="./lake_demo")

# 写入带文本列的 dataset
docs = pa.table({
    "id": [1, 2, 3, 4, 5],
    "title": ["机器学习入门指南", "深度学习与神经网络", "自然语言处理实战",
              "Python 数据分析", "推荐系统算法详解"],
    "text_content": [
        "机器学习是人工智能的核心分支，涵盖了监督学习和无监督学习等技术",
        "深度学习通过多层神经网络实现特征自动提取，广泛应用于计算机视觉",
        "自然语言处理利用深度学习模型实现文本分类、情感分析和机器翻译",
        "Python 提供了丰富的数据分析库，如 Pandas 和 NumPy",
        "推荐系统结合协同过滤和内容推荐，为用户提供个性化服务",
    ],
    "category": ["AI", "AI", "AI", "数据", "AI"],
})
lake.create_dataset("docs", docs)

# 创建全文索引 (默认使用 jieba 中文分词)
lake.create_fts_index("docs", fts_column="text_content")

# 执行全文搜索
result = lake.text_search("docs", query="机器学习入门", top_k=10)
for i in range(result.table.num_rows):
    doc_id = result.table.column("id")[i].as_py()
    score = result.table.column("_score")[i].as_py()
    title = result.table.column("title")[i].as_py()
    print(f"  [{doc_id}] {title}  (score={score:.4f})")

lake.shutdown()
```

***

## 2. 创建 FTS 索引

```python
# 在默认列 (text_content) 上创建索引
lake.create_fts_index("docs")

# 指定索引列
lake.create_fts_index("docs", fts_column="title")

# 强制重建索引
lake.create_fts_index("docs", fts_column="text_content", replace=True)
```

### API 签名

```python
def create_fts_index(
    self,
    dataset_name: str,
    *,
    fts_column: str | None = None,   # 文本列名，默认来自配置
    replace: bool = True,             # 是否替换已有索引
) -> None: ...
```

当 `tokenizer_type` 为 `"jieba"` (默认) 时，`create_index` 会：

1. 对每行文本调用 `segment_text()` 进行分词
2. 将分词结果写入 `_fts_segmented` 列
3. 在该列上建立 Tantivy BM25 索引

***

## 3. 执行全文搜索

```python
# 基本搜索
result = lake.text_search("docs", query="深度学习模型")
print(f"查询：{result.query}, 结果：{result.row_count} 条，最高分：{result.max_score:.4f}")

# 限制返回数量
result = lake.text_search("docs", query="Python", top_k=5)

# 指定搜索列
result = lake.text_search("docs", query="推荐算法", fts_column="title")
```

### API 签名

```python
def text_search(
    self,
    dataset_name: str,
    query: str,
    *,
    top_k: int | None = None,      # 返回数量 (默认来自配置)
    fts_column: str | None = None,  # 搜索列名
    where: str | None = None,       # 元数据过滤表达式
    version: int | None = None,     # 数据集版本 (时间旅行查询)
    offset: int = 0,                # 跳过结果数 (分页)
) -> FullTextSearchResult: ...
```

### 返回类型：FullTextSearchResult

```python
@dataclass(frozen=True)
class FullTextSearchResult:
    table: pa.Table           # Arrow 表，含 _score 相关性列
    row_count: int            # 结果数量
    query: str                # 搜索查询字符串
    top_k: int                # 请求的最大返回数
    fts_column: str           # 搜索的文本列
    max_score: float | None   # 最高相关性评分
```

### 遍历结果

```python
result = lake.text_search("docs", query="自然语言处理", top_k=3)
ids = result.table.column("id").to_pylist()
scores = result.table.column("_score").to_pylist()
titles = result.table.column("title").to_pylist()
for doc_id, title, score in zip(ids, titles, scores):
    print(f"  [{doc_id}] {title}  (score={score:.4f})")

# 转为 Pandas
df = result.table.to_pandas()
```

***

## 4. 中文分词：jieba 集成

分词逻辑位于 `arrow_lake.query._chinese_tokenizer` 模块。

```python
from arrow_lake.query._chinese_tokenizer import segment_text, segment_query

# 索引时：分词文档
print(segment_text("自然语言处理利用深度学习模型实现文本分类"))
# "自然 语言 处理 利用 深度 学习 模型 实现 文本 分类"

# 搜索时：分词查询
print(segment_query("深度学习入门"))
# "深度 学习 入门"
```

### 自定义词典

```text
# custom_dict.txt — 每行一个词条
机器学习
深度学习
自然语言处理
推荐系统
```

```python
from arrow_lake import Lake
from arrow_lake.config import FullTextSearchConfig

fts_config = FullTextSearchConfig(
    fts_column="text_content",
    tokenizer_type="jieba",
    jieba_user_dict="./custom_dict.txt",
)
lake = Lake(base_uri="./lake", fts=fts_config)
lake.create_fts_index("docs")
```

***

## 5. 搜索参数配置

```python
from arrow_lake.config import FullTextSearchConfig

config = FullTextSearchConfig(
    default_top_k=10,          # 默认返回数量 (>= 1)
    fts_column="text_content", # 默认索引文本列
    stem=True,                 # 词干提取 (英文：running -> run)
    remove_stop_words=True,    # 去除停用词 (the, is, 的，了)
    lower_case=True,           # 转小写
    tokenizer_type="jieba",    # "jieba" (中文推荐) | "default" (内置)
    jieba_user_dict=None,      # jieba 自定义词典路径
)
```

| 参数                  | 类型            | 默认值              | 说明                      |
| ------------------- | ------------- | ---------------- | ----------------------- |
| `default_top_k`     | `int`         | `10`             | 默认返回数量                  |
| `fts_column`        | `str`         | `"text_content"` | 默认索引列                   |
| `stem`              | `bool`        | `True`           | 英文词干提取                  |
| `remove_stop_words` | `bool`        | `True`           | 去除停用词                   |
| `lower_case`        | `bool`        | `True`           | 转小写                     |
| `tokenizer_type`    | `str`         | `"jieba"`        | `"jieba"` 或 `"default"` |
| `jieba_user_dict`   | `str \| None` | `None`           | 自定义词典路径                 |

***

## 6. 向量搜索 vs 全文搜索

| 维度       | 向量搜索                  | 全文搜索            |
| -------- | --------------------- | --------------- |
| **索引列**  | `float[]` (embedding) | `string` (文本)   |
| **索引类型** | IVF-PQ                | Tantivy BM25    |
| **匹配方式** | 语义相似度 (cosine/l2/dot) | 关键词匹配 + BM25 评分 |
| **查询输入** | embedding 向量          | 自然语言字符串         |
| **精确匹配** | 弱                     | 强               |
| **模糊匹配** | 强                     | 弱               |
| **适用场景** | 语义搜索、RAG、相似推荐         | 关键词搜索、标识符查找     |

**选择向量搜索**: 语义检索、"找关于量子计算的文章"、RAG 检索
**选择全文搜索**: 精确关键词、错误码搜索、术语查找
**选择混合搜索**: 两者兼顾 -- 参见 [06-混合搜索与分面搜索](./06-hybrid-faceted-zh.md)

***

## 7. 元数据过滤 (where 参数)

```python
# 单条件过滤
result = lake.text_search("docs", query="深度学习", where="category = 'AI'")

# 数值过滤
result = lake.text_search("docs", query="NLP", where="quality_score > 0.8")

# 组合条件
result = lake.text_search("docs", query="机器学习",
                          where="category = 'AI' AND year >= 2023")

# OR 条件
result = lake.text_search("docs", query="数据分析",
                          where="category = 'AI' OR category = '数据'")
```

`where` 子句经过 `validate_where_clause` 安全验证，会阻止 SQL 注入和数据修改语句。非法表达式将抛出 `QueryError`。

***

## 8. FTS 索引管理

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data")

# 删除全文搜索索引
lake.delete_fts_index("docs")

# 获取 FTS 索引信息
info = lake.get_fts_index_info("docs")
if info is not None:
    print(f"FTS 索引：{info['name']}, 列：{info['columns']}")
else:
    print("未找到 FTS 索引")
```

***

## 9. REST API

```bash
# 创建 FTS 索引
curl -X POST http://localhost:8000/api/v1/datasets/docs/index/fts \
  -H "Content-Type: application/json" \
  -d '{"fts_column": "text_content", "replace": true}'

# 全文搜索
curl -X POST http://localhost:8000/api/v1/datasets/docs/search/fts \
  -H "Content-Type: application/json" \
  -d '{"query": "机器学习入门", "top_k": 10}'
```

| 端点                        | 请求模型                    | 响应模型                     |
| ------------------------- | ----------------------- | ------------------------ |
| `POST /{name}/index/fts`  | `FtsIndexRequest`       | `FtsIndexResponse`       |
| `POST /{name}/search/fts` | `FullTextSearchRequest` | `FullTextSearchResponse` |
| `POST /embed/text`        | `TextEmbedRequest`      | `EmbeddingResponse`      |
