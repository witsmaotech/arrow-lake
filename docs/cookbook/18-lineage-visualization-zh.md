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
print(graph.stats)  # {"total_nodes": 42, "total_edges": 51, "max_depth": 3, "truncated": False}
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

打开 `http://127.0.0.1:8000/console/lineage.html`，选择数据集，图谱用 **vis-network**
渲染。`max_nodes` 是 API 截断参数（默认 500，上限 2000），并非切换渲染库的阈值——
`lineage.html` 只加载 vis-network，不引入 G6（G6 仅用于 `kg.html` 的大图场景）。
当真实节点数超出上限时 `stats.truncated=true`，UI 显示横幅提示调大「节点上限」或
减小深度。边标签与节点标题经 HTML 转义，以防经构造标签发起 XSS。

## 5. 操作者溯源（actor）

v1.9.4 起，摄入与删除等写操作会把认证用户（`actor`）透传到血缘记录中，取代之前
的 `actor="system"` 占位。`GET /lineage/history/{dataset}` 的响应里每个事件都携带
`actor` 字段，可用于审计「谁在何时改了这个数据集」。

## 6. 下游影响分析（impact）

`POST /api/v1/lineage/impact` 分析修改某数据集后受影响的下游数据集：

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/lineage/impact" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"dataset_name": "reports"}'
```

返回 `impacted_datasets` 列表，列出每个下游数据集及其依赖路径，便于变更前评估爆炸半径。
