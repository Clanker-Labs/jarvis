#!/usr/bin/env bash
# Root half of the Pi setup (run via sudo; scripts/install-pi.sh calls it). Idempotent.
#   sudo scripts/pi-system-setup.sh [user]
# - Docker engine + compose + buildx, enabled at boot; <user> in the docker group
# - jarvis.service: `docker compose up -d` at boot (deploy/systemd)
# - GDM auto-login for <user>, so the kiosk opens after a reboot
# - gives <user>'s *current* login access to the Docker socket right away (an ACL that lasts
#   until Docker restarts; by then a fresh login carries the docker group)
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
REPO="$(cd "$(dirname "$0")/.." && pwd)"
U="${1:-${SUDO_USER:-}}"
[ -n "$U" ] && id "$U" >/dev/null || { echo "usage: sudo $0 <user>" >&2; exit 1; }
say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

need=()
command -v docker >/dev/null || need+=(docker.io)
docker compose version >/dev/null 2>&1 || need+=(docker-compose-v2)
docker buildx version >/dev/null 2>&1 || need+=(docker-buildx)
command -v setfacl >/dev/null || need+=(acl)
if [ ${#need[@]} -gt 0 ]; then
  say "Installing ${need[*]}"
  apt-get update -qq
  apt-get install -y -qq "${need[@]}"
fi
systemctl enable --now docker >/dev/null
systemctl enable tailscaled >/dev/null 2>&1 || true

if ! id -nG "$U" | tr ' ' '\n' | grep -qx docker; then
  say "Adding $U to the docker group"
  usermod -aG docker "$U"
fi
setfacl -m "u:$U:rw" /var/run/docker.sock

say "Installing jarvis.service (stack starts at boot)"
sed "s#@REPO@#$REPO#g" "$REPO/deploy/systemd/jarvis.service" > /etc/systemd/system/jarvis.service
systemctl daemon-reload
systemctl enable jarvis.service >/dev/null

GDM_CONF=/etc/gdm3/custom.conf
if [ -f "$GDM_CONF" ] && ! grep -q "^AutomaticLogin=$U\$" "$GDM_CONF"; then
  say "Enabling desktop auto-login for $U (kiosk opens at boot)"
  sed -i -e '/^\[daemon\]/,/^\[/{/^#\? *AutomaticLoginEnable *=.*/d;/^#\? *AutomaticLogin *=.*/d}' \
    -e "s/^\[daemon\]\$/[daemon]\nAutomaticLoginEnable=true\nAutomaticLogin=$U/" "$GDM_CONF"
fi
say "System setup done."
