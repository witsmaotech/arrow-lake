# examples_busi3 共享 env — source 后所有脚本通用。
# LLM = 百炼 qwen-turbo (json_schema 约束解码, 快, 非思考)
# Embedding = 本地 ollama qwen3-embedding:4b (2560维, 离线)
# HugeGraph = 宿主 127.0.0.1:8089 (→容器 8080, auth admin/pa)
# he_ka_base_dir = 本目录 data/ka (KA dump 落盘)
#
# 用法: source docs/cookbook/examples_busi3/env.sh

# 绝对路径(本脚本所在目录)
export BUSI3_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- LLM (百炼 qwen-turbo) ---
export ARROW_LAKE__LLM__PROVIDER=openai
export ARROW_LAKE__LLM__API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1
export ARROW_LAKE__LLM__API_KEY=sk-ws-H.RXDEDIM.77Xp.MEUCIQDbvQy-acMTP5SDpTtEQe4TiaRNiD_ta8a08DAVMcDYbwIgK1Zy_zjW4CgIajwm-VGxWmh1pApzMefclpg3uTYEfvM
export ARROW_LAKE__LLM__MODEL=qwen-turbo
export ARROW_LAKE__LLM__MAX_TOKENS=8192
export ARROW_LAKE__LLM__TIMEOUT_SECONDS=120.0

# --- Embedding (本地 ollama qwen3-embedding:4b) ---
export ARROW_LAKE__EMBEDDING__BACKEND=openai
export ARROW_LAKE__EMBEDDING__API_BASE=http://127.0.0.1:11434/v1
export ARROW_LAKE__EMBEDDING__MODEL=qwen3-embedding:4b
export ARROW_LAKE__EMBEDDING__API_KEY=ollama
export ARROW_LAKE__EMBEDDING__BATCH_SIZE=10

# --- HugeGraph (宿主端口 8089 → 容器 8080) ---
export ARROW_LAKE__HUGEGRAPH__ENABLED=true
export ARROW_LAKE__HUGEGRAPH__HOST=127.0.0.1
export ARROW_LAKE__HUGEGRAPH__PORT=8089
export ARROW_LAKE__HUGEGRAPH__USERNAME=admin
export ARROW_LAKE__HUGEGRAPH__PASSWORD=pa
export ARROW_LAKE__HUGEGRAPH__GRAPH_NAME=hugegraph
export ARROW_LAKE__HUGEGRAPH__BUILD_CONCURRENCY=3

# --- hyper-extract (he) 抽取后端 ---
export ARROW_LAKE__HUGEGRAPH__EXTRACTOR_BACKEND=he
export ARROW_LAKE__HUGEGRAPH__HE_MODEL=qwen-turbo
export ARROW_LAKE__HUGEGRAPH__HE_LANGUAGE=zh
export ARROW_LAKE__HUGEGRAPH__HE_KA_BASE_DIR="${BUSI3_DIR}/data/ka"
export ARROW_LAKE__HUGEGRAPH__HE_KG_GRANULARITY=dataset
export ARROW_LAKE__HUGEGRAPH__HE_CHUNK_SIZE=1000
export ARROW_LAKE__HUGEGRAPH__HE_CHUNK_OVERLAP=100

# --- 代理: 本机无直连外网, 百炼(dashscope)须走代理 7887; 内部端点(ollama) bypass ---
#   注意: dashscope 不能放 NO_PROXY (放进去会直连→Network unreachable→0实体)
export NO_PROXY=127.0.0.1,localhost,host.docker.internal
export no_proxy="$NO_PROXY"

echo "[env.sh] BUSI3_DIR=$BUSI3_DIR | LLM=百炼qwen-turbo | embed=ollama qwen3-embedding:4b | HG=127.0.0.1:8089 | ka=${BUSI3_DIR}/data/ka"
