"""NAP-104 (spec v2.1, M5): protected packages, listener devices with keys, offline leases."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, created_at, uuid_pk
from .system import _in

LICENSE_DEVICE_STATES = ("active", "revoked")


class ContentPackage(Base):
    """One .napx per delivery file. The content key is stored only wrapped by the server KEK."""
    __tablename__ = "content_packages"
    id: Mapped[uuid.UUID] = uuid_pk()
    source_asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assets.id"), unique=True)
    package_asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assets.id"))
    kek_id: Mapped[str] = mapped_column(String(32))
    wrapped_cek: Mapped[str] = mapped_column(Text)
    signing_kid: Mapped[str] = mapped_column(String(32))
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    segment_size: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = created_at()


class LicenseDevice(Base):
    """A listener's browser or app that keeps offline copies. Holds only the public half of its P-256 key; the
    private half never leaves the device (non-extractable WebCrypto key)."""
    __tablename__ = "license_devices"
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    platform: Mapped[str] = mapped_column(String(40), default="web")
    public_key: Mapped[str] = mapped_column(Text)
    key_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(12), default="active")
    created_at: Mapped[datetime] = created_at()
    last_lease_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint(_in("status", LICENSE_DEVICE_STATES), name="status"),
                      Index("uq_license_device_key", "user_id", "key_sha256", unique=True))


class LicenseLease(Base):
    """A signed permission to play one package offline on one device until `expires_at`."""
    __tablename__ = "license_leases"
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("license_devices.id"), index=True)
    app_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("apps.id"))
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_versions.id"))
    session_key: Mapped[str] = mapped_column(String(64))
    layout: Mapped[str] = mapped_column(String(24))
    package_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("content_packages.id"))
    issued_at: Mapped[datetime] = created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(32))
