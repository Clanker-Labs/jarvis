"""Admin API for connectors and the OAuth flow."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from ..connectors.oauth import OAuthError
from .auth import AdminAuth

if TYPE_CHECKING:
    from ..runtime import Runtime

log = logging.getLogger(__name__)
CALLBACK = "/api/connectors/oauth/callback"


class PastedUrl(BaseModel):
    url: str = Field(min_length=10, max_length=4000)


def build_connectors_router(rt: Runtime, auth: AdminAuth) -> tuple[APIRouter, APIRouter]:
    guarded = APIRouter(prefix="/api/connectors", dependencies=[Depends(auth.require)])
    public = APIRouter()

    def redirect_uri(request: Request) -> str:
        configured = rt.connectors.cfg.redirect_uri.strip()
        return configured or str(request.base_url).rstrip("/") + CALLBACK

    async def after_connect(cid: str) -> None:
        rt.connectors.sync()
        rt.spawn(rt.connectors.refresh_now())
        log.info("connector %s connected", cid)

    @guarded.get("")
    async def list_connectors(request: Request) -> dict[str, Any]:
        return {"connectors": rt.connectors.describe(), "redirect_uri": redirect_uri(request)}

    @guarded.post("/{cid}/connect")
    async def connect(cid: str, request: Request) -> dict[str, str]:
        lc = rt.connectors.connectors.get(cid)
        if lc is None or lc.manifest.auth != "oauth2" or not lc.manifest.oauth:
            raise HTTPException(404, "No OAuth connector with that id.")
        try:
            url = rt.oauth.begin(
                cid,
                lc.manifest.oauth.get("provider", "google"),
                list(lc.manifest.oauth.get("scopes", [])),
                redirect_uri(request),
            )
        except OAuthError as e:
            raise HTTPException(400, str(e)) from e
        return {"auth_url": url}

    @guarded.post("/oauth/complete")
    async def complete_pasted(body: PastedUrl) -> dict[str, str]:
        try:
            cid = await rt.oauth.complete_from_url(body.url)
        except OAuthError as e:
            raise HTTPException(400, str(e)) from e
        await after_connect(cid)
        return {"connected": cid}

    @guarded.post("/{cid}/disconnect")
    async def disconnect(cid: str) -> dict[str, bool]:
        await rt.oauth.revoke(cid)
        rt.connectors.sync()
        return {"ok": True}

    @guarded.post("/now/refresh")
    async def refresh_now() -> dict[str, Any]:
        return (await rt.connectors.refresh_now()).model_dump()

    # The provider redirects the browser here. No session cookie arrives (SameSite=Strict on a
    # cross-site redirect), so the single-use state + PKCE verifier are what authenticate it.
    @public.get(CALLBACK, include_in_schema=False)
    async def callback(state: str = "", code: str = "", error: str = "") -> RedirectResponse:
        if error:
            return RedirectResponse(f"/connectors?error={quote(error)}", status_code=303)
        try:
            cid = await rt.oauth.complete(state, code)
        except OAuthError as e:
            return RedirectResponse(f"/connectors?error={quote(str(e))}", status_code=303)
        await after_connect(cid)
        return RedirectResponse(f"/connectors?connected={quote(cid)}", status_code=303)

    return guarded, public
