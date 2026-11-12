#!/usr/bin/env bash
# Launches the JARVIS UI full-screen. Started from the desktop session (XDG autostart on GNOME,
# ~/.config/labwc/autostart on Raspberry Pi OS). Waits for the local UI server, then execs the
# first browser found. Chromium is preferred (best WebGL perf); Firefox is a fallback.
set -euo pipefail

URL="${JARVIS_KIOSK_URL:-http://127.0.0.1:8080/}"

for _ in $(seq 1 300); do  # cold boot: containers can take a while
  curl -fsS -o /dev/null "$URL" && break
  sleep 1
done

# Keep the panel awake while Jarvis manages its own sleep (best effort; harmless if unavailable).
gsettings set org.gnome.desktop.session idle-delay 0 2>/dev/null || true
gsettings set org.gnome.desktop.screensaver lock-enabled false 2>/dev/null || true

PROFILE="${XDG_STATE_HOME:-$HOME/.local/state}/jarvis-kiosk"
mkdir -p "$PROFILE"

CHROMIUM_FLAGS=(
  --kiosk "$URL"
  --user-data-dir="$PROFILE"
  --noerrdialogs --disable-infobars --no-first-run --disable-session-crashed-bubble
  --disable-features=Translate,MediaRouter --disable-pinch --overscroll-history-navigation=0
  --check-for-update-interval=31536000 --password-store=basic
  --autoplay-policy=no-user-gesture-required
  --ignore-gpu-blocklist --enable-gpu-rasterization --enable-zero-copy
  --ozone-platform-hint=auto
)

for b in chromium chromium-browser google-chrome; do
  if command -v "$b" >/dev/null; then
    # Clear the "Chromium didn't shut down correctly" flag after power cuts.
    sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"Crashed"/"exit_type":"Normal"/' \
      "$PROFILE/Default/Preferences" 2>/dev/null || true
    exec "$b" "${CHROMIUM_FLAGS[@]}"
  fi
done

if command -v firefox >/dev/null; then
  exec firefox --kiosk --private-window "$URL"
fi

echo "No supported browser found (install chromium)" >&2
exit 1
