"""The /health endpoint.

Phase 0's first exit criterion is that `docker compose up` produces a *working*
stack. The Compose healthcheck and the CI smoke job both gate on this endpoint,
so its failure modes are worth testing directly rather than only end-to-end.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.api.main import app, health
from prayas.db.engine import create_app_engine
from tests.conftest import requires_db


def _body(response: Any) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(response.body)
    return parsed


async def test_reports_degraded_when_no_engine_is_configured() -> None:
    """Before lifespan runs there is no engine; say so rather than raising."""
    previous = getattr(app.state, "engine", None)
    app.state.engine = None
    try:
        response = await health()
    finally:
        app.state.engine = previous

    assert response.status_code == 503
    assert _body(response) == {"status": "degraded", "database": "unset"}


async def test_reports_degraded_when_the_database_is_unreachable() -> None:
    """An orchestrator should see a structured answer, not a stack trace."""
    previous = getattr(app.state, "engine", None)
    app.state.engine = create_app_engine(
        "postgresql+asyncpg://nobody:wrong@127.0.0.1:1/nonexistent"
    )
    try:
        response = await health()
    finally:
        await app.state.engine.dispose()
        app.state.engine = previous

    assert response.status_code == 503
    assert _body(response)["database"] == "unreachable"


@pytest.mark.db
@requires_db
async def test_reports_ok_against_a_live_database(app_engine: AsyncEngine) -> None:
    previous = getattr(app.state, "engine", None)
    app.state.engine = app_engine
    try:
        response = await health()
    finally:
        app.state.engine = previous

    assert response.status_code == 200
    assert _body(response) == {"status": "ok", "database": "ok"}
