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

## MCP Server (Claude, ChatGPT, …)

priority-notify serves an MCP endpoint at `https://<host>/api/mcp`, so AI assistants can read, triage and send your notifications. Sign-in works like [Grist's](https://support.getgrist.com/mcp/). The server runs its own OAuth 2.1 authorization server (PKCE required). Assistants register themselves with a Client ID Metadata Document (CIMD), you sign in through Authentik, and you pick their permissions on a consent screen.

Enable it in `.env`:

```bash
PUBLIC_URL=https://notifications.osmosis.page   # must be the URL clients see
MCP_ENABLED=true                                 # /api/mcp, usable with API tokens
OAUTH_SERVER_ENABLED=true                        # interactive sign-in for assistants
OAUTH_CIMD_ALLOWED_HOSTS=claude.ai,chatgpt.com   # hosts whose CIMD clients may sign in
```

Then connect:

- **Claude.ai / Claude Desktop:** Settings → Connectors → Add custom connector → `https://<host>/api/mcp`
- **ChatGPT:** Settings → Apps → Create app (developer mode), Authentication: OAuth
- **Claude Code:** `claude mcp add --transport http priority-notify https://<host>/api/mcp`
  You can skip OAuth by passing an API token: `--header "Authorization: Bearer <token>"`

**Tools:** `whoami`, `list_notifications`, `get_notification`, `send_notification`, `update_notification_status`, `delete_notification`.

**Scopes:** `notifications:read` (includes marking read/archived), `notifications:write`, `notifications:delete`, `user.profile:read`, `offline_access`. When an OAuth client calls a tool outside its granted scopes, it gets a `403 insufficient_scope` challenge, which lets it ask you for more access. An API token's scope maps onto the same set: `full` grants everything.

Access tokens (`pn_at_…`) last 1 hour. Refresh tokens (`pn_rt_…`) last 60 days and rotate on every use. Connected apps are listed under **Devices → Connected apps**, where disconnecting one revokes all its tokens.

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
