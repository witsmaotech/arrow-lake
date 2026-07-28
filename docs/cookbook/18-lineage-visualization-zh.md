# 血缘可视化（Lineage Visualization）

> 追溯任一数据集的完整上下游图谱，检视列级数据流向，并用节点数上限让大图可浏览。

血缘事件由 Lance 审计轨迹在每个管线状态转换（摄入、验证、分块、嵌入、查询、物化、
清洗）时记录。`/lineage` 端点把它们以可查询图谱暴露；`lineage.html` 控制台页面
负责渲染。

## 1. 获取图谱

```bash
curl "http://127.0.0.1:8000/api/v1/lineage/graph/reports?max_depth=10&max_nodes=500&format=json" \
  -H "X-API-Key: $KEY"
```

- `max_depth` —— BFS 深度上限（默认 10）。
- `max_nodes` —— 返回节点数上限（默认 500，最大 2000）。当真实图谱超出上限时，
  `stats.truncated` 为 `true`，UI 显示横幅提示。
- `format` —— `json`（默认）、`dot` 或 `mermaid`。

```python
from arrow_lake import Lake
lake = Lake.from_yaml("configs/prod.yaml")
graph = lake.lineage_graph("reports", max_depth=10, max_nodes=500)
print(graph.stats)  # {"nodes": 42, "edges": 51, "truncated": False}
```

## 2. 节点着色

节点按类型着色以便区分：

| 类型 | 颜色 | 含义 |
|---|---|---|
| `target` | 绿色 | 被查询的数据集 |
| `source` | 蓝色 | 上游来源 |
| `derived` | 橙色 | 下游派生数据集 |

## 3. 列级血缘

在 `lineage.html` 中点击节点（或直接调用 history）查看哪个源列流向哪个目标列、
经由何种变换：

```bash
curl "http://127.0.0.1:8000/api/v1/lineage/history/reports" -H "X-API-Key: $KEY"
```

每个事件携带可选的 `column_lineage` 列表，含 `{source_column, target_column,
transform_expr}` 映射。若缺失，该节点仅有数据集级血缘。

## 4. 在控制台渲染

打开 `http://127.0.0.1:8000/console/lineage.html`，选择数据集，图谱用 vis-network
（≤2000 节点）或 G6 v4（更大图）渲染。边标签与节点标题经 HTML 转义，以防经构造
标签发起 XSS。
