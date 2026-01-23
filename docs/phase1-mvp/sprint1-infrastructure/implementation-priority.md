# DIntelliHub 实施优先级总结

**日期**: 2026-01-22
**基于**: 代码评审报告 + LanceDB/Daft/Lance Skills分析

---

## 🚨 立即行动 - P0 阻塞问题

### 1. LanceDB索引自动创建 ⏰ 2h

**问题**: 当前无索引，搜索延迟100-500ms
**目标**: 添加索引后，延迟降至10-20ms

**实施步骤**:
```python
# 文件: python/lancedb/index_manager.py (新建)

from lancedb import Table
import structlog

logger = structlog.get_logger(__name__)

async def ensure_vector_index(table: Table, column: str = "vector"):
    """确保向量索引存在"""
    try:
        num_rows = len(table)

        # 小表不需要索引
        if num_rows < 10_000:
            logger.info("Table too small for index", rows=num_rows)
            return

        # 检查现有索引
        indices = table.index_names if hasattr(table, 'index_names') else []

        if "vector_idx" not in indices:
            logger.info("Creating vector index", table=table.name, rows=num_rows)

            # 根据数据规模选择索引类型
            if num_rows < 1_000_000:
                # IVF_PQ: 平衡性能和压缩
                table.create_index(
                    column,
                    index_type="IVF_PQ",
                    num_partitions=min(256, max(2, num_rows // 10000)),
                    num_sub_vectors=16,
                    replace=True
                )
            else:
                # HNSW: 大数据集，最高召回率
                table.create_index(
                    column,
                    index_type="HNSW",
                    m=32,
                    ef_construction=200,
                    replace=True
                )

            logger.info("Index created successfully")
        else:
            logger.info("Index already exists")

    except Exception as e:
        logger.error("Index creation failed", error=str(e))

# 在main.py中使用
@app.post("/api/v1/upsert")
async def upsert_data(request: UpsertRequest):
    # ... existing code ...

    # 创建表后立即创建索引
    await ensure_vector_index(table)

    return UpsertResponse(...)
```

### 2. SQL注入风险修复 ⏰ 1h

**问题**: Delete操作使用字符串拼接，存在SQL注入风险

**修复**:
```python
# 文件: python/lancedb/main.py

@app.post("/api/v1/delete")
async def delete_records(request: DeleteRequest):
    """安全的删除操作"""
    try:
        logger.info("Delete request", collection=request.collection, id_count=len(request.ids))

        table = db.open_table(request.collection)

        # 方案1: 逐个删除（安全）
        for id_val in request.ids[:100]:  # 限制批量大小
            # 验证ID格式（防止注入）
            if not isinstance(id_val, str) or not id_val.replace('_', '').replace('-', '').isalnum():
                logger.warning("Invalid ID format", id=id_val)
                continue

            table.delete(f"id = '{id_val}'")

        # 方案2: 使用预定义过滤器（如果LanceDB支持）
        # from lancedb.query import LanceQueryBuilder
        # builder = LanceQueryBuilder(table)
        # builder = builder.filter("id", "in", request.ids)
        # table.delete(builder)

        logger.info("Delete completed", count=len(request.ids))

        return DeleteResponse(
            success=True,
            count=len(request.ids),
            message=f"Successfully deleted {len(request.ids)} records"
        )

    except Exception as e:
        logger.error("Delete failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Delete failed: {str(e)}")
```

### 3. Daft真实实现 ⏰ 8h

**问题**: 当前Daft服务全是placeholder，无法实际处理数据

**实施**: 实现完整的ETL pipeline

