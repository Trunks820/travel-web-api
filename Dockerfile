FROM ghcr.io/astral-sh/uv:0.8.4 AS uv

FROM python:3.12-slim AS builder
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim AS runtime
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN groupadd --system yuntu && useradd --system --gid yuntu --home-dir /app yuntu
WORKDIR /app
COPY --from=builder --chown=yuntu:yuntu /app/.venv /app/.venv
COPY --chown=yuntu:yuntu src /app/src
COPY --chown=yuntu:yuntu alembic /app/alembic
COPY --chown=yuntu:yuntu alembic.ini /app/alembic.ini
USER yuntu
EXPOSE 6670
CMD ["uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "6670"]
