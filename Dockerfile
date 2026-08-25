FROM python:3.12-slim-bookworm

# uv, pinned by digest-free tag on the official distroless image (ADR-001).
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependency layer first so source edits do not invalidate the install cache.
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-install-project --no-dev 2>/dev/null \
    || uv sync --no-install-project --no-dev

COPY prayas/ ./prayas/
COPY migrations/ ./migrations/
COPY alembic.ini ./

RUN uv sync --no-editable --no-dev 2>/dev/null || uv sync --no-dev

# Non-root at runtime. Defence in depth, and it mirrors the least-privilege
# posture ADR-004 applies at the database layer.
RUN useradd --create-home --uid 10001 prayas && chown -R prayas:prayas /app
USER prayas

EXPOSE 8000
CMD ["uvicorn", "prayas.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
