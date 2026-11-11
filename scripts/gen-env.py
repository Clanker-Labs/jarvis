#!/usr/bin/env python3
"""Create .env from .env.example, filling generated secrets. Never overwrites existing values."""

from __future__ import annotations

import base64
import secrets
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parent.parent
example = (root / ".env.example").read_text()
target = root / ".env"
existing: dict[str, str] = {}
if target.exists():
    for line in target.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            existing[k.strip()] = v


def tailscale_ip() -> str:
    try:
        out = subprocess.run(
            ["tailscale", "ip", "-4"],  # noqa: S607 — resolved from PATH on purpose
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip().splitlines()[0] if out.returncode == 0 else ""
    except (OSError, IndexError, subprocess.TimeoutExpired):
        return ""


generated = {
    "JARVIS_NODE_TOKEN": secrets.token_urlsafe(32),
    "JARVIS_SECRET_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    "ADMIN_BOOTSTRAP_PASSWORD": secrets.token_urlsafe(12),
    "TAILSCALE_IP": tailscale_ip(),
}

lines = []
for line in example.splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        k, default = line.split("=", 1)
        k = k.strip()
        if existing.get(k):
            line = f"{k}={existing[k]}"
        elif not default and generated.get(k):
            line = f"{k}={generated[k]}"
    lines.append(line)
extra = [f"{k}={v}" for k, v in existing.items() if f"\n{k}=" not in "\n" + example]
target.write_text("\n".join(lines + extra) + "\n")
target.chmod(0o600)
print(f"wrote {target} (mode 600)")
if not existing.get("ADMIN_BOOTSTRAP_PASSWORD"):
    print(f"admin bootstrap password: {generated['ADMIN_BOOTSTRAP_PASSWORD']}")
