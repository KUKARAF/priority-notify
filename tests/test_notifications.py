import pytest
from httpx import AsyncClient

from app.models import ClientToken, User
from tests.conftest import TEST_TOKEN_PLAINTEXT


@pytest.mark.asyncio
async def test_create_notification_with_token(
    client: AsyncClient, test_user: User, test_token: ClientToken
) -> None:
    resp = await client.post(
        "/api/notifications/",
        json={"title": "Test alert", "priority": "high", "source": "ci"},
        headers={"Authorization": f"Bearer {TEST_TOKEN_PLAINTEXT}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Test alert"
    assert data["priority"] == "high"
    assert data["source"] == "ci"
    assert data["status"] == "unread"
    assert data["user_id"] == test_user.id


@pytest.mark.asyncio
async def test_create_notification_requires_token(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/notifications/",
        json={"title": "No auth"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_notifications(
    client: AsyncClient, test_user: User, test_token: ClientToken, session_cookie: str
) -> None:
    # Create a notification
    await client.post(
        "/api/notifications/",
        json={"title": "Listed"},
        headers={"Authorization": f"Bearer {TEST_TOKEN_PLAINTEXT}"},
    )

    # List via session
    resp = await client.get(
        "/api/notifications/",
        cookies={"session": session_cookie},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["title"] == "Listed"


@pytest.mark.asyncio
async def test_list_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/api/notifications/")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_notification(
    client: AsyncClient, test_user: User, test_token: ClientToken, session_cookie: str
) -> None:
    create_resp = await client.post(
        "/api/notifications/",
        json={"title": "Single"},
        headers={"Authorization": f"Bearer {TEST_TOKEN_PLAINTEXT}"},
    )
    nid = create_resp.json()["id"]

    resp = await client.get(
        f"/api/notifications/{nid}",
        cookies={"session": session_cookie},
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "Single"


@pytest.mark.asyncio
async def test_get_notification_not_found(
    client: AsyncClient, test_user: User, session_cookie: str
) -> None:
    resp = await client.get(
        "/api/notifications/nonexistent",
        cookies={"session": session_cookie},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_notification_status(
    client: AsyncClient, test_user: User, test_token: ClientToken, session_cookie: str
) -> None:
    create_resp = await client.post(
        "/api/notifications/",
        json={"title": "To read"},
        headers={"Authorization": f"Bearer {TEST_TOKEN_PLAINTEXT}"},
    )
    nid = create_resp.json()["id"]

    resp = await client.patch(
        f"/api/notifications/{nid}",
        json={"status": "read"},
        cookies={"session": session_cookie},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "read"
    assert resp.json()["read_at"] is not None


@pytest.mark.asyncio
async def test_delete_notification(
    client: AsyncClient, test_user: User, test_token: ClientToken, session_cookie: str
) -> None:
    create_resp = await client.post(
        "/api/notifications/",
        json={"title": "To delete"},
        headers={"Authorization": f"Bearer {TEST_TOKEN_PLAINTEXT}"},
    )
    nid = create_resp.json()["id"]

    resp = await client.delete(
        f"/api/notifications/{nid}",
        cookies={"session": session_cookie},
    )
    assert resp.status_code == 204

    # Verify deleted
    resp = await client.get(
        f"/api/notifications/{nid}",
        cookies={"session": session_cookie},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_filter_by_status(
    client: AsyncClient, test_user: User, test_token: ClientToken, session_cookie: str
) -> None:
    await client.post(
        "/api/notifications/",
        json={"title": "Unread one"},
        headers={"Authorization": f"Bearer {TEST_TOKEN_PLAINTEXT}"},
    )

    resp = await client.get(
        "/api/notifications/?status=unread",
        cookies={"session": session_cookie},
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    resp = await client.get(
        "/api/notifications/?status=read",
        cookies={"session": session_cookie},
    )
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_filter_by_priority(
    client: AsyncClient, test_user: User, test_token: ClientToken, session_cookie: str
) -> None:
    await client.post(
        "/api/notifications/",
        json={"title": "Critical one", "priority": "critical"},
        headers={"Authorization": f"Bearer {TEST_TOKEN_PLAINTEXT}"},
    )
    await client.post(
        "/api/notifications/",
        json={"title": "Low one", "priority": "low"},
        headers={"Authorization": f"Bearer {TEST_TOKEN_PLAINTEXT}"},
    )

    resp = await client.get(
        "/api/notifications/?priority=critical",
        cookies={"session": session_cookie},
    )
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["title"] == "Critical one"


@pytest.mark.asyncio
async def test_pagination(
    client: AsyncClient, test_user: User, test_token: ClientToken, session_cookie: str
) -> None:
    for i in range(5):
        await client.post(
            "/api/notifications/",
            json={"title": f"Notification {i}"},
            headers={"Authorization": f"Bearer {TEST_TOKEN_PLAINTEXT}"},
        )

    resp = await client.get(
        "/api/notifications/?limit=2&offset=0",
        cookies={"session": session_cookie},
    )
    data = resp.json()
    assert data["total"] == 5
    assert len(data["items"]) == 2
    assert data["limit"] == 2
    assert data["offset"] == 0
