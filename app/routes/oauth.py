import secrets
from datetime import datetime
from urllib.parse import urlencode

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import FormData

from app.auth import get_current_user_from_session, lookup_hash, require_session
from app.config import Settings, get_settings
from app.database import get_db
from app.models import OAuthAuthorizationCode, OAuthGrant, OAuthToken, OAuthTokenKind, User
from app.oauth import (
    AUTHORIZATION_CODE_TTL,
    SCOPES,
    InvalidClientError,
    delete_grant,
    find_token,
    format_scope,
    get_client_metadata,
    issue_tokens,
    parse_scope,
    redirect_uri_allowed,
    resource_url,
    utcnow,
    verify_pkce,
    with_query,
)

log = structlog.get_logger()
router = APIRouter(tags=["oauth"])
templates = Jinja2Templates(directory="app/templates")

CONSENT_MAX_AGE = 600  # seconds the consent form stays valid
_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
# The consent page must never be framed, or a hostile page could clickjack "Allow".
_CONSENT_HEADERS = {
    **_NO_STORE,
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
}


def require_oauth_server(settings: Settings = Depends(get_settings)) -> Settings:
    if not settings.OAUTH_SERVER_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")
    return settings


def require_mcp(settings: Settings = Depends(get_settings)) -> Settings:
    if not settings.MCP_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")
    return settings


def _consent_serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.SECRET_KEY, salt="oauth-consent")


def _oauth_error(error: str, description: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=status_code,
        headers=_NO_STORE,
    )


def _error_page(request: Request, message: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "oauth_error.html",
        {"message": message},
        status_code=400,
        headers=_CONSENT_HEADERS,
    )


# --- Discovery ---


