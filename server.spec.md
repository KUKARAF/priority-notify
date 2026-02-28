# priority-notify — Server & Web Frontend

## Overview
A self-hosted Python FastAPI server that receives notifications from external sources (scripts, monitoring, CI), stores them in SQLite, and delivers them to clients via SSE or polling. Includes a server-rendered web frontend (Jinja2 + static files) for viewing notifications and managing client tokens.

## Goals & Non-Goals

### Goals
- Accept notifications from arbitrary sources via authenticated API
- Store notification history and read/unread status per user
- Deliver notifications to clients in near-real-time via SSE
- Serve a simple web UI for notification viewing and token management
- Authenticate users via OIDC with Authentik (auth.osmosis.page)
- Authenticate client devices via API tokens

### Non-Goals
- Enterprise multi-tenancy (personal/self-hosted)
- SPA frontend or separate frontend build process
- Built-in push notification delivery (FCM, APNs, email, SMS)
- Complex user management (delegated to Authentik)
- Horizontal scaling or clustering

## Architecture

```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  Monitoring  │  │  CI/CD       │  │  Scripts /   │
│  (Uptime,    │  │  (GitHub     │  │  Cron Jobs   │
│   Alertmgr)  │  │   Actions)   │  │              │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                 │                 │
       └─────────────────┼─────────────────┘
                         │  POST /api/notifications/
                         │  (API token auth)
                         ▼
┌─────────────────────────────────────────────────────┐
│                                                     │
│   FastAPI Backend (Python 3.13)                     │
│                                                     │
│  • OIDC Auth (Authentik)     • SSE Stream           │
│  • Notification CRUD         • Static Web UI        │
│  • Client Token Management   • Jinja2 Templates     │
│                                                     │
└───────────────────────┬─────────────────────────────┘
                        │
              ┌─────────▼─────────┐
              │   SQLite Database │
              │                   │
              │  • Users          │
              │  • Notifications  │
              │  • Client Tokens  │
              └───────────────────┘

       Clients (consume notifications):

┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│   Android APK   │  │ GNOME Extension │  │  Web Frontend   │
│ (separate repo: │  │ (separate repo: │  │ (served by this │
│ priority-notify- │  │ priority-notify- │  │  server)        │
│    android)     │  │    gnome)       │  │                 │
└─────────────────┘  └─────────────────┘  └─────────────────┘
```

### Data Flow

**Notification creation (inbound):**
1. External source sends `POST /api/notifications/` with an API token
2. Backend validates token, associates notification with token's user, stores in SQLite
3. Connected SSE clients for that user receive the new notification immediately

**Client consumption (outbound):**
1. User authenticates via OIDC on the web frontend
2. User creates a client token via the web UI (displayed once, transferable via QR code or copy-paste)
3. Client devices use that token for all API requests
4. Clients subscribe to SSE stream or poll with `?since=<timestamp>`
5. Clients mark notifications as read; status syncs to other clients on next fetch

## Stack

### Core
- **Python**: 3.13
- **Framework**: FastAPI
- **Key Libraries**:
  - `uvicorn` — ASGI server
  - `httpx` — Async HTTP client for OIDC token exchange
  - `joserfc` — JWT validation (OIDC ID tokens)
  - `authlib` — OAuth2/OIDC client
  - `pydantic` + `pydantic-settings` — Validation and config
  - `sse-starlette` — Server-Sent Events
  - `jinja2` — Server-side HTML templates
  - `qrcode[pil]` — QR code generation for token sharing
  - `itsdangerous` — Signed session cookies

### Data
- **Database**: SQLite via `aiosqlite`
- **ORM**: SQLAlchemy 2.x (async)
- **Migrations**: Alembic

### Dev Tooling
- **Package Manager**: `uv` (all deps in `pyproject.toml`)
- **Linting/Formatting**: `ruff`
- **Type Checking**: `mypy` (strict)
- **Testing**: `pytest` + `pytest-asyncio`
- **Pre-commit**: ruff, mypy
- **Task Runner**: `Makefile`

### Infrastructure
- **Container**: Docker + docker-compose
- **Deployment**: Self-hosted VPS
- **CI**: GitHub Actions
- **Secrets**: Environment variables via `pydantic-settings`
- **Logging**: `structlog` (JSON)

## Project Structure

