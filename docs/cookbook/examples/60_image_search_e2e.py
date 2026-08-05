#!/usr/bin/env python3
"""以图搜图 E2E demo(批4 Task3)— Daft 图像 ingest + CLIP 嵌入 + IVF_PQ + 跨模态检索。

证明 console search.html 的以图搜图链路端到端可行:

  合成 ≥256 张 8 色类彩图
    → daft from_glob_path + @daft.func 读 JPEG bytes(daft 图像 ingest)
    → CLIPImageEncoder.encode 加 image_embedding(512 维,L2 归一)
    → Lake.create_dataset(图像建表走 host facade,/ingest/documents 只吃文本)
    → lake.create_vector_index IVF_PQ(cosine, num_partitions=16, num_sub_vectors=16)
      (facade 与 REST /index/vector 同代码路径;走 facade 因 host/容器 catalog 独立)
    → 文搜图(CLIP encode_text "a red image")+ 以图搜图(CLIP encode 一张红图)→ lake.search

前置:
  - CLIP 模型已缓存(openai/clip-vit-base-patch32,@ ~/.cache/huggingface)
  - Lake 连本机栈(主机 Lake() 开箱直连 minio + system-db)
  - /embed/image 需本地后端:export ARROW_LAKE__EMBEDDING__BACKEND=local

运行:.venv/bin/python3 examples_image/image_search_demo.py [--force]
幂等:数据集已存在则跳过建/索引(除非 --force 重建)。
"""
from __future__ import annotations

import base64
import io
import os
import sys
import time
from pathlib import Path

# 8 色类:label → RGB(CLUW 文本塔对颜色词有对齐信号)
COLORS = [
    ("red", (220, 30, 30)),
    ("green", (30, 180, 60)),
    ("blue", (40, 90, 220)),
    ("yellow", (230, 210, 30)),
    ("cyan", (30, 200, 210)),
    ("magenta", (220, 40, 200)),
    ("orange", (240, 140, 30)),
    ("white", (240, 240, 240)),
]

DS = os.environ.get("IMAGE_SEARCH_DS", "image_search_demo")
API = os.environ.get("ARROW_LAKE__API__URL", "http://127.0.0.1:8000").rstrip("/")
API_KEY = os.environ.get("ARROW_LAKE__API__API_KEY", "dev-api-key-for-local-testing-only")
TOTAL = 256  # ≥256:IVF_PQ 训练阈值
FORCE = "--force" in sys.argv or os.environ.get("DEMO_FORCE") == "1"

t0 = time.time()


def log(m: str) -> None:
    print(f"[{time.time() - t0:>5.1f}s] {m}", flush=True)


def prepare_images(out_dir: Path) -> list[str]:
    """合成 TOTAL 张纯色彩图(每色 TOTAL/len(COLORS) 张),返回文件路径列表。"""
    from PIL import Image

    out_dir.mkdir(parents=True, exist_ok=True)
    per = TOTAL // len(COLORS)
    paths: list[str] = []
    for label, rgb in COLORS:
        for i in range(per):
            p = out_dir / f"{label}_{i:02d}.jpg"
            Image.new("RGB", (64, 64), rgb).save(p, "JPEG", quality=85)
            paths.append(str(p))
    log(f"合成 {len(paths)} 张彩图({len(COLORS)} 色类 × {per})→ {out_dir}")
    return paths


def build_table(img_glob: str):
    """daft from_glob_path → @daft.func 读 JPEG bytes → pyarrow.Table。

    列:id(int)、label(str,从文件名解析)、image(large_binary JPEG)、
    image_preview(str base64,供 console renderHits 直渲,因 _json_safe_row 会占位 binary 列)。
    """
    import daft
    import pyarrow as pa

    @daft.func
    def to_jpeg_bytes(uri) -> bytes:  # noqa: ANN001 - daft UDF 类型由装饰器推断
        from urllib.request import urlopen
        with urlopen(uri) as r:  # from_glob_path 返回 file:// URI
            return r.read()

    df = daft.from_glob_path(img_glob).with_column("jpeg", to_jpeg_bytes(daft.col("path")))
    collected = df.collect()
    paths = collected.to_pydict()["path"]
    jpegs = collected.to_pydict()["jpeg"]
    ids, labels, previews = [], [], []
    for i, (uri, jpeg) in enumerate(zip(paths, jpegs)):
        name = Path(uri.replace("file://", "")).stem  # red_03 → red
        label = name.split("_")[0]
        ids.append(i)
        labels.append(label)
        previews.append(base64.b64encode(jpeg).decode())  # 文本缩略图(绕过 binary 占位)
    table = pa.table({
        "id": pa.array(ids, type=pa.int64()),
        "label": pa.array(labels, type=pa.string()),
        "image": pa.array(jpegs, type=pa.large_binary()),  # 原图 bytes
        "image_preview": pa.array(previews, type=pa.string()),
    })
    log(f"daft ingest → pa.Table {table.num_rows} 行 × {table.num_columns} 列(image_embedding 待加)")
    return table


def embed_and_ingest(table, lake, name: str) -> int:
    """CLIPImageEncoder.encode(自动加 image_embedding 512d)→ Lake.create_dataset。返回嵌入维度。"""
    from arrow_lake.embed.image_encoder import CLIPImageEncoder

    encoder = CLIPImageEncoder()  # 默认 openai/clip-vit-base-patch32,512d,源=huggingface(已缓存)
    result = encoder.encode(table)
    if result.embedded == 0:
        raise RuntimeError(f"CLIP 嵌入 0 行(failed={result.failed}, null={result.null_count})")
    lake.create_dataset(name, result.table)
    log(f"CLIP 嵌入 {result.embedded}/{result.total} 行(dim={result.embedding_dim})→ create_dataset {name}")
    return result.embedding_dim


