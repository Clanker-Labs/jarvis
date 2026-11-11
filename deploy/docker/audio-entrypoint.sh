#!/bin/sh
# Route ALSA "default" through PipeWire/Pulse when its socket is reachable, else raw ALSA.
set -e
conf="${HOME:-/tmp}/.asoundrc"
[ -w "$(dirname "$conf")" ] || conf=/tmp/.asoundrc
sock="${PULSE_SERVER#unix:}"
if [ -n "$PULSE_SERVER" ] && [ -S "$sock" ]; then
  printf 'pcm.!default { type pulse }\nctl.!default { type pulse }\n' > "$conf"
  echo "audio: using PipeWire/Pulse at $sock"
else
  : > "$conf"
  echo "audio: pulse socket not available, using raw ALSA devices"
fi
export ALSA_CONFIG_PATH=/usr/share/alsa/alsa.conf HOME="$(dirname "$conf")"
exec "$@"
