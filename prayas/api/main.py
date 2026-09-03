"""Minimal application surface for Phase 0.

Exists so that "docker compose up produces a working stack" is a verifiable
claim rather than an assertion. Deliberately nothing else: the ingest, gate and
executor surfaces belong to their own phases (build discipline — do not scaffold ahead).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.api.webhooks import router as webhooks_router
from prayas.config import Settings
from prayas.console.api import router as console_router
from prayas.console.routes import pages as demo_pages
from prayas.console.routes import router as demo_router
from prayas.db.engine import create_app_engine
from prayas.observability.logging import configure

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings.from_env()
    configure(settings.log_level)

    engine = create_app_engine(settings.database_url_app)
    app.state.engine = engine
    log.info("prayas.startup", extra={"env": settings.env})

    try:
        yield
    finally:
        await engine.dispose()
        log.info("prayas.shutdown")


app = FastAPI(title="Prayas", version="0.0.0", lifespan=lifespan)
app.include_router(webhooks_router)
app.include_router(console_router)
app.include_router(demo_router)
app.include_router(demo_pages)

# Vendored, never a CDN (N3): the demo runs on localhost with the network off,
# so nothing on these screens may depend on a request leaving the machine.
app.mount(
    "/console/static",
    StaticFiles(directory=str(Path(__file__).resolve().parent.parent / "console" / "static")),
    name="console-static",
)


@app.get("/health")
async def health() -> JSONResponse:
    """Liveness plus database reachability.

    Reports degraded rather than raising, so an orchestrator sees a structured
    answer instead of a stack trace.
    """
    engine: AsyncEngine | None = getattr(app.state, "engine", None)
    body: dict[str, Any] = {"status": "ok", "database": "ok"}

    if engine is None:
        return JSONResponse(status_code=503, content={"status": "degraded", "database": "unset"})

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        log.exception("prayas.health.database_unreachable")
        return JSONResponse(
            status_code=503, content={"status": "degraded", "database": "unreachable"}
        )

    return JSONResponse(status_code=200, content=body)
