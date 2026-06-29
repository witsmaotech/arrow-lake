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

# NOTE: Do NOT clear proxy ENV here — Docker daemon injects HTTP_PROXY/HTTPS_PROXY
# via ~/.docker/config.json for apt-get and pip to reach external hosts.
# We clear them AFTER network-dependent steps complete.

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
COPY deploy/uv-local /bin/uv
COPY deploy/uvx-local /bin/uvx
RUN chmod +x /bin/uv /bin/uvx

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

# WSL2 mirror-mode build proxy (proxy-forwarder host:17887 → Windows 7887, see
# memory wsl2-proxy). BuildKit's daemon-proxy auto-injection is NOT reliably
# applied to RUN steps here, so set it explicitly — uv/pip need it to reach the
# aliyun PyPI mirror (without it: "Connection refused" on pip downloads).
ARG BUILD_PROXY="http://127.0.0.1:17887"
ENV HTTP_PROXY=${BUILD_PROXY}
ENV HTTPS_PROXY=${BUILD_PROXY}
ENV http_proxy=${BUILD_PROXY}
ENV https_proxy=${BUILD_PROXY}

RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv .venv \
    && . .venv/bin/activate \
    && uv pip install --no-cache-dir ".[fts,otel,rag,he]" \
    && uv pip install --no-cache-dir jupyterlab ipywidgets \
    && uv pip install --no-cache-dir modelscope>=1.18 PyJWT "apache-gravitino==1.2.1"

# Clear proxy ENV after all network-dependent steps
ENV HTTP_PROXY=""
ENV HTTPS_PROXY=""
ENV http_proxy=""
ENV https_proxy=""
ENV no_proxy="*"
ENV NO_PROXY="*"

# ==============================================================================
# Stage 2 — runtime
# ==============================================================================
FROM python:3.12-slim AS runtime

ARG VERSION=dev
ARG TARGETARCH

# Runtime containers get proxy via docker-compose environment variables,
# NOTE: the daemon-injected proxy is KEPT here so the runtime apt-get + mc
# download below can reach external hosts. It is cleared AFTER those network
# steps (see "Clear proxy ENV" further down) so the final image has clean defaults.

LABEL maintainer="wits@sunpw.dev"
LABEL description="Arrow Lake — Unified multimodal data lakehouse (Lance + Daft + Ray + GraphRAG)"
LABEL org.opencontainers.image.source="https://github.com/wits-sunpw/arrow-lake"
LABEL org.opencontainers.image.version="${VERSION}"

# WSL2 mirror-mode build proxy (proxy-forwarder on host:17887 → Windows 7887,
# see memory wsl2-proxy). network:host build makes 127.0.0.1:17887 reachable.
# Set EXPLICITLY because BuildKit's auto-proxy is not reliably injected into
# RUN steps in this env. Override via --build-arg BUILD_PROXY=... for other envs.
ARG BUILD_PROXY="http://127.0.0.1:17887"
ENV HTTP_PROXY=${BUILD_PROXY}
ENV HTTPS_PROXY=${BUILD_PROXY}
ENV http_proxy=${BUILD_PROXY}
ENV https_proxy=${BUILD_PROXY}

# Runtime system dependencies. Switch apt to the Aliyun mirror (the WSL2 proxy
# allows aliyun — same host as the PyPI mirror used in the builder stage) so
# apt-get update succeeds through the proxy instead of hitting the intercepted
# deb.debian.org (which returns an HTML auth page → "NOSPLIT" apt error).
# Handle BOTH apt source formats (DEB822 .sources and legacy sources.list).
RUN for f in /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list; do \
        [ -f "$f" ] && sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' "$f" || true; \
    done \
    && apt-get update && apt-get install -y --no-install-recommends \
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

# Clear proxy ENV AFTER all network steps (apt + mc) so the final runtime image
# has clean proxy defaults (the running service must not inherit the build proxy).
ENV HTTP_PROXY=""
ENV HTTPS_PROXY=""
ENV http_proxy=""
ENV https_proxy=""
ENV no_proxy="*"
ENV NO_PROXY="*"

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
