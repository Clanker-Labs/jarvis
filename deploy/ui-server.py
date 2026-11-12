#!/usr/bin/env python3
"""Serve the built kiosk UI on localhost only, plus a runtime /config.json.

The page needs the brain WebSocket URL and the device token. They come from the environment at
request time, so the static build stays generic and no secret is baked into it. This server binds
to 127.0.0.1; the tailnet sidecar (compose service `tailnet`) forwards this machine's tailnet
address to it, so the token is visible to the kiosk browser and to your tailnet devices only.

With ``BRAIN_WS_URL=auto`` (the default) the brain URL follows the host the page was opened on:
the kiosk at 127.0.0.1:8080 talks to 127.0.0.1:8000, a laptop at jarvis-iot:8080 talks to
jarvis-iot:8000. Requests for any other Host header get a 421 (DNS-rebinding defence).
"""

from __future__ import annotations

import json
import os
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def allowed_hosts() -> set[str]:
    extra = os.environ.get("TAILNET_HOSTS", "")
    return {"127.0.0.1", "localhost"} | {
        h.strip().rstrip(".").lower() for h in extra.split(",") if h.strip()
    }


def split_host(host_header: str) -> str:
    """'jarvis-iot:8080' -> 'jarvis-iot'; '[fd7a::1]:8080' -> '[fd7a::1]'."""
    h = host_header.strip().lower()
    if h.startswith("["):
        return h.split("]", 1)[0] + "]"
    return h.rsplit(":", 1)[0] if ":" in h else h


def brain_url(hostname: str) -> str:
    configured = os.environ.get("BRAIN_WS_URL", "auto").strip()
    if configured and configured != "auto":
        return configured
    return f"ws://{hostname}:{os.environ.get('BRAIN_PORT', '8000')}/ws/ui"


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:
        hostname = split_host(self.headers.get("Host", ""))
        if hostname not in allowed_hosts():
            self.send_error(421, "Unknown host")
            return
        if self.path.split("?")[0] == "/config.json":
            body = json.dumps(
                {
                    "brainUrl": brain_url(hostname),
                    "token": os.environ.get("JARVIS_NODE_TOKEN", ""),
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        path = self.translate_path(self.path)
        if not os.path.exists(path) or (
            os.path.isdir(path) and not os.path.exists(os.path.join(path, "index.html"))
        ):
            self.path = "/index.html"  # SPA fallback
        super().do_GET()

    def do_HEAD(self) -> None:
        if split_host(self.headers.get("Host", "")) not in allowed_hosts():
            self.send_error(421, "Unknown host")
            return
        super().do_HEAD()

    def list_directory(self, path):  # type: ignore[no-untyped-def]
        self.send_error(404)

    def end_headers(self) -> None:
        if "/assets/" in self.path:
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        elif not self.path.endswith("config.json"):
            self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def log_message(self, *_args) -> None:  # quiet
        pass


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "apps/ui/dist").resolve()
    port = int(os.environ.get("UI_PORT", "8080"))
    if not (root / "index.html").exists():
        sys.exit(f"{root}/index.html not found — run `make ui` first")
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(root)))
    print(f"serving {root} on http://127.0.0.1:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