```
./
├── .github/
│   └── workflows/
│       └── server.yml         # CI: lint, test, build Docker image
├── alembic/
│   ├── versions/
│   └── env.py
├── app/
│   ├── __init__.py
│   ├── main.py               # FastAPI app factory, lifespan, middleware
│   ├── config.py             # Pydantic settings (env vars)
│   ├── database.py           # SQLAlchemy engine, async session factory
│   ├── auth.py               # OIDC client, token validation, session helpers
│   ├── models.py             # SQLAlchemy models (User, Notification, ClientToken)
│   ├── schemas.py            # Pydantic request/response schemas
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── auth.py           # /auth/login, /auth/callback, /auth/logout
│   │   ├── notifications.py  # /api/notifications/ CRUD + SSE stream
│   │   ├── tokens.py         # /api/tokens/ management + QR
│   │   └── frontend.py       # / and /tokens — Jinja2 HTML pages
│   ├── static/
│   │   ├── style.css
│   │   └── app.js            # Minimal JS for SSE, mark-read, QR display
│   └── templates/
│       ├── base.html          # Layout with nav
│       ├── dashboard.html     # Notification list
│       └── tokens.html        # Token management
├── tests/
│   ├── conftest.py           # Fixtures: test DB, mock OIDC, test client
│   ├── test_notifications.py
│   ├── test_tokens.py
│   └── test_auth.py
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
├── .env.example
├── .gitignore
├── .pre-commit-config.yaml
├── alembic.ini
├── Makefile
├── pyproject.toml
└── server.spec.md
```

## Environment & Config

### Required Environment Variables
```bash
# Database
DATABASE_URL=sqlite+aiosqlite:///./data/priority_notify.db

# OIDC / Authentik (confidential client)
AUTHENTIK_ISSUER_URL=https://auth.osmosis.page
AUTHENTIK_CLIENT_ID=your-client-id
AUTHENTIK_CLIENT_SECRET=your-client-secret
AUTHENTIK_REDIRECT_URI=http://localhost:8000/auth/callback

# Application
SECRET_KEY=your-random-secret    # Signs session cookies
ALLOWED_HOSTS=localhost,127.0.0.1
```

### Optional Environment Variables
```bash
LOG_LEVEL=INFO
CORS_ORIGINS=http://localhost:3000
```

## API Design

All API routes are under `/api/`. No versioning — personal tool deployed atomically.

### Auth (`/auth/`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/auth/login` | None | Redirect to Authentik OIDC login |
| GET | `/auth/callback` | None | OIDC callback, sets session cookie |
| GET | `/auth/logout` | Session | Clear session, redirect to Authentik logout |
| GET | `/api/me` | Session or Token | Current user info |

### Notifications (`/api/notifications/`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/notifications/` | Session or Token | List notifications |
| POST | `/api/notifications/` | Token | Create notification (external sources) |
| GET | `/api/notifications/stream` | Session or Token | SSE stream of new notifications |
| GET | `/api/notifications/{id}` | Session or Token | Get single notification |
| PATCH | `/api/notifications/{id}` | Session or Token | Update status (read/unread/archived) |
| DELETE | `/api/notifications/{id}` | Session or Token | Delete notification |

**Query parameters for GET list:**
- `since` (ISO timestamp) — only notifications created after this time
- `status` (unread|read|archived) — filter by status
- `priority` (low|medium|high|critical) — filter by priority
- `source` (string) — filter by source
- `limit` (int, default 50, max 200) — page size
- `offset` (int, default 0) — pagination offset

**Create notification request:**
```bash
curl -X POST https://notify.example.com/api/notifications/ \
  -H "Authorization: Bearer <api-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Server disk full",
    "message": "Disk usage on web-01 is at 95%",
    "priority": "critical",
    "source": "monitoring"
  }'
```

**SSE stream events:**
```
event: notification
data: {"id": "...", "title": "...", "priority": "high", "source": "ci", ...}

event: status_change
data: {"id": "...", "status": "read"}
```

### Tokens (`/api/tokens/`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/tokens/` | Session | List tokens for current user |
| POST | `/api/tokens/` | Session | Create token (returns plaintext once) |
| DELETE | `/api/tokens/{token_id}` | Session | Revoke token |
| GET | `/api/tokens/{token_id}/qr` | Session | QR code image (PNG) containing the token |

### Web Frontend (`/`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | Session | Dashboard — recent notifications, live SSE updates |
| GET | `/tokens` | Session | Token management — create, revoke, show QR |

### Response Format

Single resources return plain JSON objects:
```json
{"id": "...", "title": "...", "priority": "high", "source": "monitoring", ...}
```

Lists include pagination:
```json
{
  "items": [...],
  "total": 42,
  "limit": 50,
  "offset": 0
}
```

Errors use HTTP status codes:
```json
{"detail": "Notification not found"}
```

## Data Models