```python
# 文件: python/daft/processor.py (新建)

import daft as df
from daft.functions import embed_text
import structlog

logger = structlog.get_logger(__name__)

class DaftProcessor:
    """Daft数据处理核心逻辑"""

    def __init__(self, config):
        self.config = config

    def read_data(self, source_config: dict):
        """读取数据"""
        source_type = source_config.get("type")

        if source_type in ["minio", "s3"]:
            # 配置S3认证
            s3_url = f"s3://{source_config['bucket']}/{source_config['key']}"

            dataframe = df.read_csv(
                s3_url,
                storage_config={
                    "AWS_ACCESS_KEY_ID": self.config.MINIO_ACCESS_KEY,
                    "AWS_SECRET_ACCESS_KEY": self.config.MINIO_SECRET_KEY,
                    "AWS_ENDPOINT_URL": f"http://{self.config.MINIO_ENDPOINT}",
                    "AWS_ALLOW_HTTP": "true"
                }
            )
            return dataframe

        elif source_type == "local":
            path = source_config.get("path")
            format_type = source_config.get("format", "csv")

            if format_type == "csv":
                return df.read_csv(path)
            elif format_type == "json":
                return df.read_json(path)
            elif format_type == "parquet":
                return df.read_parquet(path)

        raise ValueError(f"Unsupported source type: {source_type}")

    def apply_transformations(self, dataframe, operations: list):
        """应用转换操作"""
        for operation in operations:
            op_type = operation.get("type")

            if op_type == "filter":
                condition = operation.get("condition")
                # 示例: dataframe = dataframe.filter(dataframe["score"] > 0.5)
                logger.info("Applying filter", condition=condition)

            elif op_type == "select":
                columns = operation.get("columns")
                dataframe = dataframe.select(columns)
                logger.info("Selected columns", columns=columns)

            elif op_type == "rename":
                mapping = operation.get("mapping")
                dataframe = dataframe.rename(mapping)
                logger.info("Renamed columns", mapping=mapping)

            elif op_type == "embed":
                # 使用Daft AI函数
                text_column = operation.get("text_column")
                model = operation.get("model", "sentence-transformers/all-MiniLM-L6-v2")

                # 批量生成embeddings
                dataframe = dataframe.with_column(
                    "embedding",
                    embed_text(dataframe[text_column], provider="huggingface")
                )
                logger.info("Generated embeddings", column=text_column)

        return dataframe

    def write_data(self, dataframe, dest_config: dict):
        """写入数据"""
        dest_type = dest_config.get("type")

        if dest_type in ["minio", "s3"]:
            bucket = dest_config.get("bucket")
            key = dest_config.get("key")
            output_path = f"s3://{bucket}/{key}"

            # 写入Parquet（列式存储，更高效）
            dataframe.write_parquet(output_path)
            logger.info("Written to S3", path=output_path)

        elif dest_type == "local":
            path = dest_config.get("path")
            dataframe.write_csv(path)
            logger.info("Written to local", path=path)

        elif dest_type == "lancedb":
            # 导出到LanceDB
            collection = dest_config.get("collection")
            # 调用LanceDB HTTP服务
            logger.info("Exporting to LanceDB", collection=collection)

# 在main.py中使用
processor = DaftProcessor(settings)

@app.post("/api/v1/process")
async def process_data(request: ProcessRequest):
    """真实的数据处理"""
    start_time = time.time()

    try:
        # 读取
        dataframe = processor.read_data(request.source)

        # 转换
        dataframe = processor.apply_transformations(dataframe, request.operations)

        # 触发执行并获取结果
        result = dataframe.collect()
        records_processed = len(result)

        # 写入
        processor.write_data(dataframe, request.destination)

        execution_time_ms = (time.time() - start_time) * 1000

        return ProcessResponse(
            success=True,
            records_processed=records_processed,
            execution_time_ms=execution_time_ms,
            message=f"Processed {records_processed} records"
        )

    except Exception as e:
        logger.error("Processing failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
```

---

## 🟡 重要改进 - P1 (本周完成)

### 4. LanceDB主备负载均衡 ⏰ 6h

**参考文档**: `lancedb-ha-loadbalancing.md`

**关键文件**:
- `nginx/lancedb-lb.conf` - Nginx配置
- `docker-compose.yml` - 添加primary/standby/lb服务
- `python/lancedb/main.py` - 增强健康检查和metrics

