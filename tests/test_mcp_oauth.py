import base64
import hashlib
import re
import time
from collections.abc import AsyncGenerator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import AsyncClient

from app import oauth
from app.config import Settings, get_settings
from app.main import app
from app.models import ClientToken, TokenScope, User
from app.oauth import (
    ClientMetadata,
    InvalidClientError,
    _parse_client_metadata,
    redirect_uri_allowed,
    validate_client_id_url,
)
from app.routes.auth import _safe_next
from tests.conftest import TEST_TOKEN_PLAINTEXT

CLIENT_ID = "https://claude.ai/oauth/mcp-oauth-client-metadata"
REDIRECT_URI = "https://claude.ai/api/mcp/auth_callback"
VERIFIER = "v" * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=")
ALL_SCOPES = "notifications:read notifications:write notifications:delete user.profile:read"


def _settings(**overrides: Any) -> Settings:
    return get_settings().model_copy(
        update={
            "PUBLIC_URL": "http://test",
            "MCP_ENABLED": True,
            "OAUTH_SERVER_ENABLED": True,
            "OAUTH_CIMD_ALLOWED_HOSTS": "claude.ai",
            **overrides,
        }
    )


@pytest.fixture
async def mcp_client(client: AsyncClient) -> AsyncGenerator[AsyncClient]:
    app.dependency_overrides[get_settings] = lambda: _settings()
    # Stand in for fetching Claude's CIMD document over the network.
    oauth._cimd_cache[CLIENT_ID] = (
        time.monotonic() + 60,
        ClientMetadata(
            client_id=CLIENT_ID,
            client_name="Claude",
            client_uri="https://claude.ai",
            redirect_uris=(REDIRECT_URI,),
        ),
    )
    yield client
    oauth._cimd_cache.clear()


def _authorize_params(**overrides: str) -> dict[str, str]:
    return {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "state": "xyz",
        "code_challenge": CHALLENGE.decode(),
        "code_challenge_method": "S256",
        "scope": ALL_SCOPES,
        **overrides,
    }


async def _authorize(
    client: AsyncClient, session_cookie: str, scopes: list[str] | None = None
) -> str:
    resp = await client.get(
        "/oauth/authorize", params=_authorize_params(), cookies={"session": session_cookie}
    )
    assert resp.status_code == 200
    assert resp.headers["x-frame-options"] == "DENY"
    consent_request = re.search(r'name="consent_request" value="([^"]+)"', resp.text)
    assert consent_request

    resp = await client.post(
        "/oauth/authorize",
        data={
            "consent_request": consent_request.group(1),
            "action": "allow",
            "scope": scopes if scopes is not None else ALL_SCOPES.split(),
        },
        cookies={"session": session_cookie},
    )
    assert resp.status_code == 302
    location = urlsplit(resp.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == REDIRECT_URI
    query = parse_qs(location.query)
    assert query["state"] == ["xyz"]
    assert query["iss"] == ["http://test"]
    return query["code"][0]


async def _exchange(client: AsyncClient, code: str, verifier: str = VERIFIER) -> Any:
    return await client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
        },
    )


async def _login(client: AsyncClient, session_cookie: str, scopes: list[str] | None = None) -> Any:
    code = await _authorize(client, session_cookie, scopes)
    resp = await _exchange(client, code)
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"
    return resp.json()


async def _rpc(
    client: AsyncClient, token: str, method: str, params: dict[str, Any] | None = None
) -> Any:
    return await client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        headers={"Authorization": f"Bearer {token}"},
    )


async def _call(client: AsyncClient, token: str, name: str, **arguments: Any) -> Any:
    return await _rpc(client, token, "tools/call", {"name": name, "arguments": arguments})


# --- Discovery ---


async def test_metadata_endpoints(mcp_client: AsyncClient) -> None:
    resp = await mcp_client.get("/.well-known/oauth-protected-resource/api/mcp")
    assert resp.json()["resource"] == "http://test/api/mcp"
    assert resp.json()["authorization_servers"] == ["http://test"]

    resp = await mcp_client.get("/.well-known/oauth-authorization-server")
    meta = resp.json()
    assert meta["issuer"] == "http://test"
    assert meta["code_challenge_methods_supported"] == ["S256"]
    assert meta["client_id_metadata_document_supported"] is True


