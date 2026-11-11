"""SQLite storage: config, secrets, traces, notes, memory, admin auth.

One file (``$JARVIS_DATA_DIR/jarvis.db``), WAL mode, forward-only numbered migrations. Calls are
short and local, so they run synchronously under a lock; nothing here should take milliseconds.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

MIGRATIONS: list[str] = [
    # 1 — config + secrets
    """
    CREATE TABLE config (section TEXT PRIMARY KEY, data TEXT NOT NULL, updated REAL NOT NULL);
    CREATE TABLE secrets (name TEXT PRIMARY KEY, value BLOB NOT NULL, updated REAL NOT NULL);
    """,
    # 2 — conversation traces
    """
    CREATE TABLE turns (
      id TEXT PRIMARY KEY, started REAL NOT NULL, ended REAL,
      user_text TEXT, reply_text TEXT, provider TEXT, model TEXT,
      input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0, cost_usd REAL DEFAULT 0,
      latency TEXT, events TEXT, error TEXT
    );
    CREATE INDEX turns_started ON turns(started DESC);
    """,
    # 3 — admin auth
    """
    CREATE TABLE admin (
      id INTEGER PRIMARY KEY CHECK (id = 1), password_hash TEXT NOT NULL,
      totp_secret BLOB, totp_enabled INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL
    );
    CREATE TABLE admin_sessions (
      token_hash TEXT PRIMARY KEY, created REAL NOT NULL, expires REAL NOT NULL,
      last_seen REAL NOT NULL, ip TEXT
    );
    """,
    # 4 — notes, timers/reminders, long-term memory
    """
    CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT NOT NULL, created REAL NOT NULL);
    CREATE VIRTUAL TABLE notes_fts USING fts5(text, content='notes', content_rowid='id');
    CREATE TRIGGER notes_ai AFTER INSERT ON notes BEGIN
      INSERT INTO notes_fts(rowid, text) VALUES (new.id, new.text); END;
    CREATE TRIGGER notes_ad AFTER DELETE ON notes BEGIN
      INSERT INTO notes_fts(notes_fts, rowid, text) VALUES('delete', old.id, old.text); END;
    CREATE TABLE timers (
      id INTEGER PRIMARY KEY, kind TEXT NOT NULL, label TEXT NOT NULL, due REAL NOT NULL,
      created REAL NOT NULL, fired REAL
    );
    CREATE INDEX timers_due ON timers(fired, due);
    CREATE TABLE memories (
      id INTEGER PRIMARY KEY, fact TEXT NOT NULL, source TEXT, created REAL NOT NULL,
      updated REAL NOT NULL, embedding BLOB
    );
    CREATE VIRTUAL TABLE memories_fts USING fts5(fact, content='memories', content_rowid='id');
    CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
      INSERT INTO memories_fts(rowid, fact) VALUES (new.id, new.fact); END;
    CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
      INSERT INTO memories_fts(memories_fts, rowid, fact) VALUES('delete', old.id, old.fact); END;
    CREATE TRIGGER memories_au AFTER UPDATE ON memories BEGIN
      INSERT INTO memories_fts(memories_fts, rowid, fact) VALUES('delete', old.id, old.fact);
      INSERT INTO memories_fts(rowid, fact) VALUES (new.id, new.fact); END;
    """,
]


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=3000")
        self.migrate()

    def migrate(self, extra: Sequence[str] = ()) -> None:
        with self._lock:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            for i, sql in enumerate([*MIGRATIONS, *extra], start=1):
                if i <= version:
                    continue
                # executescript() commits implicitly, so the transaction lives inside the script.
                try:
                    self._conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {i};\nCOMMIT;")
                except sqlite3.Error:
                    if self._conn.in_transaction:
                        self._conn.execute("ROLLBACK")
                    raise

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def execute(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), default=str)
