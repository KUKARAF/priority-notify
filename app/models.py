import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Priority(enum.StrEnum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class Status(enum.StrEnum):
    unread = "unread"
    read = "read"
    archived = "archived"


class DeviceType(enum.StrEnum):
    android = "android"
    gnome = "gnome"
    other = "other"


class TokenScope(enum.StrEnum):
    write = "write"  # create, not delete
    read = "read"  # read + mark-as-read
    delete = "delete"  # delete only
    full = "full"  # everything (not recommended)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    sub: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_login_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    notifications: Mapped[list["Notification"]] = relationship(back_populates="user")
    tokens: Mapped[list["ClientToken"]] = relationship(back_populates="user")
    push_devices: Mapped[list["PushDevice"]] = relationship(back_populates="user")


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notification_user_created", "user_id", "created_at"),
        Index("ix_notification_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(500))
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[Priority] = mapped_column(Enum(Priority), default=Priority.medium)
    status: Mapped[Status] = mapped_column(Enum(Status), default=Status.unread)
    source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notification_icon: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    metadata_: Mapped[dict | None] = mapped_column(  # type: ignore[type-arg]
        "metadata", JSON, nullable=True
    )

    user: Mapped["User"] = relationship(back_populates="notifications")


class ClientToken(Base):
    __tablename__ = "client_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    # SHA-256 of the raw token for O(1) indexed lookup (see auth.lookup_hash).
    # Nullable so tokens created before this column can be backfilled on first use.
    token_lookup: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, nullable=True
    )
    name: Mapped[str] = mapped_column(String(255))
    device_type: Mapped[DeviceType] = mapped_column(Enum(DeviceType), default=DeviceType.other)
    scope: Mapped[TokenScope] = mapped_column(
        Enum(TokenScope), default=TokenScope.write, server_default="write"
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(back_populates="tokens")


class ManagementKey(Base):
    __tablename__ = "management_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), unique=True, index=True
    )
    key_hash: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    # SHA-256 of the raw key for O(1) indexed lookup (see auth.lookup_hash).
    key_lookup: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship()


class PushDevice(Base):
    """A device registered to receive FCM push for a user.

    One row per FCM registration token; a user may have several (phone, tablet, ...).
    Stale tokens are pruned by the FCM sender when the platform reports them gone.
    """

    __tablename__ = "push_devices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    fcm_token: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    device_type: Mapped[DeviceType] = mapped_column(Enum(DeviceType), default=DeviceType.android)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(back_populates="push_devices")


class OAuthClient(Base):
    """A public OAuth client registered through Dynamic Client Registration (RFC 7591).

    CIMD clients aren't stored: their client_id is a URL we fetch instead.
    """

    __tablename__ = "oauth_clients"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # the client_id
    client_name: Mapped[str] = mapped_column(String(255))
    client_uri: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    redirect_uris: Mapped[list[str]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class OAuthGrant(Base):
    """A user's authorization of one OAuth client (e.g. Claude) — one per user and client.

    Revoking the grant deletes every code and token issued under it.
    """

    __tablename__ = "oauth_grants"
    __table_args__ = (Index("ix_oauth_grant_user_client", "user_id", "client_id", unique=True),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    # For CIMD clients the client_id is the URL of their metadata document.
    client_id: Mapped[str] = mapped_column(String(2048))
    client_name: Mapped[str] = mapped_column(String(255))
    client_uri: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    scope: Mapped[str] = mapped_column(Text)  # space-separated, as last consented
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship()


class OAuthAuthorizationCode(Base):
    __tablename__ = "oauth_authorization_codes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    grant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("oauth_grants.id", ondelete="CASCADE"), index=True
    )
    code_lookup: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    redirect_uri: Mapped[str] = mapped_column(String(2048))
    code_challenge: Mapped[str] = mapped_column(String(128))
    scope: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class OAuthTokenKind(enum.StrEnum):
    access = "access"
    refresh = "refresh"


class OAuthToken(Base):
    __tablename__ = "oauth_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    grant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("oauth_grants.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[OAuthTokenKind] = mapped_column(Enum(OAuthTokenKind))
    # SHA-256 of the opaque token (see auth.lookup_hash); the plaintext is never stored.
    token_lookup: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    scope: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime)

    grant: Mapped["OAuthGrant"] = relationship()