async def test_disabled_by_default(client: AsyncClient) -> None:
    app.dependency_overrides[get_settings] = lambda: _settings(
        MCP_ENABLED=False, OAUTH_SERVER_ENABLED=False
    )
    assert (await client.post("/api/mcp", json={})).status_code == 404
    assert (await client.get("/.well-known/oauth-authorization-server")).status_code == 404
    assert (await client.get("/oauth/authorize")).status_code == 404


async def test_mcp_unauthenticated_challenge(mcp_client: AsyncClient) -> None:
    resp = await _rpc(mcp_client, "nope", "initialize")
    assert resp.status_code == 401
    challenge = resp.headers["www-authenticate"]
    assert 'resource_metadata="http://test/.well-known/oauth-protected-resource/api/mcp"' in (
        challenge
    )

    resp = await mcp_client.post("/api/mcp", json={})
    assert resp.status_code == 401


# --- Full sign-in flow ---


async def test_authorize_redirects_to_login_when_signed_out(mcp_client: AsyncClient) -> None:
    resp = await mcp_client.get("/oauth/authorize", params=_authorize_params())
    assert resp.status_code == 302
    location = urlsplit(resp.headers["location"])
    assert location.path == "/auth/login"
    assert parse_qs(location.query)["next"][0].startswith("/oauth/authorize?")


async def test_full_flow_and_tools(
    mcp_client: AsyncClient, test_user: User, session_cookie: str
) -> None:
    tokens = await _login(mcp_client, session_cookie)
    assert tokens["access_token"].startswith("pn_at_")
    assert tokens["refresh_token"].startswith("pn_rt_")
    assert tokens["scope"] == ALL_SCOPES
    at = tokens["access_token"]

    resp = await _rpc(mcp_client, at, "initialize", {"protocolVersion": "2025-06-18"})
    assert resp.json()["result"]["protocolVersion"] == "2025-06-18"
    assert resp.json()["result"]["serverInfo"]["name"] == "priority-notify"

    resp = await mcp_client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"Authorization": f"Bearer {at}"},
    )
    assert resp.status_code == 202

    resp = await _rpc(mcp_client, at, "tools/list")
    tools = {t["name"]: t for t in resp.json()["result"]["tools"]}
    assert "send_notification" in tools
    assert "$defs" not in tools["list_notifications"]["inputSchema"]
    assert tools["delete_notification"]["annotations"]["destructiveHint"] is True

    resp = await _call(mcp_client, at, "whoami")
    assert resp.json()["result"]["structuredContent"]["email"] == "test@example.com"

    resp = await _call(mcp_client, at, "send_notification", title="Disk full", priority="high")
    sent = resp.json()["result"]["structuredContent"]
    assert sent["title"] == "Disk full"

    resp = await _call(mcp_client, at, "list_notifications", status="unread", search="disk")
    listing = resp.json()["result"]["structuredContent"]
    assert listing["total"] == 1

    resp = await _call(
        mcp_client, at, "update_notification_status", notification_id=sent["id"], status="read"
    )
    assert resp.json()["result"]["structuredContent"]["status"] == "read"

    resp = await _call(mcp_client, at, "delete_notification", notification_id=sent["id"])
    assert resp.json()["result"]["structuredContent"] == {"deleted": sent["id"]}

    resp = await _call(mcp_client, at, "get_notification", notification_id=sent["id"])
    assert resp.json()["result"]["isError"] is True


async def test_tool_errors(mcp_client: AsyncClient, test_user: User, session_cookie: str) -> None:
    at = (await _login(mcp_client, session_cookie))["access_token"]

    resp = await _call(mcp_client, at, "send_notification", priority="urgent")
    assert resp.json()["result"]["isError"] is True

    resp = await _call(mcp_client, at, "no_such_tool")
    assert resp.json()["error"]["code"] == -32602

    resp = await _rpc(mcp_client, at, "resources/list")
    assert resp.json()["error"]["code"] == -32601


