# GraphRAG 问答质量 + 孤立顶点修复

> 2026-07-31。诊断 GraphRAG 回答质量不完善 + 图谱大量孤立顶点的两个同源根因并修复。

## 诊断

### 根因①:关系语义在注入 LLM 前被丢光

kg.html 问答主路径 `_build_neighbor_context`(`_lake_kg.py:143`)只用 edge `label`(多数是 `related_to`),不读 `properties.relation_type`。而写入侧 `builder.py:576-590` 明明把每条关系的真实动词写进了 edge properties。结果 LLM 拿到一堆 `[related_to]`,关系动词全丢,回答单薄。

**数据已到位**(snapshot 边带 properties),只是读取处没取。

### 根因②:relation 端点名对不齐 → 关系被丢弃 → 实体孤立

`_insert_kg`(`builder.py:562`)用 `r.source not in entity_id_map` 做精确字符串匹配,而 `entity_id_map` key 和 `relation.source/target` 都是 LLM 抽取的原始字符串(仅 `.strip()`,无 case-fold/空格折叠)。LLM 抽 relation 时 source/target 常用大小写/空格变体(`Alice` vs ` alice `),对不上就被 `continue` 丢弃,端点实体若没别的 entity↔entity 关系,在前端过滤掉 chunk/document 后的 entity 子图里就显示为孤立。

两根因同源:**KG 写入和问答注入都没把"关系"当一等公民**。

## 范围

- **修**:kg.html 主路径(`_lake_kg.py`)+ 写入侧(`builder.py`)。
- **不修(后续)**:`retriever.py`(`/api/v1/rag/query?use_kg=true`)的 predicate 压扁 —— HugeGraph `traverser_kneighbor` API 不返边语义(见 `_traversers.py:31-72`,只返顶点),需另加 `client.get_vertex_edges` 方法。

## 修复①:关系语义注入 LLM

`_lake_kg.py` `_build_neighbor_context` L143:

```python
eprops = e.get("properties") or {}
lbl = eprops.get("relation_type") or e.get("label") or "related_to"
```

`references` 边 properties 为空 → fallback 到 label,行为不变。

## 修复②:端点名规范化

`entity_router.py` 新增 `normalize_name`:

```python
def normalize_name(name: str) -> str:
    return " ".join(str(name or "").casefold().split())
```

`_insert_kg` 内 7 处 key(entity_id_map / typed_id_map / entity_type_map / _vertex_id / relation 匹配 / references 判断)全部用 `normalize_name`。顶点展示名不变(仍存原始 `e.name`)。

**保守强度**:只 casefold + 折叠空白。不做模糊匹配(短名误连风险)。简称/全称不一致需模糊匹配,列为后续。

**已知局限**:per-chunk 粒度下同名实体仍重复写顶点(entity_id_map 不跨 chunk)。彻底去重需"先查后写",列为后续。

## 验证

1. 单测:`test_kg_builder.py`(端点规范化)+ `test_lake_kg_facade.py`(`_build_neighbor_context` relation_type)+ `entity_router` normalize 单测。
2. 重建数据集 KG(`make kg-drop-graph` + `/kg/build`)→ `/kg/stats` 看 vertices/edges 比 + kg.html 看孤立点。
3. 同一问题问答对比(修复后 LLM 看到真实关系动词)。
4. `docker restart arrow-lake-api-1` 生效。