### User
| Field | Type | Notes |
|-------|------|-------|
| id | UUID | Primary key |
| sub | string | Unique, OIDC subject identifier |
| email | string | |
| name | string | |
| created_at | datetime | |
| last_login_at | datetime | |

### Notification
| Field | Type | Notes |
|-------|------|-------|
| id | UUID | Primary key |
| user_id | UUID | FK → User |
| title | string | Required |
| message | text | Optional |
| priority | enum | low, medium, high, critical |
| status | enum | unread, read, archived |
| source | string | Optional — e.g. "monitoring", "ci", "backup-script" |
| created_at | datetime | |
| read_at | datetime | Nullable |
| metadata | JSON | Optional, arbitrary extra data from source |

### ClientToken
| Field | Type | Notes |
|-------|------|-------|
| id | UUID | Primary key |
| user_id | UUID | FK → User |
| token_hash | string | Unique, bcrypt hash (plaintext shown once at creation) |
| name | string | User-friendly label, e.g. "Pixel 8", "Work Laptop" |
| device_type | enum | android, gnome, other |
| last_used_at | datetime | Nullable, updated on each API request |
| created_at | datetime | |
| expires_at | datetime | Nullable |

### Database Indexes
- `notification(user_id, created_at)` — listing
- `notification(user_id, status)` — filtering unread
- `client_token(token_hash)` — unique, auth lookups

### Relationships
- One User → Many Notifications
- One User → Many ClientTokens

## Authentication

### Web Frontend (OIDC)
- Confidential client: standard OAuth2 authorization code flow with `client_secret`
- On successful callback, server creates/updates User record and sets a signed session cookie
- Session cookie: `HttpOnly`, `Secure` (in prod), `SameSite=Lax`, signed with `SECRET_KEY` via `itsdangerous`

### Client Devices (API Tokens)
- User creates tokens via web UI
- Plaintext token shown once, stored as bcrypt hash in DB
- Clients send `Authorization: Bearer <token>` on every request
- Server hashes the received token and looks up the matching `ClientToken` record
- Token owner's `user_id` determines which notifications are accessible

## SSE Delivery

Server-Sent Events are the primary real-time delivery mechanism.

**Server-side implementation:**
- `GET /api/notifications/stream` holds the connection open
- On new notification creation (`POST`), the route handler publishes to an in-process `asyncio.Queue` per connected user
- SSE endpoint reads from the queue and writes events to the response stream
- On disconnect, the queue is cleaned up

**Event types:**
- `notification` — new notification created
- `status_change` — notification status updated (read/archived)

**Reconnection:** SSE clients auto-reconnect. The server sends `id:` fields (notification UUID) so clients can use `Last-Event-ID` to catch up on missed events.

## Web Frontend Details

Server-rendered HTML via Jinja2. No build step, no JS framework.

**Dashboard (`/`):**
- Lists recent notifications, newest first
- Color-coded by priority
- Click to expand message body
- "Mark as read" button per notification
- Live updates via SSE (small JS snippet using `EventSource`)

**Token Management (`/tokens`):**
- Lists existing tokens with name, device type, last used, created date
- "Create Token" form (name + device type) — on submit, shows plaintext token once + QR code
- "Revoke" button per token with confirmation

**Static assets:** Minimal CSS (no framework needed), small JS file for SSE and interactive elements.

## Security

- **OIDC**: Confidential client with secret, validates ID token signature and claims
- **API tokens**: bcrypt-hashed, constant-time comparison
- **Session cookies**: Signed, `HttpOnly`, `SameSite=Lax`, `Secure` in production
- **Input validation**: Pydantic on all API inputs
- **CORS**: Configured for known origins only
- **HTTPS**: Enforced at reverse proxy (Caddy/nginx)
- **Secrets**: `.env` gitignored, no secrets in code

## Development Workflow

### Getting Started
```bash
git clone <repo-url> && cd priority-notify
cp .env.example .env           # Edit with your Authentik values
uv sync                        # Install dependencies
make db-upgrade                # Run Alembic migrations
make dev                       # Start uvicorn with reload
```

### Makefile Targets
```makefile
dev          # uvicorn app.main:app --reload
test         # pytest
lint         # ruff check + ruff format --check
typecheck    # mypy app/
db-upgrade   # alembic upgrade head
db-revision  # alembic revision --autogenerate -m "..."
```

### Branch Strategy
`main` only. Short-lived feature branches when needed.

### Testing
- Tests in `tests/`, mirroring route modules
- SQLite in-memory for test DB
- Mock Authentik OIDC responses
- `make test` runs everything; CI runs the same

## Open Questions

1. **Notification expiry**: Should old notifications auto-archive after N days?
2. **Backup strategy**: Cron copying the SQLite file, or more structured?