async def test_insufficient_scope_step_up(
    mcp_client: AsyncClient, test_user: User, session_cookie: str
) -> None:
    tokens = await _login(mcp_client, session_cookie, scopes=["notifications:read"])
    assert tokens["scope"] == "notifications:read"

    resp = await _call(mcp_client, tokens["access_token"], "send_notification", title="x")
    assert resp.status_code == 403
    challenge = resp.headers["www-authenticate"]
    assert 'error="insufficient_scope"' in challenge
    assert 'scope="notifications:read notifications:write"' in challenge


async def test_consent_denied(
    mcp_client: AsyncClient, test_user: User, session_cookie: str
) -> None:
    resp = await mcp_client.get(
        "/oauth/authorize", params=_authorize_params(), cookies={"session": session_cookie}
    )
    consent_request = re.search(r'name="consent_request" value="([^"]+)"', resp.text)
    assert consent_request
    resp = await mcp_client.post(
        "/oauth/authorize",
        data={"consent_request": consent_request.group(1), "action": "deny"},
        cookies={"session": session_cookie},
    )
    assert parse_qs(urlsplit(resp.headers["location"]).query)["error"] == ["access_denied"]


async def test_consent_request_is_signed(
    mcp_client: AsyncClient, test_user: User, session_cookie: str
) -> None:
    resp = await mcp_client.post(
        "/oauth/authorize",
        data={"consent_request": "forged", "action": "allow", "scope": "notifications:read"},
        cookies={"session": session_cookie},
    )
    assert resp.status_code == 400
    assert "location" not in resp.headers


# --- Token endpoint security ---


async def test_code_is_single_use(
    mcp_client: AsyncClient, test_user: User, session_cookie: str
) -> None:
    code = await _authorize(mcp_client, session_cookie)
    assert (await _exchange(mcp_client, code)).status_code == 200
    resp = await _exchange(mcp_client, code)
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


async def test_pkce_mismatch_burns_code(
    mcp_client: AsyncClient, test_user: User, session_cookie: str
) -> None:
    code = await _authorize(mcp_client, session_cookie)
    assert (await _exchange(mcp_client, code, verifier="w" * 64)).status_code == 400
    assert (await _exchange(mcp_client, code)).status_code == 400


async def test_refresh_rotates(
    mcp_client: AsyncClient, test_user: User, session_cookie: str
) -> None:
    tokens = await _login(mcp_client, session_cookie)
    body = {
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": CLIENT_ID,
    }
    resp = await mcp_client.post("/oauth/token", data=body)
    assert resp.status_code == 200
    new = resp.json()
    assert new["refresh_token"] != tokens["refresh_token"]
    assert (await _rpc(mcp_client, new["access_token"], "ping")).status_code == 200

    # The old refresh token is gone after rotation.
    assert (await mcp_client.post("/oauth/token", data=body)).status_code == 400

    # Another client can't use it either.
    resp = await mcp_client.post(
        "/oauth/token",
        data={**body, "refresh_token": new["refresh_token"], "client_id": "https://evil/x"},
    )
    assert resp.status_code == 400


async def test_revoking_grant_kills_tokens(
    mcp_client: AsyncClient, test_user: User, session_cookie: str
) -> None:
    at = (await _login(mcp_client, session_cookie))["access_token"]
    grants = (await mcp_client.get("/api/oauth/grants", cookies={"session": session_cookie})).json()
    assert [g["client_name"] for g in grants] == ["Claude"]

    resp = await mcp_client.delete(
        f"/api/oauth/grants/{grants[0]['id']}", cookies={"session": session_cookie}
    )
    assert resp.status_code == 204
    assert (await _rpc(mcp_client, at, "ping")).status_code == 401


async def test_revoke_endpoint(
    mcp_client: AsyncClient, test_user: User, session_cookie: str
) -> None:
    at = (await _login(mcp_client, session_cookie))["access_token"]
    resp = await mcp_client.post("/oauth/revoke", data={"token": at, "client_id": CLIENT_ID})
    assert resp.status_code == 200
    assert (await _rpc(mcp_client, at, "ping")).status_code == 401


