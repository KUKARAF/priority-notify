"""Built-in OAuth 2.1 authorization server for MCP clients (Claude, ChatGPT, ...).

Modelled on Grist's MCP sign-in: the assistant is a public client that identifies itself
with a Client ID Metadata Document (CIMD) — its `client_id` is an https URL we fetch to
learn its name and redirect URIs — restricted to an allowlist of hosts. The user signs in
through the normal Authentik OIDC login, approves scopes on a consent screen, and the
client gets opaque, prefixed access/refresh tokens. PKCE (S256) is mandatory.
"""

import base64
import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urlsplit

import httpx
import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import lookup_hash
from app.config import Settings
from app.models import (
    OAuthAuthorizationCode,
    OAuthGrant,
    OAuthToken,
    OAuthTokenKind,
    TokenScope,
    User,
)

log = structlog.get_logger()

# Scope -> label shown on the consent screen, in display order.
SCOPES: dict[str, str] = {
    "notifications:read": "Read your notifications and mark them read, unread or archived",
    "notifications:write": "Send notifications to you",
    "notifications:delete": "Delete notifications",
    "user.profile:read": "See your name and email address",
    "offline_access": "Stay signed in without asking you again",
}

# What an API token's scope grants when it's used on the MCP endpoint instead of OAuth.
API_TOKEN_SCOPES: dict[TokenScope, frozenset[str]] = {
    TokenScope.read: frozenset({"notifications:read", "user.profile:read"}),
    TokenScope.write: frozenset({"notifications:write", "user.profile:read"}),
    TokenScope.delete: frozenset({"notifications:delete", "user.profile:read"}),
    TokenScope.full: frozenset(SCOPES),
}

ACCESS_TOKEN_PREFIX = "pn_at_"
REFRESH_TOKEN_PREFIX = "pn_rt_"
ACCESS_TOKEN_TTL = timedelta(hours=1)
REFRESH_TOKEN_TTL = timedelta(days=60)
AUTHORIZATION_CODE_TTL = timedelta(minutes=10)

MCP_PATH = "/api/mcp"

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_PKCE_VERIFIER_RE = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")


def utcnow() -> datetime:
    # Naive UTC, matching how the SQLite DateTime columns round-trip.
    return datetime.now(UTC).replace(tzinfo=None)


def parse_scope(raw: str | None) -> list[str]:
    """Known scopes from a space-separated string, in canonical order.

    Unknown scopes are dropped rather than rejected, and an empty result means "everything
    we offer": clients often ask for nothing specific, and the consent screen lets the user
    narrow it down anyway.
    """
    requested = set((raw or "").split())
    known = [s for s in SCOPES if s in requested]
    return known or list(SCOPES)


def format_scope(scopes: list[str] | set[str] | frozenset[str]) -> str:
    return " ".join(s for s in SCOPES if s in scopes)


def resource_url(settings: Settings) -> str:
    return f"{settings.public_url}{MCP_PATH}"


def resource_metadata_url(settings: Settings) -> str:
    return f"{settings.public_url}/.well-known/oauth-protected-resource{MCP_PATH}"


# --- Client ID Metadata Documents ---


class InvalidClientError(Exception):
    pass


@dataclass(frozen=True)
class ClientMetadata:
    client_id: str
    client_name: str
    client_uri: str | None
    redirect_uris: tuple[str, ...]


CIMD_CACHE_TTL = 3600.0
CIMD_MAX_BYTES = 16 * 1024
_cimd_cache: dict[str, tuple[float, ClientMetadata]] = {}


def validate_client_id_url(client_id: str, settings: Settings) -> None:
    parts = urlsplit(client_id)
    if parts.scheme != "https":
        raise InvalidClientError("client_id must be an https URL")
    if parts.username or parts.password or parts.fragment or parts.port not in (None, 443):
        raise InvalidClientError("client_id URL must not contain credentials, port or fragment")
    if parts.path in ("", "/") or "/./" in parts.path or "/../" in parts.path:
        raise InvalidClientError("client_id URL must have a normalized, non-root path")
    host = (parts.hostname or "").lower()
    if host not in settings.cimd_allowed_hosts_list:
        raise InvalidClientError(f"Clients from {host or 'this host'} are not allowed")


async def get_client_metadata(client_id: str, settings: Settings) -> ClientMetadata:
    validate_client_id_url(client_id, settings)

    cached = _cimd_cache.get(client_id)
    if cached and cached[0] > time.monotonic():
        return cached[1]

    try:
        async with (
            httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client,
            client.stream("GET", client_id, headers={"Accept": "application/json"}) as resp,
        ):
            if resp.status_code != 200:
                raise InvalidClientError(f"Client metadata fetch returned {resp.status_code}")
            body = b""
            async for chunk in resp.aiter_bytes():
                body += chunk
                if len(body) > CIMD_MAX_BYTES:
                    raise InvalidClientError("Client metadata document is too large")
    except httpx.HTTPError as exc:
        raise InvalidClientError("Could not fetch client metadata document") from exc

    try:
        doc = json.loads(body)
    except ValueError as exc:
        raise InvalidClientError("Client metadata document is not valid JSON") from exc

    metadata = _parse_client_metadata(client_id, doc)
    _cimd_cache[client_id] = (time.monotonic() + CIMD_CACHE_TTL, metadata)
    return metadata


