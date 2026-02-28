from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

from app.models import DeviceType, Priority, Status

T = TypeVar("T")


# --- Notifications ---


class NotificationCreate(BaseModel):
    title: str = Field(max_length=500)
    message: str | None = None
    priority: Priority = Priority.medium
    source: str | None = Field(default=None, max_length=255)
    metadata: dict | None = None


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
    created_at: datetime
    read_at: datetime | None
    metadata: dict | None = Field(default=None, alias="metadata_")


# --- Tokens ---


class TokenCreate(BaseModel):
    name: str = Field(max_length=255)
    device_type: DeviceType = DeviceType.other


class TokenResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    name: str
    device_type: DeviceType
    last_used_at: datetime | None
    created_at: datetime
    expires_at: datetime | None


class TokenCreatedResponse(TokenResponse):
    token: str  # plaintext, shown only once


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
