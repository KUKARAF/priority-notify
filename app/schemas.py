from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

from app.models import DeviceType, Priority, Status, TokenScope

T = TypeVar("T")


# --- Notifications ---


class NotificationCreate(BaseModel):
    title: str = Field(max_length=500)
    message: str | None = None
    priority: Priority = Priority.medium
    source: str | None = Field(default=None, max_length=255)
    notification_icon: str | None = Field(default=None, max_length=64)
    metadata: dict | None = None  # type: ignore[type-arg]


class NotificationUpdate(BaseModel):
    status: Status | None = None


class NotificationResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    user_id: str
    title: str
    message: str | None
    priority: Priority
    status: Status
    source: str | None
    notification_icon: str | None
    created_at: datetime
    read_at: datetime | None
    metadata: dict | None = Field(default=None, alias="metadata_")  # type: ignore[type-arg]


# --- Tokens ---


class TokenCreate(BaseModel):
    name: str = Field(max_length=255)
    device_type: DeviceType = DeviceType.other
    scope: TokenScope = TokenScope.write


class TokenResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    name: str
    device_type: DeviceType
    scope: TokenScope
    last_used_at: datetime | None
    created_at: datetime
    expires_at: datetime | None


class TokenCreatedResponse(TokenResponse):
    token: str  # plaintext, shown only once


# --- Push devices ---


class PushDeviceCreate(BaseModel):
    fcm_token: str = Field(max_length=255)
    device_type: DeviceType = DeviceType.android
    label: str | None = Field(default=None, max_length=255)


class PushDeviceResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    device_type: DeviceType
    label: str | None
    created_at: datetime
    last_seen_at: datetime | None
    # The FCM token is a secret; never echo it back in full.


# --- Management key ---


class ManagementKeyStatus(BaseModel):
    model_config = {"from_attributes": True}

    exists: bool
    created_at: datetime | None = None
    last_used_at: datetime | None = None


class ManagementKeyCreated(BaseModel):
    created_at: datetime
    key: str  # plaintext, shown only once


# --- User ---


class UserResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    email: str
    name: str
    created_at: datetime
    last_login_at: datetime


# --- Pagination ---


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int
