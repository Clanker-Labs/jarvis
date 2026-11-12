#!/usr/bin/env bash
# One-time Pi setup. Everything runs in docker compose; only the kiosk browser runs on the host.
# Idempotent: re-run after pulling to rebuild and restart.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

say "System setup (sudo): Docker + buildx, start on boot, kiosk auto-login"
sudo "$REPO/scripts/pi-system-setup.sh" "$USER"
if ! docker info >/dev/null 2>&1; then
  echo "Docker isn't usable from this shell yet: log out/in (or reboot), then re-run 'make install-pi'."
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

say "Done. On boot: Docker + jarvis.service start the stack, auto-login opens the kiosk."
TS_NAME="$(tailscale status --self --peers=false 2>/dev/null | awk 'NR==1{print $2}')"
[ -n "$TS_NAME" ] && say "From your tailnet: http://$TS_NAME:8080 (UI)  http://$TS_NAME:9000 (admin)"
