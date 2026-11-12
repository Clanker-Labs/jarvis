#!/bin/sh
# Tailnet sidecar: publishes JARVIS's localhost-only ports on this machine's Tailscale address.
#
# Uses the HOST's tailscaled (socket mounted from /var/run/tailscale), so the services answer on
# the machine's own tailnet IP and MagicDNS name, e.g. http://jarvis-iot:9000 — no extra node, no
# auth key. Each port is a *foreground* `tailscale serve --tcp` (raw TCP, so WebSockets and any Host
# header work): tailscaled drops the mapping as soon as the process goes away, so stopping the
# stack (or this container dying) un-publishes everything. Tailnet only — never the LAN or Funnel.
set -eu

SOCKET=/var/run/tailscale/tailscaled.sock
PORTS="${TAILNET_PORTS:-8080 8000 9000}"
ts() { "${TAILSCALE:-tailscale}" --socket="$SOCKET" "$@"; }

log() { echo "tailnet: $*"; }

until ts status >/dev/null 2>&1; do
  log "waiting for tailscaled (is Tailscale up on the host?)"
  sleep 5
done

# Foreground mappings only show up in the JSON status.
published() { ts serve status --json 2>/dev/null | grep -q "\"$1\": {"; }

trap 'trap - TERM INT; log stopping; kill 0' TERM INT

for port in $PORTS; do
  (
    while :; do
      log "publishing :$port -> 127.0.0.1:$port"
      ts serve --tcp="$port" "tcp://127.0.0.1:$port" >/dev/null 2>&1 || true
      log ":$port mapping ended; retrying in 5s"
      sleep 5
    done
  ) &
  # One at a time: concurrent serve-config writes conflict (etag mismatch).
  for _ in 1 2 3 4 5 6 7 8 9 10; do published "$port" && break; sleep 1; done
done

log "up on $(ts status --self --peers=false 2>/dev/null | awk 'NR==1{print $1, $2}'): ports $PORTS"
wait
