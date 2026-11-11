"""The admin app: a separate FastAPI app on its own port (9000), never linked from the kiosk."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from .api import build_router
from .api_tools import build_tools_router
from .auth import AdminAuth

if TYPE_CHECKING:
    from ..runtime import Runtime

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
        "style-src 'self' 'unsafe-inline'; connect-src 'self' ws: wss:; frame-ancestors 'none'"
    ),
}


def create_admin_app(rt: Runtime) -> FastAPI:
    auth = AdminAuth(
        rt.db,
        rt.settings.secret_key.get_secret_value(),
        rt.settings.admin_bootstrap_password.get_secret_value(),
    )
    rt.admin_auth = auth
    app = FastAPI(
        title="JARVIS admin", version=__version__, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.include_router(build_router(rt, auth))
    app.include_router(build_tools_router(rt, auth))

    @app.middleware("http")
    async def headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    dist = Path(rt.settings.admin_dist)
    if (dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):  # type: ignore[no-untyped-def]  # sync: runs in the threadpool
            if path.startswith("api/"):
                return JSONResponse({"detail": "Not found"}, status_code=404)
            f = (dist / path).resolve()
            if path and f.is_file() and f.is_relative_to(dist.resolve()):
                return FileResponse(f)
            return FileResponse(dist / "index.html")
    else:

        @app.get("/", include_in_schema=False)
        async def no_ui() -> JSONResponse:
            return JSONResponse(
                {"detail": "admin UI not built — run `make admin`"}, status_code=503
            )

    return app
