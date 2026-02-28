import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import generate_api_token, hash_token, require_session
from app.database import get_db
from app.models import ClientToken, User
from app.schemas import TokenCreate, TokenCreatedResponse, TokenResponse

log = structlog.get_logger()
router = APIRouter(prefix="/api/tokens", tags=["tokens"])


@router.get("/", response_model=list[TokenResponse])
async def list_tokens(
    user: User = Depends(require_session),
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
    user: User = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> TokenCreatedResponse:
    plaintext = generate_api_token()
    hashed = hash_token(plaintext)

    token = ClientToken(
        user_id=user.id,
        token_hash=hashed,
        name=payload.name,
        device_type=payload.device_type,
    )
    db.add(token)
    await db.commit()
    await db.refresh(token)

    log.info("token_created", token_id=token.id, user_id=user.id, name=payload.name)

    response = TokenResponse.model_validate(token)
    return TokenCreatedResponse(**response.model_dump(), token=plaintext)


@router.delete("/{token_id}", status_code=204)
async def revoke_token(
    token_id: str,
    user: User = Depends(require_session),
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
