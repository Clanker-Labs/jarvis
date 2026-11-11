"""Entry point: ``jarvis-brain`` runs the brain API (and, from phase 4, the admin API)."""

from __future__ import annotations

import asyncio
import logging

import uvicorn

from .app import create_app
from .logging_setup import setup_logging
from .runtime import Runtime
from .settings import get_settings

log = logging.getLogger("jarvis_brain")


async def serve() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    settings.check_binds()
    if not settings.node_token.get_secret_value():
        log.warning("JARVIS_NODE_TOKEN is empty — WebSockets are unauthenticated (dev only)")
    runtime = Runtime(settings)
    brain = uvicorn.Server(
        uvicorn.Config(
            create_app(runtime),
            host=settings.brain_host,
            port=settings.brain_port,
            log_config=None,
            ws_max_size=4 * 1024 * 1024,
            proxy_headers=False,
        )
    )
    log.info("brain listening on %s:%s", settings.brain_host, settings.brain_port)
    await brain.serve()


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
