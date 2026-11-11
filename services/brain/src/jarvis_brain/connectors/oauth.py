"""OAuth 2.0 authorization-code flow with PKCE, token refresh, and encrypted token storage.

The admin cookie is SameSite=Strict, so it is *not* sent when Google redirects back to us. The
callback is therefore authenticated by the single-use ``state`` (created by a signed-in admin,
bound server-side to the PKCE verifier, 10-minute TTL) instead of the session.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets as pysecrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from ..secrets import SecretStore

log = logging.getLogger(__name__)

PROVIDERS: dict[str, dict[str, str]] = {
    "google": {
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "revoke_url": "https://oauth2.googleapis.com/revoke",
        "client_id_secret": "GOOGLE_CLIENT_ID",
        "client_secret_secret": "GOOGLE_CLIENT_SECRET",
    },
}
STATE_TTL = 600


class OAuthError(RuntimeError):
    pass


@dataclass
class PendingAuth:
    connector: str
    provider: str
    scopes: list[str]
    verifier: str
    redirect_uri: str
    created: float


class OAuthManager:
    def __init__(self, secrets: SecretStore, http: httpx.AsyncClient | None = None) -> None:
        self.secrets = secrets
        self._pending: dict[str, PendingAuth] = {}
        self._http = http

    def _client(self) -> httpx.AsyncClient:
        return self._http or httpx.AsyncClient(timeout=15)

    # ------------------------------------------------------------------ tokens

    @staticmethod
    def token_key(connector: str) -> str:
        return f"OAUTH_TOKEN_{connector.upper()}"

    def load(self, connector: str) -> dict[str, Any] | None:
        raw = self.secrets.get(self.token_key(connector))
        return json.loads(raw) if raw else None

    def save(self, connector: str, token: dict[str, Any]) -> None:
        self.secrets.set(self.token_key(connector), json.dumps(token))

    def forget(self, connector: str) -> None:
        self.secrets.delete(self.token_key(connector))

    def connected(self, connector: str) -> bool:
        tok = self.load(connector)
        return bool(tok and tok.get("refresh_token"))

    # ------------------------------------------------------------------ flow

    def begin(self, connector: str, provider: str, scopes: list[str], redirect_uri: str) -> str:
        p = PROVIDERS[provider]
        client_id = self.secrets.get(p["client_id_secret"])
        if not client_id or not self.secrets.get(p["client_secret_secret"]):
            raise OAuthError(
                f"set {p['client_id_secret']} and {p['client_secret_secret']} in Secrets first"
            )
        self._gc()
        state = pysecrets.token_urlsafe(32)
        verifier = pysecrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        self._pending[state] = PendingAuth(
            connector, provider, scopes, verifier, redirect_uri, time.time()
        )
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
        }
        return f"{p['auth_url']}?{urlencode(params)}"

    async def complete(self, state: str, code: str) -> str:
        """Exchange the code. Returns the connector id. The state is single-use."""
        self._gc()
        pend = self._pending.pop(state, None)
        if pend is None:
            raise OAuthError("this sign-in link expired or was already used — start again")
        p = PROVIDERS[pend.provider]
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": pend.redirect_uri,
            "client_id": self.secrets.get(p["client_id_secret"]),
            "client_secret": self.secrets.get(p["client_secret_secret"]),
            "code_verifier": pend.verifier,
        }
        client = self._client()
        try:
            r = await client.post(p["token_url"], data=data)
        finally:
            if client is not self._http:
                await client.aclose()
        if r.status_code >= 400:
            raise OAuthError(f"token exchange failed: {r.text[:200]}")
        tok = r.json()
        tok["expires_at"] = time.time() + float(tok.get("expires_in", 3600)) - 60
        tok["provider"] = pend.provider
        tok["scopes"] = pend.scopes
        self.save(pend.connector, tok)
        log.info("OAuth connected: %s", pend.connector)
        return pend.connector

    async def complete_from_url(self, url: str) -> str:
        """Fallback when the redirect couldn't reach us: the user pastes the URL they landed on."""
        q = parse_qs(urlparse(url.strip()).query)
        if "error" in q:
            raise OAuthError(f"provider returned an error: {q['error'][0]}")
        if "code" not in q or "state" not in q:
            raise OAuthError("that URL has no code/state in it")
        return await self.complete(q["state"][0], q["code"][0])

    async def access_token(self, connector: str) -> str:
        tok = self.load(connector)
        if not tok:
            raise OAuthError(f"{connector} is not connected (admin → Tools & Connectors)")
        if tok.get("expires_at", 0) > time.time():
            return str(tok["access_token"])
        if not tok.get("refresh_token"):
            raise OAuthError(f"{connector} needs to be reconnected")
        p = PROVIDERS[tok.get("provider", "google")]
        client = self._client()
        try:
            r = await client.post(
                p["token_url"],
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": tok["refresh_token"],
                    "client_id": self.secrets.get(p["client_id_secret"]),
                    "client_secret": self.secrets.get(p["client_secret_secret"]),
                },
            )
        finally:
            if client is not self._http:
                await client.aclose()
        if r.status_code >= 400:
            if r.status_code in (400, 401) and "invalid_grant" in r.text:
                raise OAuthError(f"{connector} access was revoked — reconnect it in admin")
            raise OAuthError(f"token refresh failed ({r.status_code})")
        new = r.json()
        tok.update(
            access_token=new["access_token"],
            expires_at=time.time() + float(new.get("expires_in", 3600)) - 60,
        )
        if new.get("refresh_token"):
            tok["refresh_token"] = new["refresh_token"]
        self.save(connector, tok)
        return str(tok["access_token"])

    async def revoke(self, connector: str) -> None:
        tok = self.load(connector)
        self.forget(connector)
        if tok and (url := PROVIDERS.get(tok.get("provider", ""), {}).get("revoke_url")):
            client = self._client()
            try:
                await client.post(
                    url, data={"token": tok.get("refresh_token") or tok.get("access_token")}
                )
            except httpx.HTTPError:
                pass
            finally:
                if client is not self._http:
                    await client.aclose()

    def _gc(self) -> None:
        now = time.time()
        for k in [k for k, v in self._pending.items() if now - v.created > STATE_TTL]:
            del self._pending[k]
