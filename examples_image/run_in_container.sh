#!/usr/bin/env bash
# 以图搜图 demo 在 api 容器内运行 → dataset 落【容器 catalog】(system-db)→ console 可见可搜。
#
# 为何走容器而非宿主:宿主 Lake() catalog=file:local.db,容器=system-db 服务器(不暴露宿主),
# 宿主建的 dataset 容器 REST 看不见(STORAGE_PATH_NOT_FOUND)。容器内跑走原生 create_dataset,
# catalog 自动正确注册,console(8000/console)立即可选可搜。比手插 turso catalog(方案2)健壮得多。
#
# 前置:CLIP 已缓存 /app/.docling-cache(持久卷);daft/transformers 在 /app/.venv。
# 用法:bash examples_image/run_in_container.sh [--force]
set -euo pipefail
CTR="${ARROW_LAKE_API_CONTAINER:-arrow-lake-api-1}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${ARROW_LAKE_CONTAINER_PY:-/app/.venv/bin/python3}"

echo "→ 容器内运行(stdin 管道喂脚本,绕 read-only rootfs;Lake() 解析容器 catalog/minio;CLIP 走缓存)"
# docker cp 被 read-only rootfs 挡;改 stdin 管道。--force → DEMO_FORCE env(stdin 模式 sys.argv 失效)。
FORCE_ENV="0"
for a in "$@"; do [ "$a" = "--force" ] && FORCE_ENV="1"; done
docker exec -i \
  -e HF_HUB_OFFLINE=1 \
  -e ARROW_LAKE__EMBEDDING__BACKEND=local \
  -e DEMO_FORCE="$FORCE_ENV" \
  "$CTR" "$PY" - < "$HERE/image_search_demo.py"
echo ""
echo "✓ dataset image_search_demo 已入容器 catalog。"
echo "  console: http://127.0.0.1:8000/console/search.html?mode=image → 选 image_search_demo → 以图搜图/文搜图"
