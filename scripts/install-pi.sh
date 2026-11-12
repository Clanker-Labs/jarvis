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

say "Starting JARVIS at boot (systemd unit, sudo)"
sed "s#@REPO@#$REPO#g" deploy/systemd/jarvis.service | sudo tee /etc/systemd/system/jarvis.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable docker jarvis.service >/dev/null
sudo systemctl enable tailscaled >/dev/null 2>&1 || true

# The kiosk is a desktop-session app, so the session must start without a login prompt.
GDM_CONF=/etc/gdm3/custom.conf
if [ -f "$GDM_CONF" ] && ! grep -q "^AutomaticLogin=$USER" "$GDM_CONF"; then
  say "Enabling desktop auto-login for $USER (GDM, sudo) so the kiosk opens at boot"
  sudo sed -i -e '/^\[daemon\]/,/^\[/{/^#\? *AutomaticLoginEnable *=.*/d;/^#\? *AutomaticLogin *=.*/d}' \
    -e "s/^\[daemon\]$/[daemon]\nAutomaticLoginEnable=true\nAutomaticLogin=$USER/" "$GDM_CONF"
fi

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
