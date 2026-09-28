"""MCP server (Streamable HTTP transport, stateless, JSON responses) at /api/mcp.

Callers authenticate with a Bearer token: either an OAuth access token from the built-in
authorization server (interactive sign-in from Claude, ChatGPT, ...) or a regular API
token, whose scope maps onto the OAuth scopes (see oauth.API_TOKEN_SCOPES). Cookies are
deliberately ignored, so a browser session can't be ridden cross-site.
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import _authenticate_token, _bearer_token
from app.config import Settings
from app.database import get_db
from app.models import Notification, Priority, Status, User
from app.oauth import (
    ACCESS_TOKEN_PREFIX,
    API_TOKEN_SCOPES,
    SCOPES,
    authenticate_access_token,
    format_scope,
    resource_metadata_url,
)
from app.routes.notifications import create_notification_for, set_notification_status
from app.routes.oauth import require_mcp
from app.schemas import NotificationCreate, NotificationResponse

log = structlog.get_logger()
router = APIRouter(tags=["mcp"])

SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
SERVER_INFO = {
    "name": "priority-notify",
    "title": "Rafa's notifications (priority-notify)",
    "version": "0.1.0",
}
INSTRUCTIONS = (
    "priority-notify is Rafa's self-hosted notification system: Rafa's personal inbox for "
    "alerts from scripts, home-lab monitoring and CI, pushed to Rafa's phone and desktop. "
    "Use list_notifications to see what needs Rafa's attention (filter status=unread for "
    "the inbox), update_notification_status to mark items read or archived, and "
    "send_notification to alert Rafa on every device."
)


@dataclass
class Principal:
    user: User
    scopes: frozenset[str]
    via_oauth: bool


class InsufficientScopeError(Exception):
    def __init__(self, scope: str) -> None:
        self.scope = scope


class ToolInputError(Exception):
    pass


# --- Tools ---


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(_Args):
    pass


class ListNotificationsArgs(_Args):
    status: Status | None = Field(default=None, description="Only notifications in this state")
    priority: Priority | None = Field(default=None, description="Only this priority")
    source: str | None = Field(default=None, description="Only from this source, e.g. 'ci'")
    search: str | None = Field(
        default=None, description="Case-insensitive text to find in the title or message"
    )
    since: datetime | None = Field(default=None, description="Only created after this time")
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class NotificationIdArgs(_Args):
    notification_id: str


class UpdateStatusArgs(_Args):
    notification_id: str
    status: Status


class SendNotificationArgs(NotificationCreate):
    model_config = ConfigDict(extra="forbid")


ToolHandler = Callable[[AsyncSession, User, Any], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class Tool:
    name: str
    title: str
    description: str
    scope: str
    args: type[BaseModel]
    handler: ToolHandler
    read_only: bool = False
    destructive: bool = False

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": _inline_refs(self.args.model_json_schema()),
            "annotations": {
                "title": self.title,
                "readOnlyHint": self.read_only,
                "destructiveHint": self.destructive,
                "idempotentHint": self.read_only or self.destructive,
                "openWorldHint": False,
            },
        }


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve pydantic's `$defs`/`$ref` so clients with naive schema support cope."""
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(defs[node["$ref"].rsplit("/", 1)[-1]])
            return {k: resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)  # type: ignore[no-any-return]


def _dump(n: Notification) -> dict[str, Any]:
    return NotificationResponse.model_validate(n).model_dump(mode="json")


async def _get_owned(db: AsyncSession, user: User, notification_id: str) -> Notification:
    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id, Notification.user_id == user.id
        )
    )
    notification = result.scalar_one_or_none()
    if notification is None:
        raise ToolInputError(f"Notification {notification_id} not found")
    return notification


async def _whoami(db: AsyncSession, user: User, args: NoArgs) -> dict[str, Any]:
    return {"id": user.id, "email": user.email, "name": user.name}


