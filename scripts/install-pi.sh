#!/usr/bin/env bash
# One-time Pi setup. Everything runs in docker compose; only the kiosk browser runs on the host.
# Idempotent: re-run after pulling to rebuild and restart.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  say "Installing Docker + compose plugin (sudo)"
  sudo apt-get update -qq
  sudo apt-get install -y -qq docker.io docker-compose-v2 || \
    sudo apt-get install -y -qq docker.io docker-compose-plugin
  sudo systemctl enable --now docker
fi
if ! docker info >/dev/null 2>&1; then
  say "Adding $USER to the docker group (sudo) — log out/in or reboot afterwards"
  sudo usermod -aG docker "$USER"
  echo "Re-run 'make install-pi' after logging back in."
  exit 1
fi

if ! command -v chromium >/dev/null && ! command -v chromium-browser >/dev/null; then
  say "Installing Chromium for the kiosk (sudo)"
  sudo apt-get install -y -qq chromium || sudo apt-get install -y -qq chromium-browser || \
    say "Chromium not installable via apt; the kiosk will fall back to Firefox"
fi

[ -f .env ] || python3 scripts/gen-env.py

say "Building and starting the stack (the first build takes a while on a Pi)"
docker compose up -d --build --remove-orphans
docker compose ps

say "Installing kiosk autostart"
mkdir -p "$HOME/.config/autostart"
sed "s#@REPO@#$REPO#g" deploy/kiosk/jarvis-kiosk.desktop > "$HOME/.config/autostart/jarvis-kiosk.desktop"
if [ -d "$HOME/.config/labwc" ] || [ -f /etc/xdg/labwc/autostart ]; then
  mkdir -p "$HOME/.config/labwc"
  grep -q jarvis-kiosk "$HOME/.config/labwc/autostart" 2>/dev/null || \
    echo "$REPO/deploy/kiosk/jarvis-kiosk.sh &" >> "$HOME/.config/labwc/autostart"
fi

say "Done. Containers restart automatically at boot; the kiosk opens with the desktop session."