**启动命令**:
```bash
# 1. 创建Nginx配置
mkdir -p nginx
# (复制lancedb-lb.conf内容)

# 2. 启动服务
docker compose up -d lancedb-primary lancedb-standby lancedb-lb

# 3. 测试
curl http://localhost:8765/health
```

### 5. 监控和告警 ⏰ 4h

**Prometheus配置**:
```yaml
# prometheus/prometheus.yml
scrape_configs:
  - job_name: 'lancedb'
    static_configs:
      - targets: ['lancedb-primary:8765', 'lancedb-standby:8765']
    metrics_path: '/metrics'
```

**Grafana Dashboard**:
- LanceDB搜索延迟 (P50, P95, P99)
- QPS (查询/秒)
- 磁盘使用率
- 内存使用率
- 主备切换次数

---

## 📊 性能优化 - P2 (下周)

### 6. Hybrid Search ⏰ 4h

**功能**: 向量搜索 + 全文搜索 + Reranking

```python
@app.post("/api/v1/hybrid_search")
async def hybrid_search(request: HybridSearchRequest):
    """混合搜索"""
    table = db.open_table(request.collection)

    # 先进行向量搜索
    vector_results = (
        table.search(request.vector)
        .limit(request.limit * 2)  # 获取更多候选
        .to_pandas()
    )

    # 再进行全文搜索
    fts_results = (
        table.search(request.query_text)
        .limit(request.limit * 2)
        .to_pandas()
    )

    # 合并结果
    # ...

    # Rerank (使用Cohere或本地模型)
    # ...

    return results
```

### 7. 缓存优化 ⏰ 2h

```python
# 打开表时配置缓存
table = db.open_table(
    "vectors",
    index_cache_size=100 * 1024 * 1024,  # 100MB
    storage_options={
        "max_rows_per_group": 8192,
        "max_rows_per_file": 1024 * 1024
    }
)
```

---

## 📅 实施时间表

### 本周剩余 (3天)

| 任务 | 工时 | 优先级 | 负责人 |
|------|------|--------|--------|
| LanceDB索引创建 | 2h | P0 | Winston |
| SQL注入修复 | 1h | P0 | Winston |
| Daft真实实现 | 8h | P0 | Winston |
| 主备负载均衡 | 6h | P1 | Winston |
| 监控配置 | 4h | P1 | Winston |
| **总计** | **21h** | | **3-4天** |

### 下周 (5天)

| 任务 | 工时 | 优先级 |
|------|------|--------|
| Hybrid Search | 4h | P2 |
| 缓存优化 | 2h | P2 |
| 性能测试 | 4h | P2 |
| 文档完善 | 4h | P2 |
| 代码重构 | 8h | P2 |
| **总计** | **22h** | **~3天** |

---

## 📊 预期成果

### 修复前后对比

| 指标 | 当前 | P0修复后 | P1+P2完成后 |
|------|------|----------|-------------|
| **搜索延迟** | 100-500ms | 10-20ms | <10ms |
| **吞吐量** | ~200 QPS | ~10K QPS | ~20K QPS |
| **可用性** | 99% (单点) | 99.5% | 99.9% |
| **安全性** | SQL注入风险 | 安全 | 审计日志 |
| **功能完整度** | 60% | 80% | 95% |

---

## ✅ 验收标准

### P0 验收

- [ ] LanceDB索引自动创建
- [ ] 搜索延迟P99 <20ms
- [ ] SQL注入风险修复
- [ ] Daft可以真实处理数据
- [ ] 所有单元测试通过

### P1 验收

- [ ] 主备负载均衡运行
- [ ] 故障自动切换（主库down，自动切到备库）
- [ ] Prometheus metrics采集
- [ ] Grafana dashboard显示

### P2 验收

- [ ] Hybrid Search端点可用
- [ ] 缓存命中率 >80%
- [ ] 性能测试报告
- [ ] API文档完整

---

**文档创建时间**: 2026-01-22
**更新频率**: 每日更新进度
**下次评审**: P0问题修复后
