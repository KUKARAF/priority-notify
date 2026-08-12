FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

FROM python:3.13-slim

WORKDIR /app
COPY --from=builder /app /app

ENV PATH="/app/.venv/bin:$PATH"

RUN mkdir -p /app/data

EXPOSE 8000

# Apply DB migrations before serving. `alembic upgrade head` is idempotent (no-op when
# already at head), so this is safe on every deploy and keeps the schema in lockstep with
# the image. Fails fast if a migration errors rather than serving against a stale schema.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
