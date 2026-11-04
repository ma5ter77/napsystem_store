"""Devices, play sessions, commands, events, questionnaires answers, measurements and consents."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String,
                        UniqueConstraint, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, created_at, uuid_pk
from .system import _in

SESSION_STATES = ("created", "precheck", "ready", "running", "paused", "completed", "cancelled", "failed")
SESSION_ACTIVE = ("created", "precheck", "ready", "running", "paused")
MEASUREMENT_TYPES = ("self_report", "heart_rate", "hrv_rmssd", "eeg")


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    adapter: Mapped[str] = mapped_column(String(24))  # simulated / web_headphones / nap_chair
    name: Mapped[str] = mapped_column(String(120))
    model_code: Mapped[str | None] = mapped_column(String(64))
    capabilities: Mapped[dict] = mapped_column(JSONB)
    # NAP-101: {layout: {channel: {"gain_db": -6..6, "delay_ms": 0..20}}} — the listener's own speakers / chair
    calibration: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)  # device-service credential
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = created_at()
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlaySession(Base):
    __tablename__ = "play_sessions"
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id"), index=True)
    app_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("apps.id"), index=True)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_versions.id"))
    program_session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("program_sessions.id"))
    state: Mapped[str] = mapped_column(String(16), default="created")
    state_version: Mapped[int] = mapped_column(Integer, default=0)
    position_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    headphones_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)  # user confirmation, not a measurement
    lr_test_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    channel_separation_source: Mapped[str | None] = mapped_column(String(24))  # user_confirmed / device_capability
    precheck: Mapped[dict | None] = mapped_column(JSONB)
    pre_status: Mapped[str] = mapped_column(String(12), default="pending")
    post_status: Mapped[str] = mapped_column(String(12), default="pending")
    last_event_seq: Mapped[int] = mapped_column(Integer, default=0)
    failure_reason: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(_in("state", SESSION_STATES), name="state"),
        CheckConstraint("pre_status IN ('pending','completed','skipped')", name="pre"),
        CheckConstraint("post_status IN ('pending','completed','skipped')", name="post"),
        # one device executes at most one session — enforced by the database
        Index("uq_one_active_session_per_device", "device_id", unique=True,
              postgresql_where="state IN ('created','precheck','ready','running','paused')"),
    )


class SessionCommand(Base):
    __tablename__ = "session_commands"
    id: Mapped[uuid.UUID] = uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("play_sessions.id"), index=True)
    command_id: Mapped[str] = mapped_column(String(64))
    command: Mapped[str] = mapped_column(String(16))
    expected_version: Mapped[int] = mapped_column(Integer)
    result: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at()
    __table_args__ = (UniqueConstraint("session_id", "command_id"),)


class SessionEvent(Base):
    __tablename__ = "session_events"
    id: Mapped[uuid.UUID] = uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("play_sessions.id"), index=True)
    event_id: Mapped[str] = mapped_column(String(64))
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(32))
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = created_at()
    __table_args__ = (UniqueConstraint("session_id", "event_id"), UniqueConstraint("session_id", "seq"))


class QuestionnaireResponse(Base):
    __tablename__ = "questionnaire_responses"
    id: Mapped[uuid.UUID] = uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("play_sessions.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    phase: Mapped[str] = mapped_column(String(8))  # pre / post
    questionnaire_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(12))  # completed / skipped
    answers: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = created_at()
    __table_args__ = (UniqueConstraint("session_id", "phase"),)


class Measurement(Base):
    __tablename__ = "measurements"
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("play_sessions.id"), index=True)
    type: Mapped[str] = mapped_column(String(16))
    metric: Mapped[str] = mapped_column(String(48))  # e.g. relaxation / tension / bpm / rmssd
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(16))
    phase: Mapped[str | None] = mapped_column(String(8))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(48))  # questionnaire / sensor:<id> / simulator
    quality: Mapped[str] = mapped_column(String(16), default="ok")
    calc_version: Mapped[str] = mapped_column(String(16), default="1")
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)  # explicitly marked artificial data
    created_at: Mapped[datetime] = created_at()
    __table_args__ = (CheckConstraint(_in("type", MEASUREMENT_TYPES), name="type"),)


class Consent(Base):
    __tablename__ = "consents"
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # store_measurements
    granted_at: Mapped[datetime] = created_at()
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retention_days: Mapped[int] = mapped_column(Integer, default=365)
