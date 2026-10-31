"""Apps, versions, assets, DSP profiles, conversion jobs, moderation, questionnaires."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer,
                        String, Text, UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base, created_at, uuid_pk
from .system import _in

APP_VERSION_STATES = ("draft", "submitted", "approved", "published", "rejected", "withdrawn", "archived")
JOB_STATES = ("queued", "validating", "decoding", "rendering", "quality_check", "packaging", "completed",
              "failed", "canceled")
JOB_TERMINAL = ("completed", "failed", "canceled")
ASSET_KINDS = ("source", "master", "delivery", "preview", "public_preview", "qa_report", "params", "layer",
               "cover", "document", "label")


class App(Base):
    __tablename__ = "apps"
    id: Mapped[uuid.UUID] = uuid_pk()
    developer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("categories.id"))
    author_name: Mapped[str] = mapped_column(String(120))
    sales_status: Mapped[str] = mapped_column(String(16), default="active")  # active / withdrawn
    published_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_versions.id", use_alter=True, name="fk_apps_published_version"))
    public_preview_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assets.id", use_alter=True, name="fk_apps_public_preview"))
    cover_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assets.id", use_alter=True,
                                                                        name="fk_apps_cover"))
    cover_hue: Mapped[int] = mapped_column(Integer, default=210)  # generated cover when no image
    recommended_kit_product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("products.id", use_alter=True, name="fk_apps_kit"))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = created_at()
    __table_args__ = (CheckConstraint("sales_status IN ('active','withdrawn')", name="sales_status"),)


class AppVersion(Base):
    __tablename__ = "app_versions"
    id: Mapped[uuid.UUID] = uuid_pk()
    app_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("apps.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    version_label: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(16), default="draft")
    content_language: Mapped[str] = mapped_column(String(12), default="ru")
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)  # {ru:{title,..}, en:{...}}
    requirements: Mapped[dict] = mapped_column(JSONB, default=dict)  # min_api, capabilities, playback
    questionnaire_pre: Mapped[str | None] = mapped_column(String(64))
    questionnaire_post: Mapped[str | None] = mapped_column(String(64))
    rights_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    manifest: Mapped[dict | None] = mapped_column(JSONB)  # frozen at submit
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at()
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # NAP-083: publish as soon as the moderator approves (and a price is set), like "release automatically"
    auto_release: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    sessions: Mapped[list["ProgramSession"]] = relationship(order_by="ProgramSession.position",
                                                           cascade="all, delete-orphan", lazy="selectin")
    __table_args__ = (UniqueConstraint("app_id", "number"),
                      CheckConstraint(_in("state", APP_VERSION_STATES), name="state"))

    @property
    def frozen(self) -> bool:
        return self.state != "draft"


class ProgramSession(Base):
    """One lesson/session in the program, bound to a converted delivery asset."""
    __tablename__ = "program_sessions"
    id: Mapped[uuid.UUID] = uuid_pk()
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_versions.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    key: Mapped[str] = mapped_column(String(64))
    titles: Mapped[dict] = mapped_column(JSONB)  # {"ru": "...", "en": "..."}
    conversion_job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversion_jobs.id"))
    delivery_asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assets.id"))
    master_asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assets.id"))
    duration_seconds: Mapped[float] = mapped_column(Float)
    sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (UniqueConstraint("version_id", "key"), UniqueConstraint("version_id", "position"))


class ModerationDecision(Base):
    __tablename__ = "moderation_decisions"
    id: Mapped[uuid.UUID] = uuid_pk()
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_versions.id"), index=True)
    moderator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(String(16))  # approved / rejected / comment
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = created_at()


class Asset(Base):
    """Object in private storage. Key is generated by the server, never from user file names."""
    __tablename__ = "assets"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    app_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("apps.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    storage_key: Mapped[str] = mapped_column(String(255), unique=True)
    original_name: Mapped[str | None] = mapped_column(String(255))  # display only
    content_type: Mapped[str] = mapped_column(String(80), default="application/octet-stream")
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/uploaded/ready/rejected
    immutable: Mapped[bool] = mapped_column(Boolean, default=False)
    probe: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at()
    __table_args__ = (CheckConstraint(_in("kind", ASSET_KINDS), name="kind"),)


class UploadTicket(Base):
    """Single-use upload permission bound to one asset; cannot overwrite an immutable object."""
    __tablename__ = "upload_tickets"
    id: Mapped[uuid.UUID] = uuid_pk()
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assets.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expected_size: Mapped[int] = mapped_column(BigInteger)
    expected_sha256: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DspProfile(Base):
    """Versioned, admin-approved profile. Editing creates a new version row."""
    __tablename__ = "dsp_profiles"
    id: Mapped[uuid.UUID] = uuid_pk()
    slug: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    names: Mapped[dict] = mapped_column(JSONB)
    descriptions: Mapped[dict] = mapped_column(JSONB, default=dict)
    params: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(12), default="active")  # active / retired
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at()
    __table_args__ = (UniqueConstraint("slug", "version"),)


class ConversionJob(Base):
    __tablename__ = "conversion_jobs"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    app_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("apps.id"), index=True)
    source_asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assets.id"))
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dsp_profiles.id"))
    profile_snapshot: Mapped[dict] = mapped_column(JSONB)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(16), default="queued")
    stage_progress: Mapped[float] = mapped_column(Float, default=0.0)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    retry_of_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("conversion_jobs.id"))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    lease_owner: Mapped[str | None] = mapped_column(String(80))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_detail: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict | None] = mapped_column(JSONB)  # asset ids, opened atomically on completion
    report: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint(_in("state", JOB_STATES), name="state"),
                      Index("uq_job_idem", "owner_id", "idempotency_key", unique=True,
                            postgresql_where="idempotency_key IS NOT NULL"),
                      CheckConstraint("(state = 'completed') = (result IS NOT NULL)", name="result_only_completed"))


class Questionnaire(Base):
    __tablename__ = "questionnaires"
    id: Mapped[uuid.UUID] = uuid_pk()
    key: Mapped[str] = mapped_column(String(64), unique=True)  # e.g. "state-v1"
    items: Mapped[list] = mapped_column(JSONB)  # [{id, min, max, labels:{ru,en}}]
