"""NAP Audio Studio (spec v2.1 M1): projects with immutable revisions, routing profiles, render jobs/artifacts."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, created_at, uuid_pk
from .system import _in

RENDER_STATES = ("queued", "decoding", "rendering", "verifying", "completed", "failed", "canceled")


class AudioProject(Base):
    __tablename__ = "audio_projects"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    sample_rate: Mapped[int] = mapped_column(Integer, default=48_000)
    bit_depth: Mapped[int] = mapped_column(Integer, default=24)
    bus_count: Mapped[int] = mapped_column(Integer, default=2)          # 2 / 3 / 6 / 8 logical buses offered
    state: Mapped[dict] = mapped_column(JSONB, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    duration_frames: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("sample_rate IN (44100, 48000, 88200, 96000)", name="rate"),
                      CheckConstraint("bit_depth IN (16, 24, 32)", name="bits"),
                      CheckConstraint("bus_count IN (2, 3, 6, 8)", name="buses"))


class AudioProjectRevision(Base):
    """Immutable snapshot; renders always point at one."""
    __tablename__ = "audio_project_revisions"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("audio_projects.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    state: Mapped[dict] = mapped_column(JSONB)
    state_sha256: Mapped[str] = mapped_column(String(64))
    sample_rate: Mapped[int] = mapped_column(Integer)
    bit_depth: Mapped[int] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(String(200), default="")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at()
    __table_args__ = (UniqueConstraint("project_id", "number"),)


class RoutingProfile(Base):
    __tablename__ = "routing_profiles"
    id: Mapped[uuid.UUID] = uuid_pk()
    key: Mapped[str] = mapped_column(String(40))
    version: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(80))
    layout: Mapped[str] = mapped_column(String(20))
    channels: Mapped[list] = mapped_column(JSONB)
    matrix: Mapped[list] = mapped_column(JSONB)
    note: Mapped[str] = mapped_column(Text, default="")
    builtin: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at()
    __table_args__ = (UniqueConstraint("key", "version"),)


class RenderJob(Base):
    __tablename__ = "render_jobs"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("audio_projects.id"), index=True)
    revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("audio_project_revisions.id"))
    profile_key: Mapped[str] = mapped_column(String(40))
    profile_version: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(16), default="queued")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    lease_owner: Mapped[str | None] = mapped_column(String(80))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_detail: Mapped[dict | None] = mapped_column(JSONB)
    logs: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = created_at()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint(_in("state", RENDER_STATES), name="state"),
                      Index("uq_render_idem", "owner_id", "idempotency_key", unique=True,
                            postgresql_where="idempotency_key IS NOT NULL"))


class RenderArtifact(Base):
    __tablename__ = "render_artifacts"
    id: Mapped[uuid.UUID] = uuid_pk()
    render_job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("render_jobs.id"), unique=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assets.id"))
    sha256: Mapped[str] = mapped_column(String(64))
    format: Mapped[str] = mapped_column(String(20))
    channel_layout: Mapped[str] = mapped_column(String(20))
    channel_order: Mapped[list] = mapped_column(JSONB)
    sample_rate: Mapped[int] = mapped_column(Integer)
    bit_depth: Mapped[int] = mapped_column(Integer)
    frames: Mapped[int] = mapped_column(BigInteger)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    report: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at()
