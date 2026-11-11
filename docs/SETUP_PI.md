# Setting up the Pi

Tested on a Raspberry Pi 4 (4 GB) with Ubuntu 26.04 + GNOME; written for Raspberry Pi OS Bookworm
(labwc) as well.

## 1. OS basics (once, needs sudo)

- **Auto-login** to the desktop for your user:
  - Raspberry Pi OS: `sudo raspi-config` → System → Boot / Auto login → Desktop autologin.
  - Ubuntu/GNOME: Settings → Users → Automatic login, or set `AutomaticLoginEnable=true` and
    `AutomaticLogin=<user>` under `[daemon]` in `/etc/gdm3/custom.conf`.
- **Landscape 1280×720** for the Touch Display 2 (native portrait 720×1280):
  - Pi OS: Screen Configuration → orientation → *right*; or add `video=DSI-1:720x1280@60,rotate=90`
    to `/boot/firmware/cmdline.txt`.
  - GNOME: Settings → Displays → Orientation → Landscape.
- **Tailscale** up: `sudo tailscale up` (already done on this Pi).
- Plug in the USB speakerphone; confirm with `arecord -l` and `aplay -l`.

## 2. Install

```bash
git clone git@github.com:Clanker-Labs/jarvis.git ~/jarvis && cd ~/jarvis
make env                # creates .env, prints the initial admin password
$EDITOR .env            # LOCAL_LLM_BASE_URL / LOCAL_LLM_MODEL, BRAIN_URL if the brain is elsewhere
make install-pi         # BRAIN=1 make install-pi to run the brain on the Pi too
```

`install-pi` installs `alsa-utils` + Chromium (apt), uv + Node (user-local), builds the UI, installs
user services `jarvis-ui`, `jarvis-audio-node` (and `jarvis-brain` with `BRAIN=1`), enables lingering
so they start at boot, and registers the kiosk launcher for both GNOME and labwc.

## 3. Audio devices

`AUDIO_INPUT_DEVICE` / `AUDIO_OUTPUT_DEVICE` in `.env` take ALSA names (`arecord -L`). `default`
follows PipeWire, which is right for most setups. To pin the speakerphone directly use e.g.
`plughw:CARD=Speakerphone,DEV=0` (the `plughw` plugin resamples for you).

## 4. Operate

```bash
systemctl --user status 'jarvis-*'
journalctl --user -fu jarvis-audio-node
make logs
```

Open the kiosk on a desktop for debugging: `http://127.0.0.1:8080/?fps=1&cursor=1`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "connecting to brain…" on screen | `curl $BRAIN_URL/health` (http), check `BRAIN_WS_URL` and the token in `.env` |
| Core is choppy | `?fps=1` shows fps + quality; set Appearance → Quality to `low` in admin |
| No wake word | `journalctl --user -u jarvis-audio-node`; check the mic level in admin → Voice |
| Chromium "restore pages?" bubble | the launcher clears it on start; delete `~/.local/state/jarvis-kiosk` if stuck |
