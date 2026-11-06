"""NAP-104 (spec v2.1, M5): Secure Player — listener devices, protected packages, offline leases.

Flow: the browser creates a non-extractable P-256 key pair once and registers the public half here. To keep a
program for listening without the internet it asks for a lease: the server checks the purchase or subscription,
makes (once per delivery file) an encrypted .napx package and returns a lease signed with Ed25519 that carries the
package's content key encrypted to that device's key, with an expiry. The package is useless without a lease, a
lease is useless on another device, and a revoked device or a lost purchase stops getting leases; when online the
player asks for the lease status and deletes copies that are no longer allowed.
"""
from __future__ import annotations

import base64
import hashlib
import os
import tempfile
import uuid
from datetime import datetime, timedelta

from cryptography.hazmat.primitives import serialization
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import utcnow
from ..errors import ApiError
from ..models import App, AppVersion, Asset, ContentPackage, LicenseDevice, LicenseLease, User
from ..secure import keys, napx
from ..storage import get_storage
from .assets import signed_url, store_generated
from .billing import access_for, require_session
from .common import audit
from .sessions import LAYOUT_NAMES

PACKAGE_EXT = "napx"


# ------------------------------------------------------------------ devices

def device_view(d: LicenseDevice, leases: int = 0) -> dict:
    return {"id": str(d.id), "name": d.name, "platform": d.platform, "status": d.status,
            "key_fingerprint": d.key_sha256[:16], "created_at": d.created_at.isoformat(),
            "last_lease_at": d.last_lease_at.isoformat() if d.last_lease_at else None,
            "revoked_at": d.revoked_at.isoformat() if d.revoked_at else None, "active_leases": leases}


def register_device(db: Session, user: User, public_key: str, name: str, platform: str) -> tuple[LicenseDevice, bool]:
    try:
        key = napx.load_device_key(public_key)
    except napx.NapxError:
        raise ApiError("validation_failed", 422, details={"field": "public_key"})
    der = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    digest = hashlib.sha256(der).hexdigest()
    existing = db.scalar(select(LicenseDevice).where(LicenseDevice.user_id == user.id,
                                                     LicenseDevice.key_sha256 == digest))
    if existing:
        if existing.status != "active":
            raise ApiError("license_device_revoked", 409)
        return existing, False
    active = db.scalar(select(func.count()).select_from(LicenseDevice).where(
        LicenseDevice.user_id == user.id, LicenseDevice.status == "active"))
    limit = get_settings().license_max_devices
    if active >= limit:
        raise ApiError("license_device_limit", 409, details={"limit": limit})
    d = LicenseDevice(user_id=user.id, name=(name or "Browser").strip()[:120] or "Browser",
                      platform=(platform or "web")[:40], public_key=base64.b64encode(der).decode(),
                      key_sha256=digest, status="active")
    db.add(d)
    db.flush()
    audit(db, user.id, "license_device.registered", "license_device", d.id, {"platform": d.platform})
    return d, True


def _own_device(db: Session, user: User, device_id: uuid.UUID) -> LicenseDevice:
    d = db.get(LicenseDevice, device_id)
    if not d or d.user_id != user.id:
        raise ApiError("not_found", 404, entity="device")
    return d


def list_devices(db: Session, user: User) -> list[dict]:
    now = utcnow()
    counts = dict(db.execute(select(LicenseLease.device_id, func.count()).where(
        LicenseLease.user_id == user.id, LicenseLease.revoked_at.is_(None), LicenseLease.expires_at > now)
        .group_by(LicenseLease.device_id)).all())
    rows = db.scalars(select(LicenseDevice).where(LicenseDevice.user_id == user.id)
                      .order_by(LicenseDevice.status, LicenseDevice.created_at.desc())).all()
    return [device_view(d, counts.get(d.id, 0)) for d in rows]


