# priority-notify

Self-hosted notification server. Receives notifications from scripts, monitoring, and CI via API tokens; delivers them to clients (Android, GNOME, web) via SSE. Users authenticate with OIDC (Authentik).

## Quick Start

```bash
cp .env.example .env       # fill in secrets — see setup_authentik.md
uv sync
make db-upgrade
make dev
```

Open `http://localhost:8000`, sign in, and create a device token.

## Sending Notifications

### From the web UI

Click **Add notification** on the dashboard to create a notification with a title, message, priority, and source.

### With the helper script

Add your token to `.env` as `TOKEN`, then:

```bash
./send-notification.sh "Deploy finished" "v2.4.1 is live" medium ci
# Usage: ./send-notification.sh <title> [message] [priority] [source]
```

Set `BASE_URL` in `.env` to override the default endpoint.

### With curl

```bash
curl -X POST http://localhost:8000/api/notifications/ \
  -H "Authorization: Bearer <your-token>" \
  -H "Content-Type: application/json" \
  -d '{"title": "Hello", "priority": "high", "source": "test"}'
```

The `priority` field accepts `low`, `medium`, `high`, or `critical`. The `message` and `source` fields are optional.

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
