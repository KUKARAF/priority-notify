import secrets
from datetime import UTC, datetime

import bcrypt
import httpx
import structlog
from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from joserfc import jwt
from joserfc.jwk import KeySet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db
from app.models import ClientToken, User

log = structlog.get_logger()

SESSION_COOKIE = "session"
SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days


class SessionManager:
    def __init__(self, secret_key: str) -> None:
        self.serializer = URLSafeTimedSerializer(secret_key)

    def create_session(self, user_id: str) -> str:
        return self.serializer.dumps({"uid": user_id})

    def validate_session(self, token: str) -> str | None:
        try:
            data = self.serializer.loads(token, max_age=SESSION_MAX_AGE)
            return data["uid"]  # type: ignore[no-any-return]
        except (BadSignature, SignatureExpired, KeyError):
            return None


def get_session_manager(settings: Settings = Depends(get_settings)) -> SessionManager:
    return SessionManager(settings.SECRET_KEY)


# --- OIDC Helpers ---

_oidc_config_cache: dict[str, object] = {}
_jwks_cache: KeySet | None = None


async def get_oidc_config(settings: Settings) -> dict:  # type: ignore[type-arg]
    if "config" not in _oidc_config_cache:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{settings.AUTHENTIK_ISSUER_URL}/application/o/{settings.AUTHENTIK_CLIENT_ID}/.well-known/openid-configuration"
            )
            resp.raise_for_status()
            _oidc_config_cache["config"] = resp.json()
    return _oidc_config_cache["config"]  # type: ignore[return-value]


async def get_jwks(settings: Settings) -> KeySet:
    global _jwks_cache
    if _jwks_cache is None:
        oidc_config = await get_oidc_config(settings)
        async with httpx.AsyncClient() as client:
            resp = await client.get(oidc_config["jwks_uri"])
            resp.raise_for_status()
            _jwks_cache = KeySet.import_key_set(resp.json())
    return _jwks_cache


def build_authorization_url(oidc_config: dict, settings: Settings, state: str) -> str:  # type: ignore[type-arg]
    params = {
        "response_type": "code",
        "client_id": settings.AUTHENTIK_CLIENT_ID,
        "redirect_uri": settings.AUTHENTIK_REDIRECT_URI,
        "scope": "openid email profile",
        "state": state,
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{oidc_config['authorization_endpoint']}?{query}"


async def exchange_code_for_tokens(
    code: str,
    oidc_config: dict,
    settings: Settings,  # type: ignore[type-arg]
) -> dict:  # type: ignore[type-arg]
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            oidc_config["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.AUTHENTIK_REDIRECT_URI,
                "client_id": settings.AUTHENTIK_CLIENT_ID,
                "client_secret": settings.AUTHENTIK_CLIENT_SECRET,
            },
        )
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]


async def validate_id_token(id_token: str, settings: Settings) -> dict:  # type: ignore[type-arg]
    jwks = await get_jwks(settings)
    oidc_config = await get_oidc_config(settings)
    token = jwt.decode(id_token, jwks)
    claims = token.claims
    if claims.get("iss") != oidc_config["issuer"]:
        raise ValueError("Invalid issuer")
    if claims.get("aud") != settings.AUTHENTIK_CLIENT_ID:
        raise ValueError("Invalid audience")
    return claims  # type: ignore[no-any-return]


# --- User resolution dependencies ---


async def get_current_user_from_session(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User | None:
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        return None
    sm = SessionManager(settings.SECRET_KEY)
    user_id = sm.validate_session(cookie)
    if not user_id:
        return None
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_current_user_from_token(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User | None:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    raw_token = auth_header[7:]

    # Look up all tokens and bcrypt-verify (bcrypt hashes aren't searchable)
    result = await db.execute(select(ClientToken))
    tokens = result.scalars().all()
    for ct in tokens:
        if bcrypt.checkpw(raw_token.encode(), ct.token_hash.encode()):
            ct.last_used_at = datetime.now(UTC)
            await db.commit()
            user_result = await db.execute(select(User).where(User.id == ct.user_id))
            return user_result.scalar_one_or_none()
    return None


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    user = await get_current_user_from_session(request, db, settings)
    if user:
        return user
    user = await get_current_user_from_token(request, db)
    if user:
        return user
    raise HTTPException(status_code=401, detail="Not authenticated")


async def require_session(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    user = await get_current_user_from_session(request, db, settings)
    if not user:
        raise HTTPException(status_code=401, detail="Session required")
    return user


def generate_api_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return bcrypt.hashpw(token.encode(), bcrypt.gensalt()).decode()