def _parse_client_metadata(client_id: str, doc: object) -> ClientMetadata:
    if not isinstance(doc, dict):
        raise InvalidClientError("Client metadata document must be a JSON object")
    if doc.get("client_id") != client_id:
        raise InvalidClientError("client_id in metadata document does not match its URL")
    # Public clients only: we have no way to verify a client secret or signed assertion.
    if doc.get("token_endpoint_auth_method", "none") != "none":
        raise InvalidClientError("Only token_endpoint_auth_method=none is supported")

    redirect_uris = doc.get("redirect_uris")
    if (
        not isinstance(redirect_uris, list)
        or not redirect_uris
        or not all(isinstance(u, str) for u in redirect_uris)
    ):
        raise InvalidClientError("Client metadata must list redirect_uris")

    name = doc.get("client_name")
    client_uri = doc.get("client_uri")
    return ClientMetadata(
        client_id=client_id,
        client_name=name if isinstance(name, str) and name else urlsplit(client_id).netloc,
        client_uri=client_uri if isinstance(client_uri, str) else None,
        redirect_uris=tuple(redirect_uris),
    )


def redirect_uri_allowed(requested: str, registered: tuple[str, ...]) -> bool:
    if requested in registered:
        return True
    # RFC 8252 §7.3: native apps listen on an ephemeral loopback port, so ignore the port.
    req = urlsplit(requested)
    if req.scheme != "http" or req.hostname not in _LOOPBACK_HOSTS:
        return False
    for uri in registered:
        reg = urlsplit(uri)
        if (reg.scheme, reg.hostname, reg.path, reg.query) == (
            req.scheme,
            req.hostname,
            req.path,
            req.query,
        ):
            return True
    return False


def with_query(uri: str, params: dict[str, str]) -> str:
    return f"{uri}{'&' if urlsplit(uri).query else '?'}{urlencode(params)}"


# --- PKCE ---


def verify_pkce(verifier: str, challenge: str) -> bool:
    if not _PKCE_VERIFIER_RE.match(verifier):
        return False
    digest = hashlib.sha256(verifier.encode()).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return secrets.compare_digest(expected, challenge)


# --- Tokens ---


async def issue_tokens(db: AsyncSession, grant: OAuthGrant, scope: str) -> dict[str, object]:
    """Mint an access + refresh token pair under `grant` and return the token response."""
    now = utcnow()
    await db.execute(delete(OAuthToken).where(OAuthToken.expires_at < now))

    access = ACCESS_TOKEN_PREFIX + secrets.token_urlsafe(32)
    refresh = REFRESH_TOKEN_PREFIX + secrets.token_urlsafe(32)
    db.add_all(
        [
            OAuthToken(
                grant_id=grant.id,
                kind=OAuthTokenKind.access,
                token_lookup=lookup_hash(access),
                scope=scope,
                expires_at=now + ACCESS_TOKEN_TTL,
            ),
            OAuthToken(
                grant_id=grant.id,
                kind=OAuthTokenKind.refresh,
                token_lookup=lookup_hash(refresh),
                scope=scope,
                expires_at=now + REFRESH_TOKEN_TTL,
            ),
        ]
    )
    grant.last_used_at = now
    await db.commit()

    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": int(ACCESS_TOKEN_TTL.total_seconds()),
        "refresh_token": refresh,
        "scope": scope,
    }


async def find_token(db: AsyncSession, raw: str, kind: OAuthTokenKind) -> OAuthToken | None:
    result = await db.execute(
        select(OAuthToken).where(
            OAuthToken.token_lookup == lookup_hash(raw),
            OAuthToken.kind == kind,
            OAuthToken.expires_at > utcnow(),
        )
    )
    return result.scalar_one_or_none()


async def authenticate_access_token(
    db: AsyncSession, raw: str
) -> tuple[User, frozenset[str]] | None:
    token = await find_token(db, raw, OAuthTokenKind.access)
    if token is None:
        return None
    grant = await db.get(OAuthGrant, token.grant_id)
    if grant is None:
        return None
    user = await db.get(User, grant.user_id)
    if user is None:
        return None
    grant.last_used_at = utcnow()
    await db.commit()
    return user, frozenset(token.scope.split())


async def delete_grant(db: AsyncSession, grant: OAuthGrant) -> None:
    """Revoke a grant and everything issued under it.

    Done explicitly: SQLite doesn't enforce ON DELETE CASCADE without PRAGMA foreign_keys.
    """
    await db.execute(delete(OAuthToken).where(OAuthToken.grant_id == grant.id))
    await db.execute(
        delete(OAuthAuthorizationCode).where(OAuthAuthorizationCode.grant_id == grant.id)
    )
    await db.delete(grant)
    await db.commit()
