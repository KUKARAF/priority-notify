# priority-notify

Self-hosted notification server. Receives notifications from scripts, monitoring, and CI via API tokens; delivers them to clients (Android, GNOME, web) via SSE. Users authenticate with OIDC (Authentik).

## Quick Start

```bash
cp .env.example .env       # fill in secrets — see setup_authentik.md
uv sync
make db-upgrade
make dev
```

Open `http://localhost:8000`, sign in, create a device token, then push a notification:

```bash
curl -X POST http://localhost:8000/api/notifications/ \
  -H "Authorization: Bearer <your-token>" \
  -H "Content-Type: application/json" \
  -d '{"title": "Hello", "priority": "high", "source": "test"}'
```

## Setup

- [Authentik OIDC setup](setup_authentik.md) — creating the provider and application in Authentik
- [Server spec](server.spec.md) — full architecture, API reference, and data models

## Makefile Targets

| Target | Description |
|--------|-------------|
| `make dev` | Start uvicorn with reload |
| `make test` | Run pytest |
| `make lint` | Ruff check + format check |
| `make db-upgrade` | Run Alembic migrations |
| `make db-revision msg="..."` | Generate a new migration |

## Docker

```bash
docker compose -f docker/docker-compose.yml up
```
