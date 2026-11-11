# syntax=docker/dockerfile:1.7
# JARVIS audio node: mic, wake word, VAD, playback. ALSA tools + the PulseAudio ALSA plugin, so
# "default" routes through the host's PipeWire when its socket is mounted.
FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    JARVIS_MODELS_DIR=/opt/models PATH=/app/.venv/bin:$PATH
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends alsa-utils libasound2-plugins \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock .python-version ./
COPY packages/protocol/pyproject.toml packages/protocol/
COPY services/brain/pyproject.toml services/brain/
COPY services/audio-node/pyproject.toml services/audio-node/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package jarvis-audio-node --no-install-workspace
COPY packages/protocol packages/protocol
COPY services/audio-node services/audio-node
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package jarvis-audio-node
# Bake the models into the image: cold boot must work without internet.
RUN python -m jarvis_audio.models && chmod -R a+rX /opt/models
COPY deploy/docker/audio-entrypoint.sh /usr/local/bin/audio-entrypoint
ENTRYPOINT ["audio-entrypoint"]
CMD ["jarvis-audio-node"]