# --- Client validation ---


async def test_unlisted_client_host_rejected(
    mcp_client: AsyncClient, test_user: User, session_cookie: str
) -> None:
    resp = await mcp_client.get(
        "/oauth/authorize",
        params=_authorize_params(client_id="https://evil.example/client.json"),
        cookies={"session": session_cookie},
    )
    assert resp.status_code == 400
    assert "location" not in resp.headers


async def test_unregistered_redirect_uri_rejected(
    mcp_client: AsyncClient, test_user: User, session_cookie: str
) -> None:
    resp = await mcp_client.get(
        "/oauth/authorize",
        params=_authorize_params(redirect_uri="https://evil.example/cb"),
        cookies={"session": session_cookie},
    )
    assert resp.status_code == 400
    assert "location" not in resp.headers


async def test_pkce_required(mcp_client: AsyncClient, session_cookie: str) -> None:
    resp = await mcp_client.get(
        "/oauth/authorize",
        params=_authorize_params(code_challenge_method="plain"),
        cookies={"session": session_cookie},
    )
    assert resp.status_code == 302
    assert parse_qs(urlsplit(resp.headers["location"]).query)["error"] == ["invalid_request"]


def test_validate_client_id_url() -> None:
    settings = _settings()
    validate_client_id_url(CLIENT_ID, settings)
    for bad in [
        "http://claude.ai/meta",
        "https://claude.ai/",
        "https://claude.ai:8443/meta",
        "https://user@claude.ai/meta",
        "https://claude.ai/meta#frag",
        "https://claude.ai.evil.com/meta",
    ]:
        with pytest.raises(InvalidClientError):
            validate_client_id_url(bad, settings)


def test_parse_client_metadata() -> None:
    doc = {"client_id": CLIENT_ID, "client_name": "Claude", "redirect_uris": [REDIRECT_URI]}
    assert _parse_client_metadata(CLIENT_ID, doc).client_name == "Claude"
    with pytest.raises(InvalidClientError):
        _parse_client_metadata(CLIENT_ID, {**doc, "client_id": "https://claude.ai/other"})
    with pytest.raises(InvalidClientError):
        _parse_client_metadata(CLIENT_ID, {**doc, "token_endpoint_auth_method": "client_secret"})
    with pytest.raises(InvalidClientError):
        _parse_client_metadata(CLIENT_ID, {**doc, "redirect_uris": []})


def test_loopback_redirect_ignores_port() -> None:
    registered = ("http://localhost/callback",)
    assert redirect_uri_allowed("http://localhost:53682/callback", registered)
    assert not redirect_uri_allowed("http://localhost:53682/other", registered)
    assert not redirect_uri_allowed("https://claude.ai/callback", registered)


def test_safe_next() -> None:
    assert _safe_next("/oauth/authorize?x=1") == "/oauth/authorize?x=1"
    for bad in [None, "", "https://evil.com", "//evil.com", "/\\evil.com"]:
        assert _safe_next(bad) == "/"


# --- API tokens on the MCP endpoint ---


async def test_api_token_on_mcp(mcp_client: AsyncClient, test_token: ClientToken) -> None:
    # The fixture token has the default `write` scope: can send, can't read.
    resp = await _call(mcp_client, TEST_TOKEN_PLAINTEXT, "send_notification", title="from CI")
    assert resp.json()["result"]["isError"] is False

    resp = await _call(mcp_client, TEST_TOKEN_PLAINTEXT, "list_notifications")
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["isError"] is True
    assert "notifications:read" in result["content"][0]["text"]


async def test_full_api_token_can_read(
    mcp_client: AsyncClient, test_token: ClientToken, db: Any
) -> None:
    test_token.scope = TokenScope.full
    await db.commit()
    resp = await _call(mcp_client, TEST_TOKEN_PLAINTEXT, "list_notifications")
    assert resp.json()["result"]["isError"] is False