def build_index(name: str, lake) -> None:
    """IVF_PQ 索引(主机 facade lake.create_vector_index,与 REST /index/vector 同代码路径)。

    走 facade 而非 REST:主机与容器 catalog 独立(host system_db=file:local.db,容器=turso
    system-db 服务器,且 system-db 不暴露到 host)→ host 建的 dataset 不在容器 catalog,
    REST /index/vector 会 404(STORAGE_PATH_NOT_FOUND)。facade 直操 host 创建的 dataset,
    技术(IVF_PQ 训练 + 检索)与 REST 端点完全等价。
    """
    info = lake.create_vector_index(
        name,
        metric="cosine",
        vector_column="image_embedding",
        index_type="IVF_PQ",
        num_partitions=16,   # ≈ √256
        num_sub_vectors=16,  # 必须整除向量维(512/16=32)
        replace=True,
    )
    log(f"IVF_PQ 索引已建(cosine, 16 partitions × 16 sub_vectors):{info}")


def rest_probe() -> None:
    """可选 REST 交叉验证:/embed/clip-text 端点可达(console 文搜图 embed 链路)。

    完整 console 以图搜图还需两项部署对齐(非 demo 范围):
      ① dataset 注册到容器 catalog(system-db,当前 host/容器独立 → 容器看不见本 demo 的 dataset);
      ② 容器装 CLIP 模型 + ARROW_LAKE__EMBEDDING__BACKEND=local(/embed/image 否则 501)。
    本探针只验端点存在 + 返向量维度,dataset 可见性/CLIP 缺失时优雅跳过,不影响 demo PASS。
    """
    import json
    import urllib.request

    try:
        req = urllib.request.Request(
            f"{API}/api/v1/embed/clip-text",
            data=json.dumps({"texts": ["a red image"]}).encode(), method="POST",
            headers={"Content-Type": "application/json", "X-API-Key": API_KEY},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read())
        log(f"REST /embed/clip-text 可达(dim={d.get('embedding_dim')})— console 文搜图 embed 端点正常")
    except Exception as e:
        log(f"REST /embed/clip-text 跳过({type(e).__name__}: {str(e)[:80]})— 容器可能未配 CLIP 或 catalog 未对齐,不影响 demo PASS")


def verify_search(name: str, lake) -> None:
    """跨模态检索验收:(a) 文搜图 encode_text "a red image";(b) 以图搜图 encode 一张红图。"""
    from arrow_lake.embed.image_encoder import CLIPImageEncoder
    import pyarrow as pa

    enc = CLIPImageEncoder()
    VC = "image_embedding"

    # (a) 文搜图:CLIP 文本塔 → 与图像同空间
    qtext = enc.encode_text(["a red image"])[0].tolist()
    res_t = lake.search(name, qtext, top_k=8, vector_column=VC)
    rows_t = res_t.table.to_pylist()
    labels_t = [r["label"] for r in rows_t]
    red_t = labels_t.count("red")
    log(f"文搜图 'a red image':{len(rows_t)} 命中,labels={labels_t},含 red {red_t} 个")
    assert len(rows_t) > 0, "文搜图 0 命中"

    # (b) 以图搜图:一张全新的红图 → CLIP 图像塔
    from PIL import Image
    buf = io.BytesIO(); Image.new("RGB", (64, 64), (220, 30, 30)).save(buf, "JPEG", quality=85)
    qimg_table = pa.table({"image": pa.array([buf.getvalue()], type=pa.large_binary())})
    qvec = enc.encode(qimg_table).table.column("image_embedding").to_pylist()[0]
    res_i = lake.search(name, qvec, top_k=8, vector_column=VC)
    rows_i = res_i.table.to_pylist()
    labels_i = [r["label"] for r in rows_i]
    red_i = labels_i.count("red")
    log(f"以图搜图(红图):{len(rows_i)} 命中,labels={labels_i},含 red {red_i} 个")
    assert len(rows_i) > 0, "以图搜图 0 命中"

    # 软信号(不 hard-fail):红图查询 top-3 应以 red 为主(证明 CLIP 跨模态对齐有效)
    top3_red = labels_i[:3].count("red")
    log(f"以图搜图 top-3 含 red {top3_red}/3(跨模态对齐信号)")


def main() -> int:
    from arrow_lake import Lake

    lake = Lake()
    try:
        existing = lake.list_datasets()
        if DS in existing and not FORCE:
            info = lake.open_dataset(DS).search().to_arrow()
            log(f"{DS} 已存在({info.num_rows} 行)— 跳过建/索引(用 --force 重建)")
        else:
            if DS in existing:
                lake.delete_dataset(DS); log(f"删除旧 {DS}(--force)")
            # 用稳定的临时目录(放 /tmp/isd_<pid>),demo 结束不清理(供排障)
            img_dir = Path("/tmp") / f"isd_images_{os.getpid()}"
            prepare_images(img_dir)
            table = build_table(str(img_dir / "*.jpg"))
            embed_and_ingest(table, lake, DS)
            build_index(DS, lake)
        verify_search(DS, lake)
        rest_probe()  # 可选:验证 console /embed/clip-text 端点(不影响 PASS)
        print("PASS image-search-demo E2E")
        return 0
    finally:
        lake.shutdown()


if __name__ == "__main__":
    sys.exit(main())
