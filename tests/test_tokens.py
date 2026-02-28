import pytest
from httpx import AsyncClient

from app.models import User


@pytest.mark.asyncio
async def test_create_token(client: AsyncClient, test_user: User, session_cookie: str) -> None:
    resp = await client.post(
        "/api/tokens/",
        json={"name": "My Phone", "device_type": "android"},
        cookies={"session": session_cookie},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "My Phone"
    assert data["device_type"] == "android"
    assert "token" in data
    assert len(data["token"]) > 20


@pytest.mark.asyncio
async def test_create_token_requires_session(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/tokens/",
        json={"name": "No Session"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_tokens(client: AsyncClient, test_user: User, session_cookie: str) -> None:
    await client.post(
        "/api/tokens/",
        json={"name": "Device A"},
        cookies={"session": session_cookie},
    )
    await client.post(
        "/api/tokens/",
        json={"name": "Device B"},
        cookies={"session": session_cookie},
    )

    resp = await client.get(
        "/api/tokens/",
        cookies={"session": session_cookie},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    names = {t["name"] for t in data}
    assert names == {"Device A", "Device B"}
    # No plaintext token in list
    for t in data:
        assert "token" not in t


@pytest.mark.asyncio
async def test_revoke_token(client: AsyncClient, test_user: User, session_cookie: str) -> None:
    create_resp = await client.post(
        "/api/tokens/",
        json={"name": "To Revoke"},
        cookies={"session": session_cookie},
    )
    token_id = create_resp.json()["id"]

    resp = await client.delete(
        f"/api/tokens/{token_id}",
        cookies={"session": session_cookie},
    )
    assert resp.status_code == 204

    # Verify it's gone
    resp = await client.get(
        "/api/tokens/",
        cookies={"session": session_cookie},
    )
    assert len(resp.json()) == 0


@pytest.mark.asyncio
async def test_revoke_nonexistent_token(
    client: AsyncClient, test_user: User, session_cookie: str
) -> None:
    resp = await client.delete(
        "/api/tokens/nonexistent",
        cookies={"session": session_cookie},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_created_token_works_for_api(
    client: AsyncClient, test_user: User, session_cookie: str
) -> None:
    create_resp = await client.post(
        "/api/tokens/",
        json={"name": "API Device"},
        cookies={"session": session_cookie},
    )
    plaintext = create_resp.json()["token"]

    # Use token to create a notification
    resp = await client.post(
        "/api/notifications/",
        json={"title": "Via new token"},
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert resp.status_code == 201
    assert resp.json()["user_id"] == test_user.id
