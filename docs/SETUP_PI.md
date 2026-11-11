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
$EDITOR .env            # LOCAL_LLM_BASE_URL / LOCAL_LLM_MODEL, API keys, BRAIN_URL if remote
make install-pi
```

`install-pi` installs Docker + the compose plugin and Chromium (apt, sudo), adds you to the
`docker` group (log out/in once, then re-run), builds and starts the stack with
`docker compose up -d --build`, and registers the kiosk launcher for GNOME and labwc. Containers
restart automatically at boot; the kiosk browser starts with the desktop session.

What runs where is set by `COMPOSE_PROFILES` in `.env`:

| Setup | Pi `.env` | Brain host `.env` |
|---|---|---|
| Everything on the Pi | `COMPOSE_PROFILES=brain,pi` | — |
| Brain on a tailnet box | `COMPOSE_PROFILES=pi`, `BRAIN_URL=ws://<box-ts-ip>:8000`, `BRAIN_WS_URL=ws://<box-ts-ip>:8000/ws/ui` | `COMPOSE_PROFILES=brain` (+`qdrant`), `BRAIN_HOST=<box-ts-ip>`, `ADMIN_HOST=<box-ts-ip>`, same `JARVIS_NODE_TOKEN` |

## 3. Audio devices

The audio-node container runs as your user and talks to PipeWire through its Pulse socket, so
`AUDIO_INPUT_DEVICE=default` / `AUDIO_OUTPUT_DEVICE=default` follow the desktop's selected devices
(pick the speakerphone in Settings → Sound). Without a desktop session it falls back to raw ALSA via
`/dev/snd`; then use names from `arecord -L`, e.g. `plughw:CARD=Speakerphone,DEV=0`.

## 4. Operate

```bash
make ps                 # status + memory/CPU per container
make logs               # follow all logs
docker compose logs -f audio-node
make up                 # after `git pull`: rebuild + restart what changed
make down
```

Open the kiosk on a desktop for debugging: `http://127.0.0.1:8080/?fps=1&cursor=1`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "connecting to brain…" on screen | `curl $BRAIN_URL/health` (http), check `BRAIN_WS_URL` and the token in `.env` |
| Core is choppy | `?fps=1` shows fps + quality; set Appearance → Quality to `low` in admin |
| No wake word | `docker compose logs audio-node` (look for "using PipeWire"); check the mic level in admin → Voice |
| Chromium "restore pages?" bubble | the launcher clears it on start; delete `~/.local/state/jarvis-kiosk` if stuck |
