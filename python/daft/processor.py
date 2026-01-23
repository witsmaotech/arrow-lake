"""
Daft Data Processor
真实的数据处理逻辑实现
"""
import structlog
import daft as df
from typing import Dict, Any, List

logger = structlog.get_logger(__name__)


class DaftProcessor:
    """Daft数据处理核心类"""

    def __init__(self, config):
        """
        初始化处理器

        Args:
            config: 配置对象（包含MinIO、PostgreSQL等配置）
        """
        self.config = config

    def read_data(self, source_config: Dict[str, Any]):
        """
        从各种数据源读取数据

        支持的数据源：
        - MinIO/S3 (CSV, JSON, Parquet)
        - 本地文件 (CSV, JSON, Parquet)
        - PostgreSQL (待实现)

        Args:
            source_config: 数据源配置
                {
                    "type": "minio" | "s3" | "local" | "postgres",
                    "bucket": "bucket-name",  # S3
                    "key": "path/to/file",     # S3
                    "path": "/local/path",    # local
                    "format": "csv" | "json" | "parquet"
                }

        Returns:
            daft.DataFrame: 加载的数据框
        """
        source_type = source_config.get("type", "local")

        try:
            if source_type in ["minio", "s3"]:
                return self._read_from_s3(source_config)
            elif source_type == "local":
                return self._read_from_local(source_config)
            elif source_type == "postgres":
                return self._read_from_postgres(source_config)
            else:
                raise ValueError(f"Unsupported source type: {source_type}")

        except Exception as e:
            logger.error("Failed to read data", source=source_config, error=str(e))
            raise

    def _read_from_s3(self, config: Dict[str, Any]):
        """从S3/MinIO读取数据"""
        bucket = config.get("bucket")
        key = config.get("key")
        s3_url = f"s3://{bucket}/{key}"

        logger.info("Reading from S3", url=s3_url)

        # 构建存储配置
        storage_config = {
            "AWS_ACCESS_KEY_ID": self.config.MINIO_ACCESS_KEY,
            "AWS_SECRET_ACCESS_KEY": self.config.MINIO_SECRET_KEY,
            "AWS_ENDPOINT_URL": f"http://{self.config.MINIO_ENDPOINT}",
            "AWS_REGION": "us-east-1",
            "AWS_ALLOW_HTTP": "true"
        }

        # 根据文件扩展名推断格式
        format_type = self._infer_format_from_path(key) or config.get("format", "csv")

        # 使用Daft读取（支持storage_config参数）
        if format_type == "csv":
            dataframe = df.read_csv(s3_url)
        elif format_type == "json":
            dataframe = df.read_json(s3_url)
        elif format_type == "parquet":
            dataframe = df.read_parquet(s3_url)
        else:
            raise ValueError(f"Unsupported format: {format_type}")

        logger.info("S3 data loaded", rows=len(dataframe))
        return dataframe

    def _read_from_local(self, config: Dict[str, Any]):
        """从本地文件读取数据"""
        path = config.get("path")
        format_type = config.get("format") or self._infer_format_from_path(path)

        logger.info("Reading from local", path=path, format=format_type)

        if format_type == "csv":
            dataframe = df.read_csv(path)
        elif format_type == "json":
            dataframe = df.read_json(path)
        elif format_type == "parquet":
            dataframe = df.read_parquet(path)
        else:
            raise ValueError(f"Unsupported format: {format_type}")

        logger.info("Local data loaded", rows=len(dataframe))
        return dataframe

    def _read_from_postgres(self, config: Dict[str, Any]):
        """从PostgreSQL读取数据"""
        # TODO: 实现PostgreSQL连接器
        raise NotImplementedError(
            "PostgreSQL source not yet implemented. "
            "Use Daft's SQL integration or export to CSV first."
        )

    def apply_transformations(self, dataframe, operations: List[Dict[str, Any]]):
        """
        应用数据转换操作

        支持的操作：
        - filter: 过滤行
        - select: 选择列
        - rename: 重命名列
        - drop: 删除列
        - add_column: 添加计算列
        - aggregate: 聚合操作

        Args:
            dataframe: Daft DataFrame
            operations: 操作列表

        Returns:
            daft.DataFrame: 转换后的数据框
        """
        for operation in operations:
            op_type = operation.get("type")

            try:
                if op_type == "filter":
                    dataframe = self._apply_filter(dataframe, operation)

                elif op_type == "select":
                    columns = operation.get("columns")
                    dataframe = dataframe.select(columns)
                    logger.info("Selected columns", columns=columns)

                elif op_type == "rename":
                    mapping = operation.get("mapping")
                    dataframe = dataframe.rename(mapping)
                    logger.info("Renamed columns", mapping=mapping)

                elif op_type == "drop":
                    columns = operation.get("columns")
                    dataframe = dataframe.drop(columns)
                    logger.info("Dropped columns", columns=columns)

                elif op_type == "add_column":
                    dataframe = self._add_column(dataframe, operation)

                elif op_type == "aggregate":
                    dataframe = self._apply_aggregate(dataframe, operation)

                else:
                    logger.warning("Unknown operation type", op_type=op_type)

            except Exception as e:
                logger.error(
                    "Transformation failed",
                    op_type=op_type,
                    operation=operation,
                    error=str(e)
                )
                raise

        return dataframe

    def _apply_filter(self, dataframe, operation: Dict[str, Any]):
        """应用过滤条件"""
        condition = operation.get("condition")

        # 示例：condition = {"column": "score", "operator": ">", "value": 0.5}
        column = condition.get("column")
        operator = condition.get("operator")
        value = condition.get("value")

        if operator == ">":
            dataframe = dataframe.filter(dataframe[column] > value)
        elif operator == ">=":
            dataframe = dataframe.filter(dataframe[column] >= value)
        elif operator == "<":
            dataframe = dataframe.filter(dataframe[column] < value)
        elif operator == "<=":
            dataframe = dataframe.filter(dataframe[column] <= value)
        elif operator == "==":
            dataframe = dataframe.filter(dataframe[column] == value)
        elif operator == "!=":
            dataframe = dataframe.filter(dataframe[column] != value)
        elif operator == "in":
            dataframe = dataframe.filter(dataframe[column].is_in(value))
        else:
            raise ValueError(f"Unsupported filter operator: {operator}")

        logger.info("Filter applied", column=column, operator=operator, value=value)
        return dataframe

    def _add_column(self, dataframe, operation: Dict[str, Any]):
        """添加计算列"""
        column_name = operation.get("name")
        expression = operation.get("expression")

        # 示例：简单计算列
        # expression = {"type": "arithmetic", "left": "col1", "op": "+", "right": "col2"}

        if expression.get("type") == "arithmetic":
            left = dataframe[expression["left"]]
            op = expression["op"]
            right = dataframe[expression["right"]]

            if op == "+":
                dataframe = dataframe.with_column(column_name, left + right)
            elif op == "-":
                dataframe = dataframe.with_column(column_name, left - right)
            elif op == "*":
                dataframe = dataframe.with_column(column_name, left * right)
            elif op == "/":
                dataframe = dataframe.with_column(column_name, left / right)
            else:
                raise ValueError(f"Unsupported arithmetic operator: {op}")

        elif expression.get("type") == "literal":
            value = expression.get("value")
            dataframe = dataframe.with_column(column_name, df.lit(value))

        else:
            raise ValueError(f"Unsupported expression type: {expression.get('type')}")

        logger.info("Column added", name=column_name)
        return dataframe

    def _apply_aggregate(self, dataframe, operation: Dict[str, Any]):
        """应用聚合操作"""
        group_by = operation.get("group_by", [])
        aggregations = operation.get("aggregations", [])

        if not group_by:
            # 全局聚合
            for agg in aggregations:
                column = agg["column"]
                func = agg["function"]
                alias = agg.get("alias", f"{func}_{column}")

                if func == "count":
                    dataframe = dataframe.agg(dataframe.count()).alias(alias)
                elif func == "sum":
                    dataframe = dataframe.agg(dataframe[column].sum()).alias(alias)
                elif func == "mean":
                    dataframe = dataframe.agg(dataframe[column].mean()).alias(alias)
                elif func == "min":
                    dataframe = dataframe.agg(dataframe[column].min()).alias(alias)
                elif func == "max":
                    dataframe = dataframe.agg(dataframe[column].max()).alias(alias)
                else:
                    logger.warning("Unknown aggregation function", func=func)
        else:
            # 分组聚合（Daft的groupby API）
            grouped = dataframe.groupby(group_by)

            agg_exprs = []
            for agg in aggregations:
                column = agg["column"]
                func = agg["function"]
                alias = agg.get("alias", f"{func}_{column}")

                if func == "count":
                    expr = dataframe.count().alias(alias)
                elif func == "sum":
                    expr = dataframe[column].sum().alias(alias)
                elif func == "mean":
                    expr = dataframe[column].mean().alias(alias)
                else:
                    logger.warning("Unknown aggregation function", func=func)
                    continue

                agg_exprs.append(expr)

            dataframe = grouped.agg(*agg_exprs)

        logger.info("Aggregation applied", group_by=group_by)
        return dataframe

    def write_data(self, dataframe, dest_config: Dict[str, Any]):
        """
        写入数据到目标位置

        支持的目标：
        - MinIO/S3 (CSV, JSON, Parquet)
        - 本地文件 (CSV, JSON, Parquet)
        - LanceDB (通过HTTP API)

        Args:
            dataframe: Daft DataFrame
            dest_config: 目标配置
        """
        dest_type = dest_config.get("type", "local")

        try:
            if dest_type in ["minio", "s3"]:
                self._write_to_s3(dataframe, dest_config)
            elif dest_type == "local":
                self._write_to_local(dataframe, dest_config)
            elif dest_type == "lancedb":
                self._write_to_lancedb(dataframe, dest_config)
            else:
                raise ValueError(f"Unsupported destination type: {dest_type}")

        except Exception as e:
            logger.error("Failed to write data", dest=dest_config, error=str(e))
            raise

    def _write_to_s3(self, dataframe, config: Dict[str, Any]):
        """写入到S3/MinIO"""
        bucket = config.get("bucket")
        key = config.get("key")
        output_path = f"s3://{bucket}/{key}"

        logger.info("Writing to S3", path=output_path)

        # 构建存储配置
        storage_config = {
            "AWS_ACCESS_KEY_ID": self.config.MINIO_ACCESS_KEY,
            "AWS_SECRET_ACCESS_KEY": self.config.MINIO_SECRET_KEY,
            "AWS_ENDPOINT_URL": f"http://{self.config.MINIO_ENDPOINT}",
            "AWS_ALLOW_HTTP": "true"
        }

        # 推断输出格式
        format_type = self._infer_format_from_path(key) or config.get("format", "parquet")

        # 写入数据（推荐Parquet格式，列式存储更高效）
        if format_type == "csv":
            dataframe.write_csv(output_path)
        elif format_type == "json":
            dataframe.write_json(output_path)
        elif format_type == "parquet":
            dataframe.write_parquet(output_path)
        else:
            raise ValueError(f"Unsupported format: {format_type}")

        logger.info("Data written to S3", path=output_path)

    def _write_to_local(self, dataframe, config: Dict[str, Any]):
        """写入到本地文件"""
        path = config.get("path")
        format_type = config.get("format") or self._infer_format_from_path(path)

        logger.info("Writing to local", path=path, format=format_type)

        if format_type == "csv":
            dataframe.write_csv(path)
        elif format_type == "json":
            dataframe.write_json(path)
        elif format_type == "parquet":
            dataframe.write_parquet(path)
        else:
            raise ValueError(f"Unsupported format: {format_type}")

        logger.info("Data written to local", path=path)

    def _write_to_lancedb(self, dataframe, config: Dict[str, Any]):
        """写入到LanceDB（通过HTTP API）"""
        # TODO: 实现LanceDB HTTP客户端调用
        collection = config.get("collection")
        logger.info("Would export to LanceDB", collection=collection)

        # 临时方案：先导出到本地Parquet，然后手动导入
        # 生产方案：调用LanceDB HTTP服务的upsert端点
        raise NotImplementedError(
            "Direct LanceDB export not yet implemented. "
            "Export to Parquet first, then use LanceDB API."
        )

    def _infer_format_from_path(self, path: str) -> str:
        """从文件路径推断格式"""
        path_lower = path.lower()

        if path_lower.endswith(".csv"):
            return "csv"
        elif path_lower.endswith(".json"):
            return "json"
        elif path_lower.endswith(".parquet"):
            return "parquet"
        elif path_lower.endswith(".pq"):
            return "parquet"
        else:
            return None