@router.get("/.well-known/oauth-authorization-server")
async def authorization_server_metadata(
    settings: Settings = Depends(require_oauth_server),
) -> JSONResponse:
    base = settings.public_url
    return JSONResponse(
        {
            "issuer": base,
            "authorization_endpoint": f"{base}/oauth/authorize",
            "token_endpoint": f"{base}/oauth/token",
            "revocation_endpoint": f"{base}/oauth/revoke",
            "scopes_supported": list(SCOPES),
            "response_types_supported": ["code"],
            "response_modes_supported": ["query"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "revocation_endpoint_auth_methods_supported": ["none"],
            "authorization_response_iss_parameter_supported": True,
            "client_id_metadata_document_supported": True,
        }
    )


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/api/mcp")
async def protected_resource_metadata(settings: Settings = Depends(require_mcp)) -> JSONResponse:
    metadata: dict[str, object] = {
        "resource": resource_url(settings),
        "resource_name": "priority-notify",
        "scopes_supported": list(SCOPES),
        "bearer_methods_supported": ["header"],
    }
    if settings.OAUTH_SERVER_ENABLED:
        metadata["authorization_servers"] = [settings.public_url]
    return JSONResponse(metadata)


# --- Authorization endpoint ---


@router.get("/oauth/authorize", response_class=HTMLResponse)
async def authorize(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(require_oauth_server),
) -> Response:
    params = request.query_params
    client_id = params.get("client_id", "")
    redirect_uri = params.get("redirect_uri", "")
    state = params.get("state")

    # Until client and redirect_uri are verified, errors are shown here rather than
    # redirected, so this endpoint can't be used to bounce users to arbitrary URLs.
    if not client_id or not redirect_uri:
        return _error_page(request, "The request is missing client_id or redirect_uri.")
    try:
        client = await get_client_metadata(client_id, settings)
    except InvalidClientError as exc:
        log.warning("oauth_invalid_client", client_id=client_id, reason=str(exc))
        return _error_page(request, f"This app can't sign in here: {exc}.")
    if not redirect_uri_allowed(redirect_uri, client.redirect_uris):
        return _error_page(request, "The redirect_uri is not registered for this app.")

    def fail(error: str, description: str) -> RedirectResponse:
        out = {"error": error, "error_description": description, "iss": settings.public_url}
        if state is not None:
            out["state"] = state
        return RedirectResponse(with_query(redirect_uri, out), status_code=302)

    if params.get("response_type") != "code":
        return fail("unsupported_response_type", "Only response_type=code is supported")
    code_challenge = params.get("code_challenge", "")
    if not code_challenge or params.get("code_challenge_method") != "S256":
        return fail("invalid_request", "PKCE with code_challenge_method=S256 is required")

    user = await get_current_user_from_session(request, db, settings)
    if user is None:
        next_path = f"{request.url.path}?{request.url.query}"
        return RedirectResponse(f"/auth/login?{urlencode({'next': next_path})}", status_code=302)

    requested = parse_scope(params.get("scope"))
    consent_request = _consent_serializer(settings).dumps(
        {
            "uid": user.id,
            "client_id": client.client_id,
            "client_name": client.client_name,
            "client_uri": client.client_uri,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "scope": requested,
        }
    )
    return templates.TemplateResponse(
        request,
        "oauth_consent.html",
        {
            "user": user,
            "client": client,
            "scopes": [(s, SCOPES[s]) for s in requested],
            "consent_request": consent_request,
        },
        headers=_CONSENT_HEADERS,
    )


@router.post("/oauth/authorize")
async def authorize_decision(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(require_oauth_server),
) -> Response:
    user = await get_current_user_from_session(request, db, settings)
    if user is None:
        return _error_page(request, "Your session expired. Start the connection again.")

    form = await request.form()
    try:
        req = _consent_serializer(settings).loads(
            str(form.get("consent_request", "")), max_age=CONSENT_MAX_AGE
        )
    except SignatureExpired:
        return _error_page(request, "This consent screen expired. Start the connection again.")
    except BadSignature:
        return _error_page(request, "Invalid consent request.")
    if req["uid"] != user.id:
        return _error_page(request, "This consent screen belongs to a different account.")

    redirect_uri: str = req["redirect_uri"]
    base_params = {"iss": settings.public_url}
    if req["state"] is not None:
        base_params["state"] = req["state"]

    # Only scopes that were both requested and left ticked by the user.
    granted = [s for s in req["scope"] if s in form.getlist("scope")]
    if form.get("action") != "allow" or not granted:
        log.info("oauth_consent_denied", user_id=user.id, client_id=req["client_id"])
        return RedirectResponse(
            with_query(redirect_uri, {**base_params, "error": "access_denied"}), status_code=302
        )

    scope = format_scope(granted)
    result = await db.execute(
        select(OAuthGrant).where(
            OAuthGrant.user_id == user.id, OAuthGrant.client_id == req["client_id"]
        )
    )
    grant = result.scalar_one_or_none()
    if grant is None:
        grant = OAuthGrant(user_id=user.id, client_id=req["client_id"])
        db.add(grant)
    grant.client_name = req["client_name"]
    grant.client_uri = req["client_uri"]
    grant.scope = scope
    await db.flush()

    code = secrets.token_urlsafe(32)
    db.add(
        OAuthAuthorizationCode(
            grant_id=grant.id,
            code_lookup=lookup_hash(code),
            redirect_uri=redirect_uri,
            code_challenge=req["code_challenge"],
            scope=scope,
            expires_at=utcnow() + AUTHORIZATION_CODE_TTL,
        )
    )
    await db.commit()

    log.info("oauth_consent_granted", user_id=user.id, client_id=req["client_id"], scope=scope)
    redirect = with_query(redirect_uri, {**base_params, "code": code})
    return RedirectResponse(redirect, status_code=302)


# --- Token endpoint ---


@router.post("/oauth/token")
async def token(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(require_oauth_server),
) -> JSONResponse:
    form = await request.form()
    grant_type = form.get("grant_type")
    client_id = str(form.get("client_id", ""))
    if not client_id:
        return _oauth_error("invalid_request", "client_id is required")

    if grant_type == "authorization_code":
        return await _exchange_code(db, form, client_id)
    if grant_type == "refresh_token":
        return await _refresh(db, form, client_id)
    return _oauth_error("unsupported_grant_type", f"Unsupported grant_type: {grant_type}")


async def _exchange_code(db: AsyncSession, form: FormData, client_id: str) -> JSONResponse:
    code_value = str(form.get("code", ""))
    result = await db.execute(
        select(OAuthAuthorizationCode).where(
            OAuthAuthorizationCode.code_lookup == lookup_hash(code_value)
        )
    )
    code = result.scalar_one_or_none()
    if code is None:
        return _oauth_error("invalid_grant", "Unknown or already used authorization code")

    # Single use: burn it before checking anything else, so a failed PKCE guess can't retry.
    await db.delete(code)
    await db.commit()

    grant = await db.get(OAuthGrant, code.grant_id)
    if grant is None or grant.client_id != client_id:
        return _oauth_error("invalid_grant", "Authorization code was issued to another client")
    if code.expires_at < utcnow():
        return _oauth_error("invalid_grant", "Authorization code expired")
    if str(form.get("redirect_uri", "")) != code.redirect_uri:
        return _oauth_error("invalid_grant", "redirect_uri does not match the authorization")
    if not verify_pkce(str(form.get("code_verifier", "")), code.code_challenge):
        return _oauth_error("invalid_grant", "PKCE verification failed")

    log.info("oauth_token_issued", grant_id=grant.id, user_id=grant.user_id)
    return JSONResponse(await issue_tokens(db, grant, code.scope), headers=_NO_STORE)


async def _refresh(db: AsyncSession, form: FormData, client_id: str) -> JSONResponse:
    old = await find_token(db, str(form.get("refresh_token", "")), OAuthTokenKind.refresh)
    if old is None:
        return _oauth_error("invalid_grant", "Unknown or expired refresh token")
    grant = await db.get(OAuthGrant, old.grant_id)
    if grant is None or grant.client_id != client_id:
        return _oauth_error("invalid_grant", "Refresh token was issued to another client")

    scope = old.scope
    if form.get("scope"):
        narrowed = set(str(form.get("scope")).split())
        if not narrowed <= set(old.scope.split()):
            return _oauth_error("invalid_scope", "Cannot widen scope on refresh")
        scope = format_scope(narrowed)

    # Rotate: public clients must not be able to replay a refresh token (OAuth 2.1 §4.3.1).
    await db.delete(old)
    return JSONResponse(await issue_tokens(db, grant, scope), headers=_NO_STORE)


@router.post("/oauth/revoke")
async def revoke(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: Settings = Depends(require_oauth_server),
) -> Response:
    form = await request.form()
    raw = str(form.get("token", ""))
    result = await db.execute(select(OAuthToken).where(OAuthToken.token_lookup == lookup_hash(raw)))
    tok = result.scalar_one_or_none()
    if tok is not None:
        grant = await db.get(OAuthGrant, tok.grant_id)
        client_id = form.get("client_id")
        if grant is not None and (client_id is None or client_id == grant.client_id):
            if tok.kind == OAuthTokenKind.refresh:
                # RFC 7009 §2.1: revoking a refresh token also drops the grant's access tokens.
                await delete_grant(db, grant)
            else:
                await db.delete(tok)
                await db.commit()
    # RFC 7009 §2.2: respond 200 whether or not the token was valid.
    return Response(status_code=200)


# --- Connected apps (session only) ---


class GrantResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    client_id: str
    client_name: str
    client_uri: str | None
    scope: str
    created_at: datetime
    last_used_at: datetime | None


@router.get("/api/oauth/grants", response_model=list[GrantResponse])
async def list_grants(
    user: User = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> list[OAuthGrant]:
    result = await db.execute(
        select(OAuthGrant)
        .where(OAuthGrant.user_id == user.id)
        .order_by(OAuthGrant.created_at.desc())
    )
    return list(result.scalars().all())


@router.delete("/api/oauth/grants/{grant_id}", status_code=204)
async def revoke_grant(
    grant_id: str,
    user: User = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(
        select(OAuthGrant).where(OAuthGrant.id == grant_id, OAuthGrant.user_id == user.id)
    )
    grant = result.scalar_one_or_none()
    if grant is None:
        raise HTTPException(status_code=404, detail="Authorized app not found")
    await delete_grant(db, grant)
    log.info("oauth_grant_revoked", grant_id=grant_id, user_id=user.id)
