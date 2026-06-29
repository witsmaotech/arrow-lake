"""Arrow Lake v1.8.0 新特性合集示例（cookbook #41）.

逐个演示 v1.8.0 落地的 11 项新能力（roadmap 收尾 + 三批）。每个 demo 独立、
自带 try/except —— 缺模型 / 缺外部服务（Gravitino / HF 网络 / VLM）时优雅
跳过并打印 [SKIP]，本地 Lance 能力（branches / blob / row lineage）开箱可跑。

用法:
    python 41_v1_8_0_new_features.py            # 跑全部
    python 41_v1_8_0_new_features.py branches   # 只跑指定 demo

对应文档: docs/cookbook/16-v1.8.0-new-features-zh.md
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

import pyarrow as pa

from arrow_lake import Lake


def _setup_lake() -> tuple[Lake, Path]:
    """建一个临时本地 Lake 并写入一个小文本数据集，供后续 demo 复用。

    强制 LOCAL backend：示例应脱离 MinIO/S3 独立运行，故用环境变量覆盖
    项目 .env 里可能的 S3 凭据（pydantic-settings 优先级：env > .env）。
    """
    import os

    os.environ.setdefault("ARROW_LAKE__STORAGE__BACKEND", "local")
    tmp = Path(tempfile.mkdtemp(prefix="al_v180_"))
    lake = Lake(str(tmp / "lake"))
    tbl = pa.table(
        {
            "id": [1, 2, 3],
            "text": [
                "Arrow Lake 是统一多模态数据湖仓",
                "Lance 列式存储支持向量与标量索引",
                "Daft 提供 lazy DataFrame 与内置 AI 函数",
            ],
            "category": ["intro", "storage", "compute"],
        }
    )
    try:
        lake.delete_dataset("docs")  # 保险：理论上 fresh 目录不会命中
    except Exception:
        pass
    lake.create_dataset("docs", tbl)
    return lake, tmp


# --------------------------------------------------------------------------- #
# 1. CLIP 跨模态（#6）—— 文搜图
# --------------------------------------------------------------------------- #
def demo_clip_cross_modal(lake: Lake) -> None:
    print("\n[1] CLIP 跨模态 encode_text_clip（#6）—— 用文本查询编入图像嵌入空间")
    try:
        qvecs = lake.encode_text_clip(["a cat sitting on a sofa"], model="openai/clip-vit-base-patch32")
        print(f"    ✓ encode_text_clip 返回 {len(qvecs)} 条向量，dim={len(qvecs[0])}")
        print("    → 用法: lake.search('photos', qvecs[0], vector_column='image_embedding')")
        print("      （需先有含 image_embedding 列的数据集；此处仅演示编码）")
    except Exception as e:  # 模型未下载 / 无网络 / 无 torch
        print(f"    [SKIP] 无 CLIP 模型或网络: {type(e).__name__}")


# --------------------------------------------------------------------------- #
# 2. Lance 数据集 branches（#1）—— 数据版本治理
# --------------------------------------------------------------------------- #
def demo_lance_branches(lake: Lake) -> None:
    print("\n[2] Lance dataset branches（#1）—— 数据版本治理（本地可跑）")
    try:
        lake.create_branch("docs", "experiment-a")  # 默认从 HEAD
        lake.create_branch("docs", "v1_8_baseline")  # 名字须匹配 ^[a-zA-Z_][a-zA-Z0-9_-]*$（不含点）
        print(f"    ✓ branches = {lake.list_branches('docs')}")
        head = lake.read_at_branch("docs", "experiment-a")
        print(f"    ✓ read_at_branch('experiment-a') 行数 = {head.num_rows}")
        lake.delete_branch("docs", "v1_8_baseline")
        print(f"    ✓ 删除后 branches = {lake.list_branches('docs')}")
    except Exception as e:
        print(f"    [SKIP] {type(e).__name__}: {e}")


# --------------------------------------------------------------------------- #
# 3. blob 存储（#2）—— 原始媒体字节入 Lance
# --------------------------------------------------------------------------- #
def demo_blob_column(lake: Lake) -> None:
    print("\n[3] add_blob_column（#2）—— image/audio/video bytes 原地存为 Lance 列（本地可跑）")
    try:
        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64  # 假装一张图的字节
        lake.add_blob_column("docs", "image_bytes", [fake_png, fake_png, fake_png])
        tbl = lake.read_dataset("docs")
        print(f"    ✓ 已加 blob 列 'image_bytes'，现 schema 列 = {tbl.column_names}")
    except Exception as e:
        print(f"    [SKIP] {type(e).__name__}: {e}")


# --------------------------------------------------------------------------- #
# 4. hf:// 数据集（#8）—— 读 HuggingFace Lance-format
# --------------------------------------------------------------------------- #
def demo_hf_dataset(lake: Lake) -> None:
    print("\n[4] load_hf_dataset（#8）—— lancedb hf:// scheme 读 HF Lance 数据集")
    try:
        tbl = lake.load_hf_dataset("lance-format/quora-duplicates-embedding-bge-small", table="quora")
        print(f"    ✓ 加载 HF 数据集，行数 = {tbl.num_rows}")
    except Exception as e:  # 无网络 / 数据集不存在
        print(f"    [SKIP] 无网络或数据集不可达: {type(e).__name__}")


# --------------------------------------------------------------------------- #
# 5. VLM decode_image（#18）—— 多模态变换管道
# --------------------------------------------------------------------------- #
def demo_vlm_decode(lake: Lake) -> None:
    print("\n[5] decode_image 变换（#18）—— VLM 链：image bytes → 解码 → classify/prompt")
    try:
        import daft
        from arrow_lake.ingest import transforms as T

        # build_transforms 签名是 list[dict]（每个 dict 一个变换 spec）
        transforms = T.build_transforms([{"op": "decode_image", "column": "image_bytes"}])
        print(f"    ✓ 构造 {len(transforms)} 个变换（decode_image）")
        # 真正 apply 到一个含 image_bytes 的 Daft DataFrame
        df = daft.from_pydict({"id": [1], "image_bytes": [b"\x89PNG\r\n\x1a\n" + b"\x00" * 32]})
        for t in transforms:
            df = t(df)
        df = df.collect()
        print(f"    ✓ decode_image 已 apply + collect，结果列: {df.column_names}")
        print("    → 真实图像配合 classify_image / prompt 完成理解；lake.ingest(..., transforms=[...]) 摄取期自动解码")
    except Exception as e:
        print(f"    [SKIP] {type(e).__name__}: {str(e)[:90]}")


# --------------------------------------------------------------------------- #
# 6. Gravitino 统一 catalog facade（#19）
# --------------------------------------------------------------------------- #
def demo_gravitino_facade(lake: Lake) -> None:
    print("\n[6] Gravitino facade（#19）—— 三引擎经 Gravitino 统一 catalog")
    try:
        lake.gravitino_register_dataset("docs")  # 注册为 Gravitino Table + Fileset
        print("    ✓ gravitino_register_dataset('docs')")
        try:
            stats = lake.table_statistics("docs")
            print(f"    ✓ table_statistics = {stats}")
        except Exception:
            print("    (table_statistics 需 Gravitino 已抓取统计)")
    except Exception as e:
        print(f"    [SKIP] Gravitino 未运行: {type(e).__name__}")


# --------------------------------------------------------------------------- #
# 7. Daft ↔ Gravitino（#14）—— Daft 直连联邦查询
# --------------------------------------------------------------------------- #
def demo_daft_gravitino(lake: Lake) -> None:
    print("\n[7] daft_from_gravitino（#14）—— Daft GravitinoConfig 直连，不经 DuckDB")
    try:
        url = "http://localhost:8090"
        metalake = "arrow_lake"
        df = lake.daft_from_gravitino("docs", url=url, metalake=metalake)
        print(f"    ✓ 拿到 lazy Daft DataFrame: {type(df).__name__}")
        print("    → df.collect() 触发执行；联邦查询直接在 Daft 侧完成")
    except Exception as e:
        print(f"    [SKIP] Gravitino/Daft 连接不可用: {type(e).__name__}")


# --------------------------------------------------------------------------- #
# 8. 行级 lineage（#3）—— row_id 精细溯源
# --------------------------------------------------------------------------- #
def demo_row_lineage(lake: Lake) -> None:
    print("\n[8] lineage_record_row（#3）—— Lance row_id 行级血缘（本地可跑）")
    try:
        lake.lineage_record_row(
            "docs",
            row_id=2,
            source_rows=[{"dataset": "raw_docs", "row_id": 42}],
            operation="derive",
        )
        print("    ✓ 已记录 row 2 ← raw_docs:42（derive）")
        print("    → 经现有 lineage store / query / graph 可查行级溯源")
    except Exception as e:
        print(f"    [SKIP] {type(e).__name__}: {e}")


# --------------------------------------------------------------------------- #
# 9. Daft 流式写（#16）—— >16× 内存
# --------------------------------------------------------------------------- #
def demo_write_dataframe(lake: Lake) -> None:
    print("\n[9] write_lance_from_dataframe（#16）—— Daft lazy DataFrame 直接写 Lance")
    try:
        import daft

        df = daft.from_pydict({"id": [10, 11], "text": ["流式写一行", "超内存友好"]})
        storage = lake._get_storage()
        storage.write_lance_from_dataframe("docs_daft", df, mode="create")
        out = lake.read_dataset("docs_daft")
        print(f"    ✓ Daft DataFrame → Lance，行数 = {out.num_rows}")
    except Exception as e:
        print(f"    [SKIP] Daft 不可用或写入失败: {type(e).__name__}")


# --------------------------------------------------------------------------- #
# 10. 全链路 async 检索（#17）
# --------------------------------------------------------------------------- #
async def demo_async_search(lake: Lake) -> None:
    print("\n[10] *_async 检索（#17）—— fts/hybrid/faceted 非阻塞包装")
    try:
        lake.create_fts_index("docs", fts_column="text")  # async FTS 需先建索引
    except Exception:
        pass  # 已存在或不可建，忽略
    try:
        res = await lake.text_search_async("docs", "数据湖仓", top_k=2)
        print(f"    ✓ text_search_async 命中 = {getattr(res, 'total', '?')}")
    except Exception as e:
        print(f"    [SKIP] text_search_async: {type(e).__name__}: {e}")
    try:
        # hybrid 需要向量 + 文本；用零向量占位演示接口
        res = await lake.hybrid_search_async("docs", [0.0] * 8, "Lance", top_k=2)
        print(f"    ✓ hybrid_search_async 命中 = {getattr(res, 'total', '?')}")
    except Exception as e:
        print(f"    [SKIP] hybrid_search_async: {type(e).__name__}")


# --------------------------------------------------------------------------- #
# 11. 日文分词（#4）—— lindera
# --------------------------------------------------------------------------- #
def demo_japanese_tokenizer(lake: Lake) -> None:
    print("\n[11] 日文分词 lindera（#4）—— FTS 多语言分词")
    try:
        # lindera 是可选分词器；create_fts_index 时按配置启用，未装则优雅降级。
        # 此处演示建一个含日文的子集并尝试分词（lindera 未装则 SKIP）。
        ja = pa.table({"id": [1], "text": ["東京の天気は晴れです。"], "category": ["ja"]})
        lake.create_dataset("docs_ja", ja)
        try:
            lake.create_fts_index("docs_ja", fts_column="text", tokenizer="lindera")
            print("    ✓ lindera 日文分词索引已建")
        except TypeError:
            # 该 build 的 create_fts_index 无 tokenizer 形参时，走配置层启用
            print("    → lindera 经 FullTextSearchConfig 分词器配置启用（此 build 无形参）")
        except Exception as e:
            print(f"    [SKIP] lindera 未安装: {type(e).__name__}")
    except Exception as e:
        print(f"    [SKIP] {type(e).__name__}: {e}")


DEMOS = {
    "clip": demo_clip_cross_modal,
    "branches": demo_lance_branches,
    "blob": demo_blob_column,
    "hf": demo_hf_dataset,
    "vlm": demo_vlm_decode,
    "gravitino": demo_gravitino_facade,
    "daft-gravitino": demo_daft_gravitino,
    "row-lineage": demo_row_lineage,
    "write-daft": demo_write_dataframe,
    "japanese": demo_japanese_tokenizer,
}


async def _run_async(lake: Lake) -> None:
    await demo_async_search(lake)


def main(only: str | None = None) -> None:
    lake, tmp = _setup_lake()
    print(f"=== Arrow Lake v1.8.0 新特性 demo（临时 Lake: {tmp}） ===")
    try:
        for name, fn in DEMOS.items():
            if only and only != name:
                continue
            fn(lake)
        if not only:
            asyncio.run(_run_async(lake))
        elif only == "async":
            asyncio.run(_run_async(lake))
    finally:
        lake.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
        print(f"\n=== 清理临时目录 {tmp} ===")


if __name__ == "__main__":
    import sys

    main(sys.argv[1] if len(sys.argv) > 1 else None)
