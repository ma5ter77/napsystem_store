"""NAP-118: service news (administrator announcements) and posts about news to the Telegram channel."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, created_at, uuid_pk
from .system import _in

POST_STATES = ("draft", "scheduled", "sending", "sent", "failed")


class NewsItem(Base):
    """An administrator's announcement (releases, new apps and products come from their own records)."""
    __tablename__ = "news_items"
    id: Mapped[uuid.UUID] = uuid_pk()
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at()


class TelegramPost(Base):
    __tablename__ = "telegram_posts"
    id: Mapped[uuid.UUID] = uuid_pk()
    source_key: Mapped[str] = mapped_column(String(120), index=True)     # news:<id> / release:<v> / app:<id> / product:<id>
    source_title: Mapped[str] = mapped_column(String(300), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    photo_url: Mapped[str | None] = mapped_column(String(1000))
    link_url: Mapped[str | None] = mapped_column(String(1000))
    language: Mapped[str] = mapped_column(String(5), default="ru")
    generated_by: Mapped[str] = mapped_column(String(16), default="template")   # model / template / manual
    status: Mapped[str] = mapped_column(String(16), default="draft")
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_id: Mapped[str | None] = mapped_column(String(40))
    message_url: Mapped[str | None] = mapped_column(String(300))
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_detail: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    sent_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at()
    __table_args__ = (CheckConstraint(_in("status", POST_STATES), name="status"),
                      Index("ix_telegram_posts_due", "status", "scheduled_at"))