def revoke_device(db: Session, user: User, device_id: uuid.UUID) -> LicenseDevice:
    d = _own_device(db, user, device_id)
    if d.status == "active":
        now = utcnow()
        d.status, d.revoked_at = "revoked", now
        for lease in db.scalars(select(LicenseLease).where(LicenseLease.device_id == d.id,
                                                           LicenseLease.revoked_at.is_(None))):
            lease.revoked_at, lease.revoke_reason = now, "device_revoked"
        audit(db, user.id, "license_device.revoked", "license_device", d.id, {})
    return d


# ------------------------------------------------------------------ packages

def ensure_package(db: Session, source: Asset) -> ContentPackage:
    """The .napx of a delivery file, made once and reused for every listener (each gets their own lease)."""
    pkg = db.scalar(select(ContentPackage).where(ContentPackage.source_asset_id == source.id))
    if pkg:
        return pkg
    if not source.sha256 or not source.size_bytes:
        raise ApiError("not_found", 404, entity="asset")
    kek_id, kek = keys.kek()
    kid, signer = keys.signer()
    cek, pkg_id = os.urandom(32), uuid.uuid4()
    with tempfile.TemporaryDirectory(prefix="napx-") as tmp:
        src_path, out_path = os.path.join(tmp, "src"), os.path.join(tmp, "out.napx")
        get_storage().get_to_file(source.storage_key, src_path)
        with open(src_path, "rb") as src, open(out_path, "wb") as dst:
            p = napx.write_package(src, dst, cek=cek, signer=signer, content_id=str(source.id),
                                   sha256=source.sha256, size=source.size_bytes, content_type=source.content_type,
                                   key_id=str(pkg_id))
        h = hashlib.sha256()
        with open(out_path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        stored = store_generated(db, owner_id=source.owner_id, app_id=source.app_id, kind="secure_package",
                                 path=out_path, ext=PACKAGE_EXT, sha256=h.hexdigest())
        db.flush()
    pkg = ContentPackage(id=pkg_id, source_asset_id=source.id, package_asset_id=stored.id, kek_id=kek_id,
                         wrapped_cek=napx.wrap_cek(kek, cek, str(pkg_id).encode()), signing_kid=kid,
                         manifest_sha256=p.manifest_sha256, segment_size=p.manifest["segment_size"])
    db.add(pkg)
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:  # made by a parallel request a moment ago: use that one
        get_storage().delete(stored.storage_key)
        db.delete(stored)
        pkg = db.scalar(select(ContentPackage).where(ContentPackage.source_asset_id == source.id))
    audit(db, None, "secure_package.created", "asset", source.id, {"package": str(pkg.id)})
    return pkg


def package_cek(pkg: ContentPackage) -> bytes:
    kek_id, kek = keys.kek()
    if pkg.kek_id != kek_id:
        raise ApiError("secure_key_rotated", 409)
    return napx.unwrap_cek(kek, pkg.wrapped_cek, str(pkg.id).encode())


# ------------------------------------------------------------------ leases

def public_key() -> dict:
    kid, signer = keys.signer()
    return {"kid": kid, "alg": "EdDSA", "crv": "Ed25519", "x": napx.b64u(keys.public_raw(signer))}


def _expiry(db: Session, user: User, app: App, session_key: str) -> datetime:
    acc = access_for(db, user.id, app.id)
    require_session(acc, session_key)  # NAP-064: the buyer's tier must include this session
    exp = utcnow() + timedelta(days=get_settings().license_offline_days)
    if acc["paid_until"]:  # a subscription: offline listening never outlives the paid period
        paid = datetime.fromisoformat(acc["paid_until"]) + timedelta(days=get_settings().subscription_grace_days)
        exp = min(exp, paid)
    return exp


def issue_lease(db: Session, user: User, device_id: uuid.UUID, app_id: uuid.UUID, session_key: str,
                layout: str = "headphones") -> dict:
    dev = _own_device(db, user, device_id)
    if dev.status != "active":
        raise ApiError("license_device_revoked", 409)
    app = db.get(App, app_id)
    if not app or not app.published_version_id:
        raise ApiError("not_found", 404, entity="app")
    exp = _expiry(db, user, app, session_key)
    v = db.get(AppVersion, app.published_version_id)
    ps = next((s for s in v.sessions if s.key == session_key), None)
    if ps is None:
        raise ApiError("not_found", 404, entity="program_session")
    if layout not in LAYOUT_NAMES:
        raise ApiError("validation_failed", 422, details={"field": "layout"})
    var = None if layout == "headphones" else (ps.variants or {}).get(layout)
    if layout != "headphones" and not var:
        raise ApiError("not_found", 404, entity="layout")
    source = db.get(Asset, uuid.UUID(var["delivery_asset_id"]) if var else ps.delivery_asset_id)
    pkg = ensure_package(db, source)
    package_asset = db.get(Asset, pkg.package_asset_id)
    now = utcnow()
    lease = LicenseLease(user_id=user.id, device_id=dev.id, app_id=app.id, version_id=v.id, session_key=ps.key,
                         layout=layout, package_id=pkg.id, issued_at=now, expires_at=exp)
    db.add(lease)
    db.flush()
    dev.last_lease_at = now
    kid, signer = keys.signer()
    sealed = napx.seal_for_device(napx.load_device_key(dev.public_key), package_cek(pkg), str(lease.id))
    payload = {"v": 1, "lid": str(lease.id), "sub": str(user.id), "dev": str(dev.id), "app": str(app.id),
               "ver": str(v.id), "session": ps.key, "layout": layout, "content_id": str(source.id),
               "sha256": source.sha256, "manifest_sha256": pkg.manifest_sha256, "iat": int(now.timestamp()),
               "exp": int(exp.timestamp()), "key": sealed}
    audit(db, user.id, "license_lease.issued", "license_lease", lease.id,
          {"device": str(dev.id), "app": str(app.id), "session": ps.key, "layout": layout})
    return {"lease_id": str(lease.id), "lease": napx.sign_lease(signer, kid, payload),
            "expires_at": exp.isoformat(), "signing_key": public_key(),
            "package": {"url": signed_url(package_asset, f"{ps.key}.{PACKAGE_EXT}"),
                        "size_bytes": package_asset.size_bytes, "sha256": package_asset.sha256,
                        "manifest_sha256": pkg.manifest_sha256},
            "program_session": {"key": ps.key, "titles": ps.titles, "duration_seconds": ps.duration_seconds},
            "app": {"id": str(app.id), "slug": app.slug, "version": v.version_label},
            "playback": {"layout": layout, "channels": var["channels"] if var else 2,
                         "channel_order": var["channel_order"] if var else ["FL", "FR"], "sample_rate": 48000}}


def lease_status(db: Session, user: User, lease_id: uuid.UUID) -> dict:
    """Asked by the player whenever it is online: a copy whose lease is not `active` must be deleted."""
    lease = db.get(LicenseLease, lease_id)
    if not lease or lease.user_id != user.id:
        raise ApiError("not_found", 404, entity="lease")
    now = utcnow()
    if lease.revoked_at is None:
        acc = access_for(db, user.id, lease.app_id)
        if not acc["has_access"]:
            lease.revoked_at, lease.revoke_reason = now, "no_entitlement"
        elif acc.get("sessions") is not None and lease.session_key not in acc["sessions"]:
            lease.revoked_at, lease.revoke_reason = now, "tier_required"  # NAP-064: a tier change drops the session
    if lease.revoked_at:
        status = "revoked"
    elif lease.expires_at <= now:
        status = "expired"
    else:
        status = "active"
    return {"lease_id": str(lease.id), "status": status, "reason": lease.revoke_reason,
            "expires_at": lease.expires_at.isoformat()}
