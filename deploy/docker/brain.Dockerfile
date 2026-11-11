# syntax=docker/dockerfile:1.7
# JARVIS brain: orchestrator + admin API + admin UI static build.
FROM node:22-bookworm-slim AS admin-ui
WORKDIR /src
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY packages/protocol/package.json packages/protocol/
COPY apps/admin/package.json apps/admin/
COPY apps/ui/package.json apps/ui/
RUN pnpm install --frozen-lockfile --filter @jarvis/admin... --filter @jarvis/ui...
COPY packages/protocol packages/protocol
COPY apps apps
RUN pnpm --filter @jarvis/admin build && pnpm --filter @jarvis/ui build

FROM python:3.12-slim-bookworm AS app
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    JARVIS_IN_CONTAINER=1 JARVIS_DATA_DIR=/data BRAIN_HOST=0.0.0.0 ADMIN_HOST=0.0.0.0 \
    JARVIS_ADMIN_DIST=/app/apps/admin/dist JARVIS_UI_DIST=/app/apps/ui/dist
WORKDIR /app
# Node is needed at runtime only for npx-based MCP servers and agent CLIs.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl git nodejs npm \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock .python-version ./
COPY packages/protocol/pyproject.toml packages/protocol/
COPY services/brain/pyproject.toml services/brain/
COPY services/audio-node/pyproject.toml services/audio-node/
RUN --mount=type=cache,target=/root/.cache/uv \
    mkdir -p packages/protocol/src/jarvis_protocol services/brain/src/jarvis_brain services/audio-node/src/jarvis_audio \
    && touch packages/protocol/src/jarvis_protocol/__init__.py services/brain/src/jarvis_brain/__init__.py services/audio-node/src/jarvis_audio/__init__.py \
    && uv sync --frozen --no-dev --package jarvis-brain --no-install-workspace
COPY packages/protocol packages/protocol
COPY services/brain services/brain
COPY skills skills
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --package jarvis-brain
COPY --from=admin-ui /src/apps/admin/dist apps/admin/dist
COPY --from=admin-ui /src/apps/ui/dist apps/ui/dist
RUN useradd --create-home --uid 1000 jarvis && mkdir -p /data && chown jarvis /data
USER jarvis
EXPOSE 8000 9000
HEALTHCHECK --interval=30s --timeout=3s CMD curl -fsS http://127.0.0.1:8000/health || exit 1
CMD ["/app/.venv/bin/jarvis-brain"]
