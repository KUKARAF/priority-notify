from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.auth import SessionManager, generate_api_token, hash_token
from app.models import User


@pytest.mark.asyncio
async def test_session_create_and_validate() -> None:
    sm = SessionManager("test-secret")
    token = sm.create_session("user-123")
    assert sm.validate_session(token) == "user-123"


@pytest.mark.asyncio
async def test_session_invalid_token() -> None:
    sm = SessionManager("test-secret")
    assert sm.validate_session("garbage") is None


@pytest.mark.asyncio
async def test_session_wrong_secret() -> None:
    sm1 = SessionManager("secret-1")
    sm2 = SessionManager("secret-2")
    token = sm1.create_session("user-123")
    assert sm2.validate_session(token) is None


@pytest.mark.asyncio
async def test_api_token_hash_verify() -> None:
    import bcrypt

    plaintext = generate_api_token()
    hashed = hash_token(plaintext)
    assert bcrypt.checkpw(plaintext.encode(), hashed.encode())
    assert not bcrypt.checkpw(b"wrong-token", hashed.encode())


@pytest.mark.asyncio
async def test_me_endpoint_with_session(
    client: AsyncClient, test_user: User, session_cookie: str
) -> None:
    resp = await client.get(
        "/api/me",
        cookies={"session": session_cookie},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "test@example.com"
    assert data["name"] == "Test User"


@pytest.mark.asyncio
async def test_me_endpoint_no_auth(client: AsyncClient) -> None:
    resp = await client.get("/api/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_redirect(client: AsyncClient) -> None:
    mock_config = {
        "authorization_endpoint": "https://auth.osmosis.page/authorize",
        "token_endpoint": "https://auth.osmosis.page/token",
        "jwks_uri": "https://auth.osmosis.page/jwks",
    }
    with patch("app.routes.auth.get_oidc_config", new_callable=AsyncMock, return_value=mock_config):
        resp = await client.get("/auth/login", follow_redirects=False)
    assert resp.status_code == 307
    location = resp.headers.get("location", "")
    assert "auth.osmosis.page" in location


@pytest.mark.asyncio
async def test_health(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
