import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_token, lookup_hash
from app.models import ClientToken, User


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
    assert data["scope"] == "write"  # default
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


# --- Token scopes ---


async def _make_token(client: AsyncClient, session_cookie: str, scope: str) -> str:
    resp = await client.post(
        "/api/tokens/",
        json={"name": f"{scope} token", "scope": scope},
        cookies={"session": session_cookie},
    )
    assert resp.status_code == 201
    assert resp.json()["scope"] == scope
    return resp.json()["token"]


async def _make_notification(client: AsyncClient, session_cookie: str) -> str:
    resp = await client.post(
        "/api/notifications/",
        json={"title": "seed"},
        cookies={"session": session_cookie},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_write_scope_can_create_but_not_read_or_delete(
    client: AsyncClient, test_user: User, session_cookie: str
) -> None:
    token = await _make_token(client, session_cookie, "write")
    note_id = await _make_notification(client, session_cookie)
    h = {"Authorization": f"Bearer {token}"}

    assert (
        await client.post("/api/notifications/", json={"title": "x"}, headers=h)
    ).status_code == 201
    assert (await client.get("/api/notifications/", headers=h)).status_code == 403
    assert (await client.delete(f"/api/notifications/{note_id}", headers=h)).status_code == 403


@pytest.mark.asyncio
async def test_read_scope_can_read_and_patch_but_not_write_or_delete(
    client: AsyncClient, test_user: User, session_cookie: str
) -> None:
    token = await _make_token(client, session_cookie, "read")
    note_id = await _make_notification(client, session_cookie)
    h = {"Authorization": f"Bearer {token}"}

    assert (await client.get("/api/notifications/", headers=h)).status_code == 200
    assert (
        await client.patch(f"/api/notifications/{note_id}", json={"status": "read"}, headers=h)
    ).status_code == 200
    assert (
        await client.post("/api/notifications/", json={"title": "x"}, headers=h)
    ).status_code == 403
    assert (await client.delete(f"/api/notifications/{note_id}", headers=h)).status_code == 403


@pytest.mark.asyncio
async def test_delete_scope_can_delete_only(
    client: AsyncClient, test_user: User, session_cookie: str
) -> None:
    token = await _make_token(client, session_cookie, "delete")
    note_id = await _make_notification(client, session_cookie)
    h = {"Authorization": f"Bearer {token}"}

    assert (await client.get("/api/notifications/", headers=h)).status_code == 403
    assert (
        await client.post("/api/notifications/", json={"title": "x"}, headers=h)
    ).status_code == 403
    assert (await client.delete(f"/api/notifications/{note_id}", headers=h)).status_code == 204


@pytest.mark.asyncio
async def test_full_scope_can_do_everything(
    client: AsyncClient, test_user: User, session_cookie: str
) -> None:
    token = await _make_token(client, session_cookie, "full")
    note_id = await _make_notification(client, session_cookie)
    h = {"Authorization": f"Bearer {token}"}

    assert (await client.get("/api/notifications/", headers=h)).status_code == 200
    assert (
        await client.post("/api/notifications/", json={"title": "x"}, headers=h)
    ).status_code == 201
    assert (
        await client.patch(f"/api/notifications/{note_id}", json={"status": "read"}, headers=h)
    ).status_code == 200
    assert (await client.delete(f"/api/notifications/{note_id}", headers=h)).status_code == 204


# --- Management key ---


@pytest.mark.asyncio
async def test_management_key_generate_and_singleton(
    client: AsyncClient, test_user: User, session_cookie: str
) -> None:
    cookies = {"session": session_cookie}

    # None exists initially
    status = await client.get("/api/tokens/management-key", cookies=cookies)
    assert status.status_code == 200
    assert status.json()["exists"] is False

    resp = await client.post("/api/tokens/management-key", cookies=cookies)
    assert resp.status_code == 201
    assert len(resp.json()["key"]) > 20

    # Only one allowed
    dup = await client.post("/api/tokens/management-key", cookies=cookies)
    assert dup.status_code == 409

    status = await client.get("/api/tokens/management-key", cookies=cookies)
    assert status.json()["exists"] is True


@pytest.mark.asyncio
async def test_management_key_can_manage_tokens(
    client: AsyncClient, test_user: User, session_cookie: str
) -> None:
    mgmt = (
        await client.post("/api/tokens/management-key", cookies={"session": session_cookie})
    ).json()["key"]
    h = {"Authorization": f"Bearer {mgmt}"}

    # Create a token programmatically
    created = await client.post("/api/tokens/", json={"name": "prog", "scope": "read"}, headers=h)
    assert created.status_code == 201
    assert created.json()["scope"] == "read"
    token_id = created.json()["id"]

    # List
    listed = await client.get("/api/tokens/", headers=h)
    assert listed.status_code == 200
    assert any(t["id"] == token_id for t in listed.json())

    # Revoke
    assert (await client.delete(f"/api/tokens/{token_id}", headers=h)).status_code == 204


@pytest.mark.asyncio
async def test_management_key_cannot_send_notifications(
    client: AsyncClient, test_user: User, session_cookie: str
) -> None:
    mgmt = (
        await client.post("/api/tokens/management-key", cookies={"session": session_cookie})
    ).json()["key"]
    h = {"Authorization": f"Bearer {mgmt}"}

    # A management key is not a client token — it can't authenticate the notifications API
    resp = await client.post("/api/notifications/", json={"title": "nope"}, headers=h)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_management_key_revoke(
    client: AsyncClient, test_user: User, session_cookie: str
) -> None:
    cookies = {"session": session_cookie}
    await client.post("/api/tokens/management-key", cookies=cookies)

    assert (await client.delete("/api/tokens/management-key", cookies=cookies)).status_code == 204
    assert (await client.delete("/api/tokens/management-key", cookies=cookies)).status_code == 404
    assert (await client.get("/api/tokens/management-key", cookies=cookies)).json()[
        "exists"
    ] is False


# --- Lookup-hash fast path (O(1) auth) ---


@pytest.mark.asyncio
async def test_created_token_has_lookup_hash(
    client: AsyncClient, test_user: User, session_cookie: str, db: AsyncSession
) -> None:
    resp = await client.post(
        "/api/tokens/",
        json={"name": "Fast"},
        cookies={"session": session_cookie},
    )
    assert resp.status_code == 201
    plaintext = resp.json()["token"]
    token_id = resp.json()["id"]

    row = (await db.execute(select(ClientToken).where(ClientToken.id == token_id))).scalar_one()
    assert row.token_lookup == lookup_hash(plaintext)


@pytest.mark.asyncio
async def test_legacy_token_without_lookup_authenticates_and_backfills(
    client: AsyncClient, test_user: User, db: AsyncSession
) -> None:
    # Simulate a token created before token_lookup existed: bcrypt hash only.
    plaintext = "legacy-plaintext-token-000000000000"
    legacy = ClientToken(
        user_id=test_user.id,
        token_hash=hash_token(plaintext),
        token_lookup=None,
        name="Legacy",
    )
    db.add(legacy)
    await db.commit()

    # Authenticates via the bcrypt fallback (default scope is write → can POST).
    resp = await client.post(
        "/api/notifications/",
        json={"title": "from legacy"},
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert resp.status_code == 201

    # ...and the lookup hash is backfilled, so future auth is O(1).
    await db.refresh(legacy)
    assert legacy.token_lookup == lookup_hash(plaintext)
