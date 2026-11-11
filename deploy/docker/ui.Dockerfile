# syntax=docker/dockerfile:1.7
# Kiosk UI: build the static bundle, serve it on 127.0.0.1:8080 with a runtime /config.json.
FROM node:22-bookworm-slim AS build
WORKDIR /src
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY packages/protocol/package.json packages/protocol/
COPY apps/ui/package.json apps/ui/
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile --filter @jarvis/ui...
COPY packages/protocol packages/protocol
COPY apps/ui apps/ui
RUN NODE_OPTIONS=--max-old-space-size=768 pnpm --filter @jarvis/ui build

FROM python:3.12-alpine
COPY deploy/ui-server.py /srv/ui-server.py
COPY --from=build /src/apps/ui/dist /srv/dist
USER nobody
CMD ["python", "/srv/ui-server.py", "/srv/dist"]