async def _list(db: AsyncSession, user: User, args: ListNotificationsArgs) -> dict[str, Any]:
    conditions = [Notification.user_id == user.id]
    if args.status:
        conditions.append(Notification.status == args.status)
    if args.priority:
        conditions.append(Notification.priority == args.priority)
    if args.source:
        conditions.append(Notification.source == args.source)
    if args.since:
        conditions.append(Notification.created_at > args.since)
    if args.search:
        pattern = f"%{args.search}%"
        conditions.append(
            or_(Notification.title.ilike(pattern), Notification.message.ilike(pattern))
        )

    total = (
        await db.execute(select(func.count()).select_from(Notification).where(*conditions))
    ).scalar() or 0
    result = await db.execute(
        select(Notification)
        .where(*conditions)
        .order_by(Notification.created_at.desc())
        .offset(args.offset)
        .limit(args.limit)
    )
    items = [_dump(n) for n in result.scalars().all()]
    return {"items": items, "total": total, "limit": args.limit, "offset": args.offset}


async def _get(db: AsyncSession, user: User, args: NotificationIdArgs) -> dict[str, Any]:
    return _dump(await _get_owned(db, user, args.notification_id))


async def _send(db: AsyncSession, user: User, args: SendNotificationArgs) -> dict[str, Any]:
    payload = NotificationCreate.model_validate(args.model_dump())
    response = await create_notification_for(db, user, payload)
    return response.model_dump(mode="json")


async def _update_status(db: AsyncSession, user: User, args: UpdateStatusArgs) -> dict[str, Any]:
    notification = await _get_owned(db, user, args.notification_id)
    response = await set_notification_status(db, notification, args.status)
    return response.model_dump(mode="json")


async def _delete(db: AsyncSession, user: User, args: NotificationIdArgs) -> dict[str, Any]:
    notification = await _get_owned(db, user, args.notification_id)
    await db.delete(notification)
    await db.commit()
    return {"deleted": args.notification_id}


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in [
        Tool(
            name="whoami",
            title="Who am I",
            description="Name and email of the priority-notify account this connection uses.",
            scope="user.profile:read",
            args=NoArgs,
            handler=_whoami,
            read_only=True,
        ),
        Tool(
            name="list_notifications",
            title="List notifications",
            description=(
                "List Rafa's notifications, newest first. Filter by status (unread, read, "
                "archived), priority (low, medium, high, critical), source, text or time. "
                "Returns items plus the total count matching the filters."
            ),
            scope="notifications:read",
            args=ListNotificationsArgs,
            handler=_list,
            read_only=True,
        ),
        Tool(
            name="get_notification",
            title="Get notification",
            description="Fetch one notification by id, including its metadata.",
            scope="notifications:read",
            args=NotificationIdArgs,
            handler=_get,
            read_only=True,
        ),
        Tool(
            name="send_notification",
            title="Send notification",
            description=(
                "Send a notification to Rafa. It is pushed immediately to Rafa's phone and "
                "desktop; 'critical' priority is meant to wake Rafa up, so use it "
                "only for genuine emergencies."
            ),
            scope="notifications:write",
            args=SendNotificationArgs,
            handler=_send,
        ),
        Tool(
            name="update_notification_status",
            title="Mark notification",
            description="Mark a notification as read, unread or archived.",
            scope="notifications:read",
            args=UpdateStatusArgs,
            handler=_update_status,
        ),
        Tool(
            name="delete_notification",
            title="Delete notification",
            description="Permanently delete a notification. Prefer archiving unless asked.",
            scope="notifications:delete",
            args=NotificationIdArgs,
            handler=_delete,
            destructive=True,
        ),
    ]
}


# --- Transport ---


async def _authenticate(request: Request, db: AsyncSession) -> Principal | None:
    raw = _bearer_token(request)
    if not raw:
        return None
    if raw.startswith(ACCESS_TOKEN_PREFIX):
        found = await authenticate_access_token(db, raw)
        if found is None:
            return None
        return Principal(user=found[0], scopes=found[1], via_oauth=True)

    ct = await _authenticate_token(request, db)
    if ct is None:
        return None
    user = await db.get(User, ct.user_id)
    if user is None:
        return None
    return Principal(user=user, scopes=API_TOKEN_SCOPES[ct.scope], via_oauth=False)


