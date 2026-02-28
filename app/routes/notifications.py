import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.auth import get_current_user
from app.database import get_db
from app.models import Notification, Priority, Status, User
from app.schemas import (
    NotificationCreate,
    NotificationResponse,
    NotificationUpdate,
    PaginatedResponse,
)
from app.sse import broker

log = structlog.get_logger()
router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("/", response_model=PaginatedResponse[NotificationResponse])
async def list_notifications(
    since: datetime | None = None,
    status: Status | None = None,
    priority: Priority | None = None,
    source: str | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[NotificationResponse]:
    query = select(Notification).where(Notification.user_id == user.id)
    count_query = (
        select(func.count()).select_from(Notification).where(Notification.user_id == user.id)
    )

    if since:
        query = query.where(Notification.created_at > since)
        count_query = count_query.where(Notification.created_at > since)
    if status:
        query = query.where(Notification.status == status)
        count_query = count_query.where(Notification.status == status)
    if priority:
        query = query.where(Notification.priority == priority)
        count_query = count_query.where(Notification.priority == priority)
    if source:
        query = query.where(Notification.source == source)
        count_query = count_query.where(Notification.source == source)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(Notification.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    items = [NotificationResponse.model_validate(n) for n in result.scalars().all()]

    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@router.post("/", response_model=NotificationResponse, status_code=201)
async def create_notification(
    payload: NotificationCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> NotificationResponse:
    notification = Notification(
        user_id=user.id,
        title=payload.title,
        message=payload.message,
        priority=payload.priority,
        source=payload.source,
        metadata_=payload.metadata,
    )
    db.add(notification)
    await db.commit()
    await db.refresh(notification)

    response = NotificationResponse.model_validate(notification)
    await broker.publish(user.id, "notification", response.model_dump(mode="json"))

    log.info("notification_created", id=notification.id, user_id=user.id, priority=payload.priority)
    return response


@router.get("/stream")
async def stream_notifications(
    request: Request,
    user: User = Depends(get_current_user),
) -> EventSourceResponse:
    queue = broker.subscribe(user.id)

    async def event_generator() -> AsyncGenerator[dict[str, str]]:
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield event
                except TimeoutError:
                    yield {"event": "ping", "data": ""}
        finally:
            broker.unsubscribe(user.id, queue)

    return EventSourceResponse(event_generator())


@router.get("/{notification_id}", response_model=NotificationResponse)
async def get_notification(
    notification_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NotificationResponse:
    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id, Notification.user_id == user.id
        )
    )
    notification = result.scalar_one_or_none()
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    return NotificationResponse.model_validate(notification)


@router.patch("/{notification_id}", response_model=NotificationResponse)
async def update_notification(
    notification_id: str,
    payload: NotificationUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NotificationResponse:
    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id, Notification.user_id == user.id
        )
    )
    notification = result.scalar_one_or_none()
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    if payload.status is not None:
        notification.status = payload.status
        if payload.status == Status.read and notification.read_at is None:
            notification.read_at = datetime.now(UTC)

    await db.commit()
    await db.refresh(notification)

    response = NotificationResponse.model_validate(notification)
    await broker.publish(
        user.id,
        "status_change",
        {"id": notification.id, "status": notification.status.value},
    )

    return response


@router.delete("/{notification_id}", status_code=204)
async def delete_notification(
    notification_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id, Notification.user_id == user.id
        )
    )
    notification = result.scalar_one_or_none()
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    await db.delete(notification)
    await db.commit()
