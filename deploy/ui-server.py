#!/usr/bin/env python3
"""Serve the built kiosk UI on localhost only, plus a runtime /config.json.

The page needs the brain WebSocket URL and the device token. They come from the environment at
request time, so the static build stays generic and no secret is baked into it. This server binds
to 127.0.0.1: the token is only ever visible to the kiosk browser on this machine.
"""

from __future__ import annotations

import json
import os
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.split("?")[0] == "/config.json":
            body = json.dumps(
                {
                    "brainUrl": os.environ.get("BRAIN_WS_URL", "ws://127.0.0.1:8000/ws/ui"),
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
