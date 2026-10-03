from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_scope
from app.database import get_db
from app.models import PushDevice, TokenScope, User
from app.schemas import PushDeviceCreate, PushDeviceResponse

log = structlog.get_logger()
router = APIRouter(prefix="/api/push", tags=["push"])


@router.get("/devices", response_model=list[PushDeviceResponse])
async def list_devices(
    user: User = Depends(require_scope(TokenScope.read)),
    db: AsyncSession = Depends(get_db),
) -> list[PushDeviceResponse]:
    result = await db.execute(
        select(PushDevice)
        .where(PushDevice.user_id == user.id)
        .order_by(PushDevice.created_at.desc())
    )
    return [PushDeviceResponse.model_validate(d) for d in result.scalars().all()]


@router.post("/devices", response_model=PushDeviceResponse, status_code=201)
async def register_device(
    payload: PushDeviceCreate,
    user: User = Depends(require_scope(TokenScope.read)),
    db: AsyncSession = Depends(get_db),
) -> PushDeviceResponse:
    # Upsert on fcm_token: a device re-registering an existing token updates its row
    # (owner, type, label, last_seen) rather than creating a duplicate.
    result = await db.execute(select(PushDevice).where(PushDevice.fcm_token == payload.fcm_token))
    device = result.scalar_one_or_none()
    now = datetime.now(UTC)
    if device is None:
        device = PushDevice(
            user_id=user.id,
            fcm_token=payload.fcm_token,
            device_type=payload.device_type,
            label=payload.label,
            last_seen_at=now,
        )
        db.add(device)
    else:
        device.user_id = user.id
        device.device_type = payload.device_type
        device.label = payload.label
        device.last_seen_at = now

    await db.commit()
    await db.refresh(device)
    log.info("push_device_registered", device_id=device.id, user_id=user.id)
    return PushDeviceResponse.model_validate(device)


@router.delete("/devices/{device_id}", status_code=204)
async def delete_device(
    device_id: str,
    user: User = Depends(require_scope(TokenScope.read)),
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(
        select(PushDevice).where(PushDevice.id == device_id, PushDevice.user_id == user.id)
    )
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    await db.delete(device)
    await db.commit()
    log.info("push_device_deleted", device_id=device_id, user_id=user.id)
