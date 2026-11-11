#!/usr/bin/env bash
# Pi-side install: audio node + kiosk UI (+ native brain if requested with BRAIN=1).
# Idempotent. Uses user-level systemd units, so it only needs sudo for apt packages.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

if command -v apt-get >/dev/null; then
  say "Installing system packages (sudo)"
  sudo apt-get update -qq
  sudo apt-get install -y -qq alsa-utils curl git make
  # Chromium package name differs between Raspberry Pi OS and Ubuntu.
  if ! command -v chromium >/dev/null && ! command -v chromium-browser >/dev/null; then
    sudo apt-get install -y -qq chromium || sudo apt-get install -y -qq chromium-browser || \
      say "Chromium not installable via apt; the kiosk will fall back to Firefox"
  fi
fi

export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null; then
  say "Installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
fi
if ! command -v node >/dev/null || ! node -e 'process.exit(+process.versions.node.split(".")[0] < 22)'; then
  say "Installing Node 22 (user-local)"
  V=$(curl -s https://nodejs.org/dist/index.json | python3 -c "import json,sys;print(next(r['version'] for r in json.load(sys.stdin) if r['lts'] and r['version'].startswith('v22')))")
  mkdir -p "$HOME/.local/opt"
  curl -sSL "https://nodejs.org/dist/$V/node-$V-linux-arm64.tar.xz" | tar xJ -C "$HOME/.local/opt"
  ln -sfn "$HOME/.local/opt/node-$V-linux-arm64" "$HOME/.local/opt/node"
  for b in node npm npx corepack; do ln -sf "$HOME/.local/opt/node/bin/$b" "$HOME/.local/bin/$b"; done
fi
command -v pnpm >/dev/null || npm i -g --prefix "$HOME/.local" pnpm@9

[ -f .env ] || python3 scripts/gen-env.py

say "Python deps (audio node)"
uv sync --frozen --no-dev --package jarvis-audio-node
[ "${BRAIN:-0}" = "1" ] && uv sync --frozen --no-dev --package jarvis-brain
say "Downloading wake word + VAD models"
uv run --frozen --no-dev --package jarvis-audio-node python -m jarvis_audio.models || true

say "Building the kiosk UI"
NODE_OPTIONS=--max-old-space-size=768 pnpm install --frozen-lockfile --filter @jarvis/ui...
NODE_OPTIONS=--max-old-space-size=768 pnpm --filter @jarvis/ui build

say "Installing user services"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
units=(jarvis-ui jarvis-audio-node)
[ "${BRAIN:-0}" = "1" ] && units+=(jarvis-brain)
for u in "${units[@]}"; do
  sed -e "s#@REPO@#$REPO#g" -e "s#@UV@#$(command -v uv)#g" "deploy/systemd/$u.service" > "$UNIT_DIR/$u.service"
done
systemctl --user daemon-reload
systemctl --user enable --now "${units[@]/%/.service}"
# Start user services at boot without an interactive login.
loginctl enable-linger "$USER" 2>/dev/null || sudo loginctl enable-linger "$USER" || true

say "Installing kiosk autostart"
mkdir -p "$HOME/.config/autostart"
sed "s#@REPO@#$REPO#g" deploy/kiosk/jarvis-kiosk.desktop > "$HOME/.config/autostart/jarvis-kiosk.desktop"
if [ -d "$HOME/.config/labwc" ] || [ -f /etc/xdg/labwc/autostart ]; then
  mkdir -p "$HOME/.config/labwc"
  grep -q jarvis-kiosk "$HOME/.config/labwc/autostart" 2>/dev/null || \
    echo "$REPO/deploy/kiosk/jarvis-kiosk.sh &" >> "$HOME/.config/labwc/autostart"
fi

say "Done. Services: ${units[*]}. Reboot to verify the cold-boot path; see docs/SETUP_PI.md."
