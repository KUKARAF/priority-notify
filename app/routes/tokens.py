import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    generate_api_token,
    hash_token,
    lookup_hash,
    require_session,
    require_session_or_management_key,
)
from app.database import get_db
from app.models import ClientToken, ManagementKey, User
from app.schemas import (
    ManagementKeyCreated,
    ManagementKeyStatus,
    TokenCreate,
    TokenCreatedResponse,
    TokenResponse,
)

log = structlog.get_logger()
router = APIRouter(prefix="/api/tokens", tags=["tokens"])


# --- Management key ---
# Declared before /{token_id} so DELETE /management-key isn't captured by the path param.


@router.post("/management-key", response_model=ManagementKeyCreated, status_code=201)
async def create_management_key(
    user: User = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> ManagementKeyCreated:
    existing = await db.execute(select(ManagementKey).where(ManagementKey.user_id == user.id))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409,
            detail="A management key already exists. Revoke it before generating a new one.",
        )

    plaintext = generate_api_token()
    key = ManagementKey(
        user_id=user.id,
        key_hash=hash_token(plaintext),
        key_lookup=lookup_hash(plaintext),
    )
    db.add(key)
    await db.commit()
    await db.refresh(key)

    log.info("management_key_created", user_id=user.id)
    return ManagementKeyCreated(created_at=key.created_at, key=plaintext)


@router.get("/management-key", response_model=ManagementKeyStatus)
async def get_management_key(
    user: User = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> ManagementKeyStatus:
    result = await db.execute(select(ManagementKey).where(ManagementKey.user_id == user.id))
    key = result.scalar_one_or_none()
    if key is None:
        return ManagementKeyStatus(exists=False)
    return ManagementKeyStatus(
        exists=True, created_at=key.created_at, last_used_at=key.last_used_at
    )


@router.delete("/management-key", status_code=204)
async def revoke_management_key(
    user: User = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(select(ManagementKey).where(ManagementKey.user_id == user.id))
    key = result.scalar_one_or_none()
    if key is None:
        raise HTTPException(status_code=404, detail="Management key not found")
    await db.delete(key)
    await db.commit()
    log.info("management_key_revoked", user_id=user.id)


# --- API tokens ---


@router.get("/", response_model=list[TokenResponse])
async def list_tokens(
    user: User = Depends(require_session_or_management_key),
    db: AsyncSession = Depends(get_db),
) -> list[TokenResponse]:
    result = await db.execute(
        select(ClientToken)
        .where(ClientToken.user_id == user.id)
        .order_by(ClientToken.created_at.desc())
    )
    return [TokenResponse.model_validate(t) for t in result.scalars().all()]


@router.post("/", response_model=TokenCreatedResponse, status_code=201)
async def create_token(
    payload: TokenCreate,
    user: User = Depends(require_session_or_management_key),
    db: AsyncSession = Depends(get_db),
) -> TokenCreatedResponse:
    plaintext = generate_api_token()
    hashed = hash_token(plaintext)

    token = ClientToken(
        user_id=user.id,
        token_hash=hashed,
        token_lookup=lookup_hash(plaintext),
        name=payload.name,
        device_type=payload.device_type,
        scope=payload.scope,
    )
    db.add(token)
    await db.commit()
    await db.refresh(token)

    log.info(
        "token_created",
        token_id=token.id,
        user_id=user.id,
        name=payload.name,
        scope=payload.scope,
    )

    response = TokenResponse.model_validate(token)
    return TokenCreatedResponse(**response.model_dump(), token=plaintext)


@router.delete("/{token_id}", status_code=204)
async def revoke_token(
    token_id: str,
    user: User = Depends(require_session_or_management_key),
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(
        select(ClientToken).where(ClientToken.id == token_id, ClientToken.user_id == user.id)
    )
    token = result.scalar_one_or_none()
    if not token:
        raise HTTPException(status_code=404, detail="Token not found")
    await db.delete(token)
    await db.commit()
    log.info("token_revoked", token_id=token_id, user_id=user.id)