def _challenge(settings: Settings, status_code: int, error: str | None, scope: str) -> JSONResponse:
    # RFC 6750 §3.1: no `error` attribute when the request carried no credentials at all.
    attrs = [f'error="{error}"'] if error else []
    attrs += [f'scope="{scope}"', f'resource_metadata="{resource_metadata_url(settings)}"']
    return JSONResponse(
        {"error": error or "unauthorized", "error_description": "Authorization required"},
        status_code=status_code,
        headers={"WWW-Authenticate": "Bearer " + ", ".join(attrs)},
    )


def _result(req_id: Any, result: dict[str, Any]) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": result})


def _error(req_id: Any, code: int, message: str, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}},
        status_code=status_code,
    )


def _tool_result(data: dict[str, Any], is_error: bool = False) -> dict[str, Any]:
    text = data["error"] if is_error else json.dumps(data, ensure_ascii=False)
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": is_error}
    if not is_error:
        result["structuredContent"] = data
    return result


def _unauthorized(request: Request, settings: Settings) -> JSONResponse:
    error = "invalid_token" if _bearer_token(request) else None
    return _challenge(settings, 401, error, " ".join(SCOPES))


@router.post("/api/mcp")
@router.post("/api/mcp/", include_in_schema=False)
async def mcp_post(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(require_mcp),
) -> Response:
    principal = await _authenticate(request, db)
    if principal is None:
        return _unauthorized(request, settings)

    try:
        message = await request.json()
    except ValueError:
        return _error(None, -32700, "Parse error", status_code=400)
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, -32600, "Invalid Request: expected a single JSON-RPC 2.0 message", 400)

    # Notifications and responses from the client need no reply.
    if "id" not in message or "method" not in message:
        return Response(status_code=202)

    req_id = message["id"]
    method = message["method"]
    params = message.get("params") or {}

    if method == "initialize":
        version = params.get("protocolVersion")
        if version not in SUPPORTED_PROTOCOL_VERSIONS:
            version = SUPPORTED_PROTOCOL_VERSIONS[0]
        return _result(
            req_id,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": INSTRUCTIONS,
            },
        )
    if method == "ping":
        return _result(req_id, {})
    if method == "tools/list":
        return _result(req_id, {"tools": [t.definition() for t in TOOLS.values()]})
    if method == "tools/call":
        return await _call_tool(req_id, params, principal, db, settings)
    return _error(req_id, -32601, f"Method not found: {method}")


async def _call_tool(
    req_id: Any,
    params: dict[str, Any],
    principal: Principal,
    db: AsyncSession,
    settings: Settings,
) -> Response:
    tool = TOOLS.get(params.get("name", ""))
    if tool is None:
        return _error(req_id, -32602, f"Unknown tool: {params.get('name')}")

    if tool.scope not in principal.scopes:
        if principal.via_oauth:
            # Step-up: tell the client which scopes to re-authorize with (MCP scope challenge).
            return _challenge(
                settings, 403, "insufficient_scope", format_scope(principal.scopes | {tool.scope})
            )
        return _result(
            req_id,
            _tool_result(
                {"error": f"This API token lacks the '{tool.scope}' permission for {tool.name}."},
                is_error=True,
            ),
        )

    try:
        args = tool.args.model_validate(params.get("arguments") or {})
        data = await tool.handler(db, principal.user, args)
    except ValidationError as exc:
        return _result(req_id, _tool_result({"error": f"Invalid arguments: {exc}"}, True))
    except ToolInputError as exc:
        return _result(req_id, _tool_result({"error": str(exc)}, True))

    log.info("mcp_tool_call", tool=tool.name, user_id=principal.user.id)
    return _result(req_id, _tool_result(data))


@router.get("/api/mcp")
@router.delete("/api/mcp")
async def mcp_other(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(require_mcp),
) -> Response:
    if await _authenticate(request, db) is None:
        return _unauthorized(request, settings)
    # Stateless server: no standalone SSE stream and no sessions to terminate.
    return Response(status_code=405, headers={"Allow": "POST"})
