"""Users, roles, auth sessions, API keys, translations, audit, inbox/outbox, notifications."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base, created_at, uuid_pk

ROLES = ("user", "developer", "seller", "fulfillment", "moderator", "admin", "device")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str | None] = mapped_column(String(320), unique=True)  # NULL after anonymisation
    password_hash: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(120))
    locale: Mapped[str] = mapped_column(String(5), default="ru")
    payout_account: Mapped[str | None] = mapped_column(String(64))  # e.g. Stripe Connect account id
    created_at: Mapped[datetime] = created_at()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    roles: Mapped[list["RoleGrant"]] = relationship(back_populates="user", lazy="selectin",
                                                   foreign_keys="RoleGrant.user_id")
    __table_args__ = (CheckConstraint("locale IN ('ru','en')", name="locale"),)

    @property
    def role_names(self) -> set[str]:
        return {r.role for r in self.roles if r.revoked_at is None}


class RoleGrant(Base):
    """Permissions are granted separately per role; one account may hold several."""
    __tablename__ = "role_grants"
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(24))
    granted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at()
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user: Mapped[User] = relationship(back_populates="roles", foreign_keys=[user_id])
    __table_args__ = (CheckConstraint(_in("role", ROLES), name="role"),
                      Index("uq_role_active", "user_id", "role", unique=True,
                            postgresql_where="revoked_at IS NULL"))


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id: Mapped[uuid.UUID] = uuid_pk()
    key: Mapped[str] = mapped_column(String(128), index=True)  # sha256(email) — no plaintext
    success: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = created_at()


class ApiKey(Base):
    """Server-side developer keys. Only a hash is stored; scopes limit access."""
    __tablename__ = "api_keys"
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    prefix: Mapped[str] = mapped_column(String(16), unique=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String(40)))
    created_at: Mapped[datetime] = created_at()
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Translation(Base):
    """Separate content translations with publication status (missing is implied by absence)."""
    __tablename__ = "translations"
    id: Mapped[uuid.UUID] = uuid_pk()
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    locale: Mapped[str] = mapped_column(String(5))
    status: Mapped[str] = mapped_column(String(12), default="draft")
    data: Mapped[dict] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = created_at()
    __table_args__ = (UniqueConstraint("entity_type", "entity_id", "locale"),
                      CheckConstraint("status IN ('draft','published')", name="status"),
                      CheckConstraint("locale IN ('ru','en')", name="locale"),
                      Index("ix_translations_entity", "entity_type", "entity_id"))


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[uuid.UUID] = uuid_pk()
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    request_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at()


class WebhookInbox(Base):
    """Durably stored, unique external events. Processing is retried and deduplicated."""
    __tablename__ = "webhook_inbox"
    id: Mapped[uuid.UUID] = uuid_pk()
    source: Mapped[str] = mapped_column(String(32))  # mock_pay / stripe / easypost / mock_ship
    event_id: Mapped[str] = mapped_column(String(128))
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB)
    received_at: Mapped[datetime] = created_at()
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("source", "event_id"),)


class Outbox(Base):
    __tablename__ = "outbox"
    id: Mapped[uuid.UUID] = uuid_pk()
    topic: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB)
    dedupe_key: Mapped[str] = mapped_column(String(160), unique=True)
    created_at: Mapped[datetime] = created_at()
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)


class Notification(Base):
    """Rendered, localized system e-mails/notifications (delivered by the outbox dispatcher)."""
    __tablename__ = "notifications"
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    locale: Mapped[str] = mapped_column(String(5))
    template: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    dedupe_key: Mapped[str] = mapped_column(String(160), unique=True)
    created_at: Mapped[datetime] = created_at()
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Setting(Base):
    __tablename__ = "platform_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = created_at()


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String(12))  # app / hardware
    slug: Mapped[str] = mapped_column(String(64))
    names: Mapped[dict] = mapped_column(JSONB)  # {"ru": "...", "en": "..."} platform-owned
    position: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (UniqueConstraint("kind", "slug"), CheckConstraint("kind IN ('app','hardware')", name="kind"))
