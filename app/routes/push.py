from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_scope
from app.database import get_db
from app.models import PushDevice, TokenScope, User
from app.schemas import PushRegister

log = structlog.get_logger()
router = APIRouter(prefix="/api/push", tags=["push"])


@router.post("/register")
async def register_endpoint(
    payload: PushRegister,
    user: User = Depends(require_scope(TokenScope.read)),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Register a UnifiedPush endpoint URL for the caller.

    Upserts by endpoint URL: re-registering an endpoint just refreshes its owner and
    last_seen_at rather than creating a duplicate, so a device re-announcing the same
    endpoint replaces its own prior registration. A user may hold several endpoints at once
    (one per device), and the sender fans out to all of them.
    """
    result = await db.execute(select(PushDevice).where(PushDevice.endpoint == payload.endpoint))
    device = result.scalar_one_or_none()
    now = datetime.now(UTC)
    if device is None:
        device = PushDevice(user_id=user.id, endpoint=payload.endpoint, last_seen_at=now)
        db.add(device)
    else:
        device.user_id = user.id
        device.last_seen_at = now

    await db.commit()
    log.info("push_endpoint_registered", device_id=device.id, user_id=user.id)
    return {"status": "registered"}
