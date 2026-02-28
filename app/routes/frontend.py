from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_from_session
from app.config import Settings, get_settings
from app.database import get_db
from app.models import ClientToken, Notification, Priority, Status, User

router = APIRouter(tags=["frontend"])
templates = Jinja2Templates(directory="app/templates")


async def _get_optional_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User | None:
    return await get_current_user_from_session(request, db, settings)


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    status: str | None = None,
    priority: str | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(_get_optional_user),
) -> HTMLResponse:
    if not user:
        return templates.TemplateResponse(request, "login.html")

    query = select(Notification).where(Notification.user_id == user.id)
    count_query = (
        select(func.count()).select_from(Notification).where(Notification.user_id == user.id)
    )

    if status:
        query = query.where(Notification.status == Status(status))
        count_query = count_query.where(Notification.status == Status(status))
    if priority:
        query = query.where(Notification.priority == Priority(priority))
        count_query = count_query.where(Notification.priority == Priority(priority))

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(Notification.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    notifications = result.scalars().all()

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "notifications": notifications,
            "total": total,
            "limit": limit,
            "offset": offset,
            "filter_status": status,
            "filter_priority": priority,
            "active": "dashboard",
        },
    )


@router.get("/tokens", response_class=HTMLResponse)
async def tokens_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(_get_optional_user),
) -> HTMLResponse:
    if not user:
        return RedirectResponse("/auth/login")

    result = await db.execute(
        select(ClientToken)
        .where(ClientToken.user_id == user.id)
        .order_by(ClientToken.created_at.desc())
    )
    tokens = result.scalars().all()

    return templates.TemplateResponse(
        request,
        "tokens.html",
        {
            "user": user,
            "tokens": tokens,
            "active": "tokens",
        },
    )
