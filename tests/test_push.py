import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app import push
from app.models import Notification, Priority, PushDevice, User
from app.sse import broker
from tests.conftest import TEST_TOKEN_PLAINTEXT

ENDPOINT = "https://ntfy.sh/upABCDEF1234"

# --- Endpoint registration API ---


@pytest.mark.asyncio
async def test_register_endpoint_creates_row(
    client: AsyncClient, test_user: User, session_cookie: str, db: AsyncSession
) -> None:
    resp = await client.post(
        "/api/push/register",
        json={"endpoint": ENDPOINT},
        cookies={"session": session_cookie},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "registered"}

    rows = (
        (await db.execute(select(PushDevice).where(PushDevice.user_id == test_user.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].endpoint == ENDPOINT
    assert rows[0].last_seen_at is not None


@pytest.mark.asyncio
async def test_register_endpoint_upserts_on_url(
    client: AsyncClient, test_user: User, session_cookie: str, db: AsyncSession
) -> None:
    cookies = {"session": session_cookie}
    await client.post("/api/push/register", json={"endpoint": ENDPOINT}, cookies=cookies)
    resp = await client.post("/api/push/register", json={"endpoint": ENDPOINT}, cookies=cookies)
    assert resp.status_code == 200

    rows = (await db.execute(select(PushDevice))).scalars().all()
    assert len(rows) == 1  # re-registering the same endpoint upserts, not a duplicate


@pytest.mark.asyncio
async def test_register_multiple_endpoints_per_user(
    client: AsyncClient, test_user: User, session_cookie: str, db: AsyncSession
) -> None:
    cookies = {"session": session_cookie}
    await client.post("/api/push/register", json={"endpoint": ENDPOINT}, cookies=cookies)
    await client.post(
        "/api/push/register", json={"endpoint": "https://ntfy.sh/upSECOND0000"}, cookies=cookies
    )

    rows = (await db.execute(select(PushDevice))).scalars().all()
    assert len(rows) == 2  # one per device


@pytest.mark.asyncio
async def test_register_endpoint_requires_auth(client: AsyncClient) -> None:
    resp = await client.post("/api/push/register", json={"endpoint": ENDPOINT})
    assert resp.status_code == 401


# --- UnifiedPush wiring into notification creation ---


@pytest.mark.asyncio
async def test_create_notification_posts_to_endpoint_and_still_fires_sse(
    client: AsyncClient,
    test_user: User,
    test_token: object,
    session_cookie: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await client.post(
        "/api/push/register",
        json={"endpoint": ENDPOINT},
        cookies={"session": session_cookie},
    )

    posts: list[dict[str, object]] = []
    fake = _FakeAsyncClient(_FakeResponse(200))
    fake.record = posts
    monkeypatch.setattr(push.httpx, "AsyncClient", lambda **kwargs: fake)

    queue = broker.subscribe(test_user.id)
    try:
        resp = await client.post(
            "/api/notifications/",
            json={"title": "Pushed alert", "priority": "high"},
            headers={"Authorization": f"Bearer {TEST_TOKEN_PLAINTEXT}"},
        )
        assert resp.status_code == 201

        # The notification JSON was POSTed to the registered endpoint URL...
        assert len(posts) == 1
        assert posts[0]["url"] == ENDPOINT
        body = posts[0]["json"]
        assert isinstance(body, dict)
        assert body["title"] == "Pushed alert"
        assert body["priority"] == "high"
        assert body["id"] == resp.json()["id"]

        # ...and the SSE broadcast still fired.
        event = queue.get_nowait()
        assert event["event"] == "notification"
    finally:
        broker.unsubscribe(test_user.id, queue)


@pytest.mark.asyncio
async def test_create_notification_noop_without_endpoints(
    client: AsyncClient,
    test_user: User,
    test_token: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_http(*args: object, **kwargs: object) -> None:
        raise AssertionError("no endpoints registered must not touch the network")

    monkeypatch.setattr(push.httpx, "AsyncClient", no_http)

    resp = await client.post(
        "/api/notifications/",
        json={"title": "No push targets"},
        headers={"Authorization": f"Bearer {TEST_TOKEN_PLAINTEXT}"},
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_notification_creation_survives_push_failure(
    client: AsyncClient,
    test_user: User,
    test_token: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # send_to_user swallows network errors internally; the route additionally guards against a
    # DB-layer failure (endpoint lookup/prune) so creation always survives.
    async def boom(db: AsyncSession, user_id: str, notification: Notification) -> None:
        raise SQLAlchemyError("push db exploded")

    monkeypatch.setattr(push, "send_to_user", boom)

    resp = await client.post(
        "/api/notifications/",
        json={"title": "Still created"},
        headers={"Authorization": f"Bearer {TEST_TOKEN_PLAINTEXT}"},
    )
    assert resp.status_code == 201


# --- send_to_user behaviour ---


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.record: list[dict[str, object]] = []

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def post(self, url: str, json: dict[str, object] | None = None) -> _FakeResponse:
        self.record.append({"url": url, "json": json})
        return self._response


async def _persisted_notification(db: AsyncSession, user_id: str) -> Notification:
    notification = Notification(
        id=str(uuid.uuid4()),
        user_id=user_id,
        title="Hello",
        message="Body text",
        priority=Priority.high,
        source="ci",
    )
    db.add(notification)
    await db.commit()
    await db.refresh(notification)
    return notification


@pytest.mark.asyncio
async def test_send_to_user_noops_without_endpoints(
    db: AsyncSession, test_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_http(*args: object, **kwargs: object) -> None:
        raise AssertionError("no endpoints must not touch the network")

    monkeypatch.setattr(push.httpx, "AsyncClient", no_http)

    notification = await _persisted_notification(db, test_user.id)
    await push.send_to_user(db, test_user.id, notification)  # no error, no network call


@pytest.mark.asyncio
async def test_send_to_user_posts_notification_json(
    db: AsyncSession, test_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.add(PushDevice(user_id=test_user.id, endpoint=ENDPOINT))
    await db.commit()

    fake = _FakeAsyncClient(_FakeResponse(200))
    monkeypatch.setattr(push.httpx, "AsyncClient", lambda **kwargs: fake)

    notification = await _persisted_notification(db, test_user.id)
    await push.send_to_user(db, test_user.id, notification)

    assert len(fake.record) == 1
    assert fake.record[0]["url"] == ENDPOINT
    body = fake.record[0]["json"]
    assert isinstance(body, dict)
    assert body["title"] == "Hello"
    assert body["message"] == "Body text"
    assert body["priority"] == "high"
    assert body["source"] == "ci"

    # The endpoint is kept on a successful (200) delivery.
    rows = (await db.execute(select(PushDevice))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_send_to_user_prunes_gone_endpoint(
    db: AsyncSession, test_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.add(PushDevice(user_id=test_user.id, endpoint=ENDPOINT))
    await db.commit()

    fake = _FakeAsyncClient(_FakeResponse(410))
    monkeypatch.setattr(push.httpx, "AsyncClient", lambda **kwargs: fake)

    notification = await _persisted_notification(db, test_user.id)
    await push.send_to_user(db, test_user.id, notification)

    # 410 Gone marks the endpoint permanently dead, so its row is pruned.
    rows = (await db.execute(select(PushDevice))).scalars().all()
    assert rows == []
