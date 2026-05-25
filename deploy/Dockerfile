# Arrow Lake — Multi-stage Dockerfile (CPU)
#
# Stage 1 (builder): Install dependencies + build wheel
# Stage 2 (runtime): Minimal runtime image with .venv
#
# Build:  docker build -f deploy/Dockerfile -t arrow-lake:latest .
# Multi-arch: docker buildx build --platform linux/amd64,linux/arm64 -f deploy/Dockerfile -t arrow-lake:latest .
# Run:    docker run --rm arrow-lake:latest python -c "import arrow_lake"

# ==============================================================================
# Stage 1 — builder
# ==============================================================================
FROM --platform=$BUILDPLATFORM python:3.12-slim AS builder

# Clear any daemon-injected proxy — must unset before apt-get
ENV HTTP_PROXY=""
ENV HTTPS_PROXY=""
ENV http_proxy=""
ENV https_proxy=""
ENV no_proxy="*"
ENV NO_PROXY="*"

ARG TARGETARCH

# System dependencies for building Python packages (Daft, Lance, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    curl \
    git \
    libssl-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /build

# Copy dependency manifests and source (hatchling needs README.md at build time)
COPY pyproject.toml README.md ./
COPY arrow_lake/ arrow_lake/
COPY flows/ flows/

# Create virtual environment and install everything (with BuildKit cache)
# Use China PyPI mirror for faster downloads
ENV UV_HTTP_TIMEOUT=120
ENV UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ENV UV_TRUSTED_HOST=mirrors.aliyun.com
# Unset daemon-injected proxy so uv connects directly to the mirror
ENV HTTP_PROXY=""
ENV HTTPS_PROXY=""
ENV http_proxy=""
ENV https_proxy=""

RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv .venv \
    && . .venv/bin/activate \
    && uv pip install --no-cache-dir . \
    && uv pip install --no-cache-dir ".[fts]" \
    && uv pip install --no-cache-dir jupyterlab ipywidgets \
    && uv pip install --no-cache-dir modelscope>=1.18 \
    && uv pip install --no-cache-dir PyJWT \
    && uv pip install --no-cache-dir "apache-gravitino>=1.2.1" \
    && uv pip install --no-cache-dir ".[otel]"

# ==============================================================================
# Stage 2 — runtime
# ==============================================================================
FROM python:3.12-slim AS runtime

# Clear any daemon-injected proxy
ENV HTTP_PROXY=""
ENV HTTPS_PROXY=""
ENV http_proxy=""
ENV https_proxy=""
ENV no_proxy="*"
ENV NO_PROXY="*"

LABEL maintainer="wits@sunpw.dev"
LABEL description="Arrow Lake — Unified multimodal data lakehouse (Lance + Daft + Ray + GraphRAG)"
LABEL org.opencontainers.image.source="https://github.com/wits-sunpw/arrow-lake"
LABEL org.opencontainers.image.version="1.4.4"

# Runtime system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libcurl4 \
    libgomp1 \
    libssl3 \
    libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN groupadd -r -g 999 arrowlake && useradd -r -u 999 -g arrowlake -d /app -s /sbin/nologin arrowlake

# Install mc (MinIO Client) for health checks (before USER arrowlake)
RUN curl -sfL https://dl.min.io/client/mc/release/linux-amd64/mc -o /usr/local/bin/mc \
    && chmod +x /usr/local/bin/mc || echo "WARN: mc download failed, skipping"

WORKDIR /app

# Run as non-root user
USER arrowlake

# Copy virtual environment from builder
COPY --from=builder /build/.venv /app/.venv

# Fix shebangs: builder venv paths (/build/.venv) → runtime (/app/.venv)
# Must run as root since COPY sets ownership to root
USER root
RUN grep -rl '#!/build/.venv/bin/python3' /app/.venv/bin/ 2>/dev/null \
    | xargs -r sed -i '1s|^#!/build/.venv/bin/python3|#!/app/.venv/bin/python3|' || true
RUN mkdir -p /app/.duckdb /app/.local/share/jupyter/runtime \
    && chown -R arrowlake:arrowlake /app/.duckdb /app/.local
USER arrowlake

# Copy installed package source (for editable-like behavior)
COPY --from=builder /build/arrow_lake /app/arrow_lake
COPY --from=builder /build/flows /app/flows

# Copy examples for testing / demos
COPY examples/ /app/examples/

# Activate venv in PATH
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app" \
    PYTHONUNBUFFERED="1" \
    PYTHONDONTWRITEBYTECODE="1"

# Default: health/metrics server
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD ["curl", "-sf", "http://localhost:8000/health"]

CMD ["uvicorn", "arrow_lake.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
