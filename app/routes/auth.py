import secrets
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    SESSION_COOKIE,
    SessionManager,
    build_authorization_url,
    exchange_code_for_tokens,
    get_current_user,
    get_oidc_config,
    validate_id_token,
)
from app.config import Settings, get_settings
from app.database import get_db
from app.models import User
from app.schemas import UserResponse

log = structlog.get_logger()
router = APIRouter()

# In-memory state store for OIDC (fine for single-process)
_pending_states: set[str] = set()


@router.get("/auth/login")
async def login(settings: Settings = Depends(get_settings)) -> RedirectResponse:
    state = secrets.token_urlsafe(32)
    _pending_states.add(state)
    oidc_config = await get_oidc_config(settings)
    url = build_authorization_url(oidc_config, settings, state)
    return RedirectResponse(url)


@router.get("/auth/callback")
async def callback(
    code: str,
    state: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    if state not in _pending_states:
        return RedirectResponse("/auth/login")
    _pending_states.discard(state)

    oidc_config = await get_oidc_config(settings)
    token_data = await exchange_code_for_tokens(code, oidc_config, settings)
    claims = await validate_id_token(token_data["id_token"], settings)

    sub = claims["sub"]
    email = claims.get("email", "")
    name = claims.get("name", claims.get("preferred_username", ""))

    result = await db.execute(select(User).where(User.sub == sub))
    user = result.scalar_one_or_none()

    if user:
        user.email = email
        user.name = name
        user.last_login_at = datetime.now(UTC)
    else:
        user = User(sub=sub, email=email, name=name)
        db.add(user)

    await db.commit()
    await db.refresh(user)

    sm = SessionManager(settings.SECRET_KEY)
    session_token = sm.create_session(user.id)

    response = RedirectResponse("/", status_code=302)
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
    )
    log.info("user_login", user_id=user.id, email=user.email)
    return response


@router.get("/auth/logout")
async def logout(settings: Settings = Depends(get_settings)) -> RedirectResponse:
    response = RedirectResponse("/", status_code=302)
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.get("/api/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)) -> User:
    return user
