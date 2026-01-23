"""
LanceDB Index Manager
自动创建和管理向量索引
"""
import structlog
from typing import Optional
from lancedb import Table

logger = structlog.get_logger(__name__)


async def ensure_vector_index(
    table: Table,
    column: str = "vector",
    min_rows_for_index: int = 10000
) -> bool:
    """
    确保向量索引存在

    根据数据规模自动选择合适的索引类型：
    - < 10K rows: 不需要索引
    - 10K - 1M rows: IVF_PQ (平衡性能和压缩)
    - > 1M rows: HNSW (最高召回率)

    Args:
        table: LanceDB表对象
        column: 向量列名
        min_rows_for_index: 创建索引的最小行数

    Returns:
        bool: 是否创建了新索引
    """
    try:
        # 获取表行数
        num_rows = len(table)

        logger.info(
            "Checking vector index",
            table=table.name,
            rows=num_rows,
            column=column
        )

        # 小表不需要索引
        if num_rows < min_rows_for_index:
            logger.info(
                "Table too small for vector index",
                rows=num_rows,
                min_required=min_rows_for_index
            )
            return False

        # 检查现有索引
        try:
            # LanceDB不同版本API可能不同，尝试多种方式
            if hasattr(table, 'index_names'):
                indices = table.index_names
            else:
                # 尝试获取索引信息
                indices = []
                try:
                    index_info = table._index_cache if hasattr(table, '_index_cache') else {}
                    indices = list(index_info.keys())
                except:
                    indices = []

            if any(column in str(idx) for idx in indices):
                logger.info(
                    "Vector index already exists",
                    table=table.name,
                    column=column
                )
                return False
        except Exception as e:
            logger.debug("Could not check existing indices", error=str(e))

        # 根据数据规模选择索引类型
        logger.info("Creating vector index", table=table.name)

        if num_rows < 1_000_000:
            # IVF_PQ: 平衡性能和压缩比
            # num_partitions: 分区数（越多越精确，但越慢）
            # num_sub_vectors: 子向量数（越多压缩越好，但召回率越低）
            num_partitions = min(256, max(2, num_rows // 10000))

            logger.info(
                "Creating IVF_PQ index",
                type="IVF_PQ",
                partitions=num_partitions,
                sub_vectors=16
            )

            table.create_index(
                column,
                index_type="IVF_PQ",
                num_partitions=num_partitions,
                num_sub_vectors=16,
                replace=True
            )
        else:
            # HNSW: 大数据集，最高召回率
            # m: 每个节点的最大连接数
            # ef_construction: 构建时的搜索深度
            logger.info(
                "Creating HNSW index",
                type="HNSW",
                m=32,
                ef_construction=200
            )

            table.create_index(
                column,
                index_type="HNSW",
                m=32,
                ef_construction=200,
                replace=True
            )

        logger.info(
            "Vector index created successfully",
            table=table.name,
            rows=num_rows
        )

        return True

    except Exception as e:
        logger.error(
            "Failed to create vector index",
            table=table.name,
            error=str(e)
        )
        # 不抛出异常，允许系统继续运行
        return False


def optimize_nprobes(table: Table, num_vectors: int, mode: str = "balanced") -> int:
    """
    根据数据规模和模式优化nprobes参数

    nprobes控制搜索的分区数：
    - nprobes=1: 最快，召回率低 (~60%)
    - nprobes=10: 平衡 (~85%)
    - nprobes=50: 慢，召回率高 (~95%)

    Args:
        table: LanceDB表对象
        num_vectors: 向量总数
        mode: 搜索模式 (fast | balanced | accurate)

    Returns:
        int: 推荐的nprobes值
    """
    # 基础nprobes基于数据规模
    if num_vectors < 100_000:
        base_nprobes = 10
    elif num_vectors < 1_000_000:
        base_nprobes = 20
    else:
        base_nprobes = 40

    # 根据模式调整
    mode_multipliers = {
        "fast": 0.5,      # 快速模式：减少nprobes
        "balanced": 1.0,  # 平衡模式：使用基础值
        "accurate": 2.0    # 精确模式：增加nprobes
    }

    multiplier = mode_multipliers.get(mode, 1.0)
    nprobes = int(base_nprobes * multiplier)

    # 确保至少为1
    nprobes = max(1, nprobes)

    logger.debug(
        "Optimized nprobes",
        nprobes=nprobes,
        mode=mode,
        num_vectors=num_vectors
    )

    return nprobes


def should_compact_table(table: Table, threshold_fragments: int = 100) -> bool:
    """
    检查表是否需要压缩

    当删除操作导致碎片文件过多时，需要压缩以提升性能

    Args:
        table: LanceDB表对象
        threshold_fragments: 碎片文件阈值

    Returns:
        bool: 是否需要压缩
    """
    try:
        # 获取片段信息
        if hasattr(table, 'fragments'):
            num_fragments = len(table.fragments)

            logger.info(
                "Table fragment check",
                table=table.name,
                fragments=num_fragments,
                threshold=threshold_fragments
            )

            return num_fragments > threshold_fragments

        return False

    except Exception as e:
        logger.warning("Could not check fragments", error=str(e))
        return False


async def compact_table(table: Table):
    """
    压缩表以优化性能

    合并小文件，删除旧版本数据
    """
    try:
        logger.info("Compacting table", table=table.name)

        # 压缩文件
        if hasattr(table, 'optimize'):
            optimizer = table.optimize()
            optimizer.compact_files()
            logger.info("Files compacted", table=table.name)

        # 清理旧版本
        if hasattr(table, 'cleanup_old_versions'):
            table.cleanup_old_versions(older_than=7)
            logger.info("Old versions cleaned", table=table.name)

        logger.info("Table compacted successfully", table=table.name)

    except Exception as e:
        logger.error(
            "Failed to compact table",
            table=table.name,
            error=str(e)
        )
