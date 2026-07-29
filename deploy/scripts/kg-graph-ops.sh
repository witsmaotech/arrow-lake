#!/usr/bin/env bash
# HugeGraph KG graph 运维: clear/drop per-dataset 图 (kg_{DS}) + restart hg-server + 等 healthy。
#
# 解决: HugeGraph 1.7 运行期 clear/drop 动态图后, GraphManager 内存 schema 缓存不刷新
# (只改了持久层 rocksdb) → 后续 ensure_schema 的 propertykeys POST 返回 500(脏态, 非 400 已存在)
# → KG build FAILED。restart hg-server 让 GraphManager 从 rocksdb 重载, 恢复一致。
# (本会话实证: clear kg_wuhu_report 后 propertykeys 500, restart 后 200, KG 成功 9829v/24936e)
#
# 用法:
#   bash deploy/scripts/kg-graph-ops.sh <clear|drop> <dataset>
#   make kg-clear-graph DS=wuhu_report   # 清数据留 shell
#   make kg-drop-graph  DS=wuhu_report   # 删注册+schema+数据
set -euo pipefail

OP="${1:?usage: $0 <clear|drop> <dataset>}"
DS="${2:?usage: $0 <clear|drop> <dataset>}"

# 项目根 (脚本可能从 deploy/ 或 repo root 调)
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"

# HugeGraph 连接 (默认本机 prod_minimal; 可 env 覆盖)
export ARROW_LAKE__HUGEGRAPH__ENABLED="${ARROW_LAKE__HUGEGRAPH__ENABLED:-true}"
export ARROW_LAKE__HUGEGRAPH__HOST="${ARROW_LAKE__HUGEGRAPH__HOST:-127.0.0.1}"
export ARROW_LAKE__HUGEGRAPH__PORT="${ARROW_LAKE__HUGEGRAPH__PORT:-8089}"
export ARROW_LAKE__HUGEGRAPH__USERNAME="${HUGEGRAPH_USERNAME:-admin}"
export ARROW_LAKE__HUGEGRAPH__PASSWORD="${HG_SERVER_PASSWORD:-pa}"

PROJECT="${PROJECT_NAME:-arrow-lake}"
COMPOSE="docker compose --project-directory deploy -p ${PROJECT} -f deploy/docker-compose.prod_minimal.yml"

GRAPH="kg_${DS}"
echo "→ ${OP} ${GRAPH} (via HugeGraph client, best-effort)"

# 用 host .venv 的 Lake facade 调 client clear/drop (路径+auth+confirm_message 由 client 处理)
.venv/bin/python3 - "$OP" "$DS" <<'PYEOF'
import asyncio, sys
from arrow_lake import Lake
op, ds = sys.argv[1], sys.argv[2]
async def _run():
    lake = Lake()
    try:
        if op == "clear":
            await lake.kg_delete_graph(ds)   # clear: 删数据留 shell
        else:
            await lake.kg_drop_graph(ds)     # drop: 删注册+schema+数据
    except Exception as e:  # 图不存在等 best-effort, 不阻断 restart
        print(f"  (client {op} best-effort: {e})", file=sys.stderr)
    lake.shutdown()
asyncio.run(_run())
PYEOF

echo "→ restart hg-server (flush GraphManager dirty schema cache; entrypoint 顺带清孤儿目录)"
$COMPOSE restart hg-server >/dev/null

echo "→ wait hg-server healthy (up to 60s)"
CID="$($COMPOSE ps -q hg-server)"
for i in $(seq 1 30); do
  s="$(docker inspect "$CID" --format '{{.State.Health.Status}}' 2>/dev/null || echo "?")"
  if [ "$s" = "healthy" ]; then
    echo "✓ hg-server healthy after ${i}x2s — safe to rebuild KG (${GRAPH})"
    exit 0
  fi
  sleep 2
done
echo "✗ hg-server not healthy after 60s — check: docker logs ${PROJECT}-hg-server-1" >&2
exit 1
