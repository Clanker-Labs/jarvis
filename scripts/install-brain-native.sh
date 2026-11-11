#!/usr/bin/env bash
# Run the brain as a native user service (no Docker): used by `make up` on hosts without Docker.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
export PATH="$HOME/.local/bin:$PATH"
uv sync --frozen --no-dev --package jarvis-brain
NODE_OPTIONS=--max-old-space-size=768 pnpm install --frozen-lockfile
[ -d apps/admin ] && [ -f apps/admin/package.json ] && pnpm --filter @jarvis/admin build || true
mkdir -p "$HOME/.config/systemd/user"
sed -e "s#@REPO@#$REPO#g" -e "s#@UV@#$(command -v uv)#g" deploy/systemd/jarvis-brain.service \
  > "$HOME/.config/systemd/user/jarvis-brain.service"
systemctl --user daemon-reload
systemctl --user enable --now jarvis-brain.service
systemctl --user restart jarvis-brain.service
echo "brain running: curl http://127.0.0.1:8000/health"
