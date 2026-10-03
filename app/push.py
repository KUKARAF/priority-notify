"""UnifiedPush delivery.

On every new notification, the server POSTs the notification JSON to each UnifiedPush
endpoint URL the user has registered (see PushDevice). The endpoint URL is itself the
delivery capability (e.g. an ntfy.sh topic URL), so the POST carries no auth header, no
Google credentials and needs no configuration — UnifiedPush requires none. The Android
client subscribes to a distributor, hands the server the resulting endpoint URL, and the
distributor turns the POSTed body into a notification.

Delivery is best-effort: `send_to_user` never raises for network errors — it logs and moves
on — so a distributor outage can't break notification creation or the SSE broadcast. An
endpoint the distributor reports as permanently gone (HTTP 404 / 410) is pruned.
"""

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification, PushDevice
from app.schemas import NotificationResponse

log = structlog.get_logger()

# A distributor reports a dead endpoint with 404 (topic/subscription gone) or 410 Gone.
_PRUNE_STATUSES = frozenset({404, 410})


async def send_to_user(db: AsyncSession, user_id: str, notification: Notification) -> None:
    """POST the notification JSON to every UnifiedPush endpoint the user has registered.

    No-op when the user has no registered endpoints. Endpoints the distributor reports as
    gone (HTTP 404 / 410) are pruned. Never raises.
    """
    try:
        result = await db.execute(select(PushDevice).where(PushDevice.user_id == user_id))
        devices = list(result.scalars().all())
    except SQLAlchemyError:
        log.warning("push_endpoint_lookup_failed", user_id=user_id, exc_info=True)
        return
    if not devices:
        return

    # The same payload the SSE broadcast carries, so clients see one consistent shape.
    payload = NotificationResponse.model_validate(notification).model_dump(mode="json")

    stale: list[PushDevice] = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        for device in devices:
            try:
                response = await client.post(device.endpoint, json=payload)
            except httpx.HTTPError:
                log.warning("push_send_failed", device_id=device.id, exc_info=True)
                continue

            if response.status_code in _PRUNE_STATUSES:
                stale.append(device)
            elif response.status_code >= 400:
                log.warning("push_send_error", device_id=device.id, status=response.status_code)

    if stale:
        try:
            for device in stale:
                await db.delete(device)
            await db.commit()
        except SQLAlchemyError:
            log.warning("push_prune_failed", user_id=user_id, exc_info=True)
        else:
            log.info("push_pruned_stale_endpoints", user_id=user_id, count=len(stale))
