# syntax=docker/dockerfile:1.7
# JARVIS brain: orchestrator + admin API (+ offline voice engines: Piper, faster-whisper).
FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    JARVIS_DATA_DIR=/data PATH=/app/.venv/bin:$PATH
WORKDIR /app
# curl: healthcheck. nodejs/npm/git: npx-based MCP servers and agent CLIs (phase 5+).
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/*
# Dependencies first (cached layer), then the source.
COPY pyproject.toml uv.lock .python-version ./
COPY packages/protocol/pyproject.toml packages/protocol/
COPY services/brain/pyproject.toml services/brain/
COPY services/audio-node/pyproject.toml services/audio-node/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package jarvis-brain --extra local-voice --no-install-workspace
COPY packages/protocol packages/protocol
COPY services/brain services/brain
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package jarvis-brain --extra local-voice
RUN useradd --create-home --uid 1000 jarvis && mkdir -p /data && chown jarvis /data
USER jarvis
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s \
  CMD python -c "import os,urllib.request;urllib.request.urlopen(f\"http://{os.environ.get('BRAIN_HOST','127.0.0.1')}:{os.environ.get('BRAIN_PORT','8000')}/health\",timeout=2)"
CMD ["jarvis-brain"]
