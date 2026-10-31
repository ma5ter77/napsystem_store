"""Ratings and reviews (NAP-065): one rating/review per user per app, developer replies, helpful votes, reports."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base, created_at, uuid_pk
from ..db import created_at as _ts
from .system import _in

REVIEW_STATUSES = ("published", "pending", "hidden")


class Review(Base):
    """A 1-5 star rating, optionally with a title and text. The developer cannot edit, hide or delete it."""
    __tablename__ = "reviews"
    id: Mapped[uuid.UUID] = uuid_pk()
    app_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("apps.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_versions.id"))  # version the author was using
    version_label: Mapped[str] = mapped_column(String(32))
    rating: Mapped[int] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(String(100))
    body: Mapped[str | None] = mapped_column(Text)
    lang: Mapped[str] = mapped_column(String(8), default="ru")
    status: Mapped[str] = mapped_column(String(12), default="published")
    moderation_note: Mapped[str | None] = mapped_column(String(300))
    helpful_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = _ts()
    reply: Mapped["ReviewReply | None"] = relationship(back_populates="review", uselist=False,
                                                       cascade="all, delete-orphan")
    __table_args__ = (
        UniqueConstraint("app_id", "user_id"),
        CheckConstraint("rating BETWEEN 1 AND 5", name="rating"),
        CheckConstraint(_in("status", REVIEW_STATUSES), name="status"),
        CheckConstraint("char_length(body) <= 4000", name="body_len"),
        Index("ix_reviews_app_status_created", "app_id", "status", "created_at"),
    )


class ReviewReply(Base):
    """One public developer reply per review, editable by the app owner."""
    __tablename__ = "review_replies"
    id: Mapped[uuid.UUID] = uuid_pk()
    review_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reviews.id", ondelete="CASCADE"), unique=True)
    developer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="published")
    moderation_note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = _ts()
    review: Mapped[Review] = relationship(back_populates="reply")
    __table_args__ = (CheckConstraint(_in("status", REVIEW_STATUSES), name="status"),
                      CheckConstraint("char_length(body) BETWEEN 1 AND 4000", name="body_len"))


class ReviewHelpful(Base):
    __tablename__ = "review_helpful"
    review_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reviews.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    created_at: Mapped[datetime] = created_at()


class ReviewReport(Base):
    """A complaint about a review or a reply; open reports put the item into the moderation queue."""
    __tablename__ = "review_reports"
    id: Mapped[uuid.UUID] = uuid_pk()
    review_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reviews.id", ondelete="CASCADE"), index=True)
    target: Mapped[str] = mapped_column(String(8))  # review / reply
    reporter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(String(24))
    comment: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = created_at()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("review_id", "target", "reporter_id"),
                      CheckConstraint("target IN ('review','reply')", name="target"),
                      CheckConstraint(_in("reason", ("spam", "abuse", "personal_data", "advertising",
                                                     "medical_claims", "other")), name="reason"))
