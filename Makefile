.PHONY: dev test lint typecheck db-upgrade db-revision

dev:
	uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy app/

db-upgrade:
	uv run alembic upgrade head

db-revision:
	uv run alembic revision --autogenerate -m "$(msg)"
