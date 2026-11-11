"""Admin authentication: one admin account, password (scrypt) + TOTP, cookie sessions.

- The first password comes from ADMIN_BOOTSTRAP_PASSWORD (hashed into the DB on first start).
- TOTP is set up on first login and then required. The secret is encrypted with the master key.
- Sessions: random 256-bit token in an HttpOnly, SameSite=Strict cookie; only its SHA-256 is stored.
  12 h sliding expiry.
- Login attempts are rate limited per client IP.
- Mutating requests must carry `X-Jarvis-Admin: 1` (CSRF defence on top of SameSite=Strict).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets as pysecrets
import time
from dataclasses import dataclass, field

import pyotp
from cryptography.fernet import Fernet
from fastapi import HTTPException, Request, WebSocket, status

from ..db import Database

log = logging.getLogger(__name__)

COOKIE = "jarvis_admin"
CSRF_HEADER = "x-jarvis-admin"
SESSION_TTL = 12 * 3600
_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = pysecrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return (
        f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt_b64, dk_b64 = stored.split("$")
        dk = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=32,
        )
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class RateLimiter:
    max_failures: int = 5
    window_s: float = 300
    lockout_s: float = 60
    failures: dict[str, list[float]] = field(default_factory=dict)

    def check(self, key: str) -> None:
        now = time.time()
        recent = [t for t in self.failures.get(key, []) if now - t < self.window_s]
        self.failures[key] = recent
        if len(recent) >= self.max_failures and now - recent[-1] < self.lockout_s:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts; wait a minute."
            )

    def fail(self, key: str) -> None:
        self.failures.setdefault(key, []).append(time.time())

    def ok(self, key: str) -> None:
        self.failures.pop(key, None)


class AdminAuth:
    def __init__(self, db: Database, secret_key: str, bootstrap_password: str) -> None:
        self.db = db
        self._f = Fernet(secret_key.encode()) if secret_key else Fernet(Fernet.generate_key())
        self.limiter = RateLimiter()
        if self.db.one("SELECT 1 FROM admin WHERE id=1") is None:
            if not bootstrap_password:
                log.warning(
                    "no admin account and ADMIN_BOOTSTRAP_PASSWORD is empty: admin login disabled"
                )
            else:
                self.db.execute(
                    "INSERT INTO admin(id, password_hash, totp_enabled, updated) VALUES(1,?,0,?)",
                    (hash_password(bootstrap_password), time.time()),
                )
                log.info("admin account created from ADMIN_BOOTSTRAP_PASSWORD")

    # ------------------------------------------------------------------ account

    def _row(self) -> dict | None:
        return self.db.one("SELECT * FROM admin WHERE id=1")

    @property
    def totp_enabled(self) -> bool:
        row = self._row()
        return bool(row and row["totp_enabled"])

    def _totp(self) -> pyotp.TOTP | None:
        row = self._row()
        if not row or not row["totp_secret"]:
            return None
        return pyotp.TOTP(self._f.decrypt(row["totp_secret"]).decode())

    def login(self, password: str, code: str | None, ip: str) -> str:
        self.limiter.check(ip)
        row = self._row()
        if not row or not verify_password(password, row["password_hash"]):
            self.limiter.fail(ip)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong password.")
        if row["totp_enabled"]:
            totp = self._totp()
            if not code:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "totp_required")
            if not totp or not totp.verify(code.strip(), valid_window=1):
                self.limiter.fail(ip)
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong code.")
        self.limiter.ok(ip)
        return self._new_session(ip)

    def change_password(self, current: str, new: str) -> None:
        row = self._row()
        if not row or not verify_password(current, row["password_hash"]):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is wrong.")
        if len(new) < 10:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Use at least 10 characters.")
        self.db.execute(
            "UPDATE admin SET password_hash=?, updated=? WHERE id=1",
            (hash_password(new), time.time()),
        )
        self.db.execute("DELETE FROM admin_sessions")  # log everyone out

    def totp_begin(self) -> dict[str, str]:
        if self.totp_enabled:
            raise HTTPException(status.HTTP_409_CONFLICT, "TOTP is already enabled.")
        secret = pyotp.random_base32()
        self.db.execute(
            "UPDATE admin SET totp_secret=?, updated=? WHERE id=1",
            (self._f.encrypt(secret.encode()), time.time()),
        )
        uri = pyotp.TOTP(secret).provisioning_uri(name="admin", issuer_name="JARVIS")
        return {"secret": secret, "otpauth_uri": uri}

    def totp_enable(self, code: str) -> None:
        totp = self._totp()
        if not totp or not totp.verify(code.strip(), valid_window=1):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "That code doesn't match.")
        self.db.execute("UPDATE admin SET totp_enabled=1, updated=? WHERE id=1", (time.time(),))

    # ------------------------------------------------------------------ sessions

    def _new_session(self, ip: str) -> str:
        token = pysecrets.token_urlsafe(32)
        now = time.time()
        self.db.execute(
            "INSERT INTO admin_sessions(token_hash, created, expires, last_seen, ip) VALUES(?,?,?,?,?)",
            (_token_hash(token), now, now + SESSION_TTL, now, ip),
        )
        self.db.execute("DELETE FROM admin_sessions WHERE expires < ?", (now,))
        return token

    def session_valid(self, token: str | None) -> bool:
        if not token:
            return False
        now = time.time()
        h = _token_hash(token)
        row = self.db.one("SELECT expires FROM admin_sessions WHERE token_hash=?", (h,))
        if not row or row["expires"] < now:
            return False
        self.db.execute(
            "UPDATE admin_sessions SET last_seen=?, expires=? WHERE token_hash=?",
            (now, now + SESSION_TTL, h),
        )
        return True

    def logout(self, token: str | None) -> None:
        if token:
            self.db.execute("DELETE FROM admin_sessions WHERE token_hash=?", (_token_hash(token),))

    # ------------------------------------------------------------------ FastAPI dependencies

    def require(self, request: Request) -> None:
        if not self.session_valid(request.cookies.get(COOKIE)):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in.")
        if self.totp_enabled is False and request.url.path not in TOTP_SETUP_PATHS:
            # Password-only sessions may only finish TOTP setup.
            raise HTTPException(status.HTTP_403_FORBIDDEN, "totp_setup_required")
        if (
            request.method not in ("GET", "HEAD", "OPTIONS")
            and request.headers.get(CSRF_HEADER) != "1"
        ):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing CSRF header.")

    def ws_ok(self, ws: WebSocket) -> bool:
        origin = ws.headers.get("origin", "")
        host = ws.headers.get("host", "")
        same_origin = origin.split("://", 1)[-1] == host
        return same_origin and self.totp_enabled and self.session_valid(ws.cookies.get(COOKIE))


TOTP_SETUP_PATHS = frozenset({"/api/auth/totp/setup", "/api/auth/totp/enable", "/api/auth/logout"})
