import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app import fcm
from app.config import Settings
from app.models import Notification, Priority, PushDevice, User
from app.sse import broker
from tests.conftest import TEST_TOKEN_PLAINTEXT

# --- Device registration API ---


@pytest.mark.asyncio
async def test_register_device_creates_row(
    client: AsyncClient, test_user: User, session_cookie: str, db: AsyncSession
) -> None:
    resp = await client.post(
        "/api/push/devices",
        json={"fcm_token": "fcm-token-abc", "device_type": "android", "label": "Pixel"},
        cookies={"session": session_cookie},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["device_type"] == "android"
    assert data["label"] == "Pixel"
    # The FCM token is a secret and must never be echoed back.
    assert "fcm_token" not in data

    rows = (
        (await db.execute(select(PushDevice).where(PushDevice.user_id == test_user.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].fcm_token == "fcm-token-abc"


@pytest.mark.asyncio
async def test_register_device_upserts_on_token(
    client: AsyncClient, test_user: User, session_cookie: str, db: AsyncSession
) -> None:
    cookies = {"session": session_cookie}
    await client.post(
        "/api/push/devices",
        json={"fcm_token": "same-token", "label": "First"},
        cookies=cookies,
    )
    resp = await client.post(
        "/api/push/devices",
        json={"fcm_token": "same-token", "label": "Renamed"},
        cookies=cookies,
    )
    assert resp.status_code == 201
    assert resp.json()["label"] == "Renamed"

    rows = (await db.execute(select(PushDevice))).scalars().all()
    assert len(rows) == 1  # upsert, not a duplicate


@pytest.mark.asyncio
async def test_list_and_delete_devices(
    client: AsyncClient, test_user: User, session_cookie: str
) -> None:
    cookies = {"session": session_cookie}
    created = await client.post("/api/push/devices", json={"fcm_token": "t1"}, cookies=cookies)
    device_id = created.json()["id"]

    listed = await client.get("/api/push/devices", cookies=cookies)
    assert listed.status_code == 200
    assert [d["id"] for d in listed.json()] == [device_id]

    assert (
        await client.delete(f"/api/push/devices/{device_id}", cookies=cookies)
    ).status_code == 204
    assert (await client.get("/api/push/devices", cookies=cookies)).json() == []


@pytest.mark.asyncio
async def test_delete_device_requires_ownership(
    client: AsyncClient, test_user: User, session_cookie: str, db: AsyncSession
) -> None:
    other = User(id=str(uuid.uuid4()), sub="other", email="o@e.com", name="Other")
    db.add(other)
    foreign = PushDevice(user_id=other.id, fcm_token="foreign", label="Theirs")
    db.add_all([other, foreign])
    await db.commit()

    resp = await client.delete(
        f"/api/push/devices/{foreign.id}", cookies={"session": session_cookie}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_register_device_requires_auth(client: AsyncClient) -> None:
    resp = await client.post("/api/push/devices", json={"fcm_token": "x"})
    assert resp.status_code == 401


# --- FCM wiring into notification creation ---


@pytest.mark.asyncio
async def test_create_notification_invokes_fcm_and_still_fires_sse(
    client: AsyncClient,
    test_user: User,
    test_token: object,
    session_cookie: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await client.post(
        "/api/push/devices",
        json={"fcm_token": "device-token"},
        cookies={"session": session_cookie},
    )

    calls: list[str] = []

    async def fake_send_to_user(db: AsyncSession, user_id: str, notification: Notification) -> None:
        calls.append(user_id)

    monkeypatch.setattr(fcm, "send_to_user", fake_send_to_user)

    queue = broker.subscribe(test_user.id)
    try:
        resp = await client.post(
            "/api/notifications/",
            json={"title": "Pushed alert", "priority": "high"},
            headers={"Authorization": f"Bearer {TEST_TOKEN_PLAINTEXT}"},
        )
        assert resp.status_code == 201
        # FCM was invoked...
        assert calls == [test_user.id]
        # ...and the SSE broadcast still fired.
        event = queue.get_nowait()
        assert event["event"] == "notification"
    finally:
        broker.unsubscribe(test_user.id, queue)


@pytest.mark.asyncio
async def test_notification_creation_survives_fcm_failure(
    client: AsyncClient,
    test_user: User,
    test_token: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # send_to_user swallows network/auth errors internally; the route additionally guards
    # against a DB-layer failure (device lookup/prune) so creation always survives.
    async def boom(db: AsyncSession, user_id: str, notification: Notification) -> None:
        raise SQLAlchemyError("fcm db exploded")

    monkeypatch.setattr(fcm, "send_to_user", boom)

    resp = await client.post(
        "/api/notifications/",
        json={"title": "Still created"},
        headers={"Authorization": f"Bearer {TEST_TOKEN_PLAINTEXT}"},
    )
    assert resp.status_code == 201


# --- send_to_user behaviour ---


def _notification(user_id: str) -> Notification:
    return Notification(
        id=str(uuid.uuid4()),
        user_id=user_id,
        title="Hello",
        message="Body text",
        priority=Priority.high,
        source="ci",
    )


@pytest.mark.asyncio
async def test_send_to_user_noops_when_push_disabled(
    db: AsyncSession, test_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    device = PushDevice(user_id=test_user.id, fcm_token="d1")
    db.add(device)
    await db.commit()

    # Default settings leave FCM unconfigured => push_enabled is False.
    assert Settings().push_enabled is False

    def no_http(*args: object, **kwargs: object) -> None:
        raise AssertionError("push disabled must not touch the network")

    monkeypatch.setattr(fcm.httpx, "AsyncClient", no_http)

    await fcm.send_to_user(db, test_user.id, _notification(test_user.id))

    # Device untouched, no error, no network call.
    rows = (await db.execute(select(PushDevice))).scalars().all()
    assert len(rows) == 1


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.posts: list[dict[str, object]] = []

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def post(
        self, url: str, headers: dict[str, str] | None = None, json: dict[str, object] | None = None
    ) -> _FakeResponse:
        self.posts.append({"url": url, "headers": headers, "json": json})
        return self._response


def _enable_push(monkeypatch: pytest.MonkeyPatch) -> None:
    configured = Settings(FCM_PROJECT_ID="demo-project", FCM_SERVICE_ACCOUNT_FILE="/tmp/sa.json")
    assert configured.push_enabled is True
    monkeypatch.setattr(fcm, "get_settings", lambda: configured)

    async def fake_token(settings: Settings) -> str:
        return "fake-access-token"

    monkeypatch.setattr(fcm, "_get_access_token", fake_token)


@pytest.mark.asyncio
async def test_send_to_user_prunes_stale_token(
    db: AsyncSession, test_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    device = PushDevice(user_id=test_user.id, fcm_token="stale")
    db.add(device)
    await db.commit()

    _enable_push(monkeypatch)
    fake = _FakeAsyncClient(_FakeResponse(404, '{"error": {"status": "NOT_FOUND"}}'))
    monkeypatch.setattr(fcm.httpx, "AsyncClient", lambda **kwargs: fake)

    await fcm.send_to_user(db, test_user.id, _notification(test_user.id))

    # The 404 marks the token stale, so its row is deleted.
    rows = (await db.execute(select(PushDevice))).scalars().all()
    assert rows == []
    # A data-only message was sent (no top-level "notification" block).
    assert len(fake.posts) == 1
    message = fake.posts[0]["json"]["message"]  # type: ignore[index]
    assert "notification" not in message
    assert message["data"]["title"] == "Hello"
    assert message["data"]["body"] == "Body text"
    assert message["android"] == {"priority": "high"}


@pytest.mark.asyncio
async def test_send_to_user_keeps_token_on_success(
    db: AsyncSession, test_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    device = PushDevice(user_id=test_user.id, fcm_token="good")
    db.add(device)
    await db.commit()

    _enable_push(monkeypatch)
    fake = _FakeAsyncClient(_FakeResponse(200, '{"name": "projects/demo/messages/1"}'))
    monkeypatch.setattr(fcm.httpx, "AsyncClient", lambda **kwargs: fake)

    await fcm.send_to_user(db, test_user.id, _notification(test_user.id))

    rows = (await db.execute(select(PushDevice))).scalars().all()
    assert len(rows) == 1
