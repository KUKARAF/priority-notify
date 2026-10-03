"""Firebase Cloud Messaging (HTTP v1) push delivery.

Data-only messages are sent to every device a user has registered (see PushDevice). The
Android client builds the user-facing notification from the data payload, which keeps
delivery behaviour consistent whether the app is foregrounded, backgrounded or killed.

Delivery is best-effort: `send_to_user` never raises for network or FCM errors — it logs
and moves on — so an FCM outage can't break notification creation or the SSE broadcast.
Push is a no-op unless both FCM settings are configured (see Settings.push_enabled).
"""

import asyncio

import google.auth.exceptions
import google.auth.transport
import httpx
import structlog
from google.oauth2 import service_account
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import Notification, PushDevice

log = structlog.get_logger()

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
_FCM_ENDPOINT = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"

# Service-account credentials are cached across requests and refreshed in place when the
# minted OAuth token nears expiry (google-auth tracks expiry on the Credentials object).
_credentials: service_account.Credentials | None = None


class _HttpxResponse(google.auth.transport.Response):
    """Adapt an httpx.Response to the google-auth transport Response interface."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    @property
    def status(self) -> int:
        return self._response.status_code

    @property
    def headers(self) -> httpx.Headers:
        return self._response.headers

    @property
    def data(self) -> bytes:
        return self._response.content


class _HttpxRequest(google.auth.transport.Request):
    """A google-auth transport backed by httpx, so token minting reuses the project's one
    HTTP client (google-auth's default transport would require the `requests` library).

    Called synchronously by google-auth; callers run it inside ``asyncio.to_thread``.
    """

    def __call__(
        self,
        url: str,
        method: str = "GET",
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        **kwargs: object,
    ) -> _HttpxResponse:
        response = httpx.request(method, url, content=body, headers=headers, timeout=timeout)
        return _HttpxResponse(response)


def _load_credentials(path: str) -> service_account.Credentials:
    creds: service_account.Credentials = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
        path, scopes=[FCM_SCOPE]
    )
    return creds


async def _get_access_token(settings: Settings) -> str:
    """Mint (or refresh) an OAuth access token for the FCM scope.

    The blocking google-auth file read and token refresh run in a worker thread so the
    event loop is never stalled.
    """
    global _credentials
    if _credentials is None:
        _credentials = await asyncio.to_thread(_load_credentials, settings.FCM_SERVICE_ACCOUNT_FILE)
    if not _credentials.valid:
        await asyncio.to_thread(_credentials.refresh, _HttpxRequest())
    token = _credentials.token
    if not token:
        raise RuntimeError("FCM credentials produced no access token")
    return str(token)


def _data_payload(notification: Notification) -> dict[str, str]:
    """FCM data fields must all be strings; absent values become empty strings."""
    return {
        "id": notification.id,
        "title": notification.title,
        "body": notification.message or "",
        "priority": notification.priority.value,
        "source": notification.source or "",
        "icon": notification.notification_icon or "",
    }


def _is_stale(response: httpx.Response) -> bool:
    """Whether FCM reports this token as permanently invalid (should be pruned)."""
    if response.status_code == 404:
        return True
    text = response.text
    return "UNREGISTERED" in text or "InvalidArgument" in text


async def send_to_user(db: AsyncSession, user_id: str, notification: Notification) -> None:
    """Deliver a data-only push to every device registered by the user.

    No-op when push is disabled or the user has no registered devices. Tokens the platform
    reports as gone (HTTP 404 / UNREGISTERED / InvalidArgument) are pruned. Never raises.
    """
    settings = get_settings()
    if not settings.push_enabled:
        return

    try:
        result = await db.execute(select(PushDevice).where(PushDevice.user_id == user_id))
        devices = list(result.scalars().all())
    except SQLAlchemyError:
        log.warning("fcm_device_lookup_failed", user_id=user_id, exc_info=True)
        return
    if not devices:
        return

    try:
        access_token = await _get_access_token(settings)
    except (
        google.auth.exceptions.GoogleAuthError,
        OSError,
        ValueError,
        RuntimeError,
        httpx.HTTPError,
    ):
        log.warning("fcm_token_mint_failed", user_id=user_id, exc_info=True)
        return

    url = _FCM_ENDPOINT.format(project_id=settings.FCM_PROJECT_ID)
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    data = _data_payload(notification)

    stale: list[PushDevice] = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        for device in devices:
            body = {
                "message": {
                    "token": device.fcm_token,
                    "data": data,
                    "android": {"priority": "high"},
                }
            }
            try:
                response = await client.post(url, headers=headers, json=body)
            except httpx.HTTPError:
                log.warning("fcm_send_failed", device_id=device.id, exc_info=True)
                continue

            if response.status_code == 200:
                continue
            if _is_stale(response):
                stale.append(device)
            else:
                log.warning(
                    "fcm_send_error",
                    device_id=device.id,
                    status=response.status_code,
                    body=response.text,
                )

    if stale:
        try:
            for device in stale:
                await db.delete(device)
            await db.commit()
        except SQLAlchemyError:
            log.warning("fcm_prune_failed", user_id=user_id, exc_info=True)
        else:
            log.info("fcm_pruned_stale_tokens", user_id=user_id, count=len(stale))
