"""Secrets (API keys, OAuth tokens) encrypted at rest with Fernet.

The key is ``JARVIS_SECRET_KEY`` from the environment, never stored in the DB. Values are
write-only from the admin API: it can list names and set/delete values, but never read them back.
Lookups fall back to the process environment so keys can also live in ``.env``.
"""

from __future__ import annotations

import logging
import os
import time

from cryptography.fernet import Fernet, InvalidToken

from .db import Database

log = logging.getLogger(__name__)


class SecretStore:
    def __init__(self, db: Database, key: str) -> None:
        self.db = db
        if not key:
            # Dev convenience only: an ephemeral key means stored secrets don't survive restarts.
            log.warning("JARVIS_SECRET_KEY not set: using an ephemeral key (dev only)")
            key = Fernet.generate_key().decode()
        self._f = Fernet(key.encode())
        self._cache: dict[str, str] = {}

    def set(self, name: str, value: str) -> None:
        token = self._f.encrypt(value.encode())
        self.db.execute(
            "INSERT INTO secrets(name, value, updated) VALUES(?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated=excluded.updated",
            (name, token, time.time()),
        )
        self._cache[name] = value

    def delete(self, name: str) -> None:
        self.db.execute("DELETE FROM secrets WHERE name=?", (name,))
        self._cache.pop(name, None)

    def get(self, name: str, default: str = "") -> str:
        if name in self._cache:
            return self._cache[name]
        row = self.db.one("SELECT value FROM secrets WHERE name=?", (name,))
        if row:
            try:
                value = self._f.decrypt(row["value"]).decode()
            except InvalidToken:
                log.error("secret %s cannot be decrypted (JARVIS_SECRET_KEY changed?)", name)
                return os.environ.get(name, default)
            self._cache[name] = value
            return value
        return os.environ.get(name, default)

    def has(self, name: str) -> bool:
        return bool(self.get(name))

    def names(self) -> list[dict[str, object]]:
        """Names + where each is set. Never returns values."""
        stored = {
            r["name"]: r["updated"] for r in self.db.query("SELECT name, updated FROM secrets")
        }
        return [{"name": n, "source": "db", "updated": u} for n, u in sorted(stored.items())]
