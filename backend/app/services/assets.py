"""Uploads (single-use tickets, size + checksum verification) and authorized, short-lived downloads."""
from __future__ import annotations

import hashlib
import os
import secrets
import tempfile
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import utcnow
from ..dsp.media import MediaError, probe
from ..errors import ApiError
from ..models import App, Asset, UploadTicket
from ..security import Principal, sha256_hex
from ..storage import get_storage, new_key
from .common import audit

SOURCE_EXT = {"wav", "flac", "mp3", "m4a"}
COVER_EXT = {"png", "jpg", "jpeg", "webp"}


def limits_public() -> dict:
    s = get_settings()
    return {"max_upload_bytes": s.max_upload_bytes, "max_duration_seconds": s.max_duration_seconds,
            "accepted": sorted(SOURCE_EXT), "channels": [1, 2], "preview_seconds": s.preview_seconds}


def create_upload(db: Session, owner_id: uuid.UUID, app: App, kind: str, filename: str, size: int,
                  sha256: str | None) -> dict:
    s = get_settings()
    if app.developer_id != owner_id:
        raise ApiError("forbidden", 403)
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    if kind == "source":
        if ext not in SOURCE_EXT:
            raise ApiError("unsupported_format", 415)
        if size <= 0:
            raise ApiError("empty_file", 422)
        if size > s.max_upload_bytes:
            raise ApiError("file_too_large", 413, max_bytes=s.max_upload_bytes)
    elif kind == "cover":
        if ext not in COVER_EXT or size > 10 * 1024 * 1024:
            raise ApiError("unsupported_format", 415)
    else:
        raise ApiError("validation_failed", 422)
    if sha256 is not None and (len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256)):
        raise ApiError("validation_failed", 422)
    asset = Asset(owner_id=owner_id, app_id=app.id, kind=kind, storage_key=new_key(kind, owner_id, ext),
                  original_name=filename[:255], content_type=_ctype(ext), status="pending")
    db.add(asset)
    db.flush()
    token = secrets.token_urlsafe(32)
    db.add(UploadTicket(asset_id=asset.id, token_hash=sha256_hex(token), expected_size=size,
                        expected_sha256=sha256, expires_at=utcnow() + timedelta(seconds=s.upload_url_ttl_seconds)))
    audit(db, owner_id, "asset.upload_created", "asset", asset.id, {"kind": kind, "size": size})
    return {"asset_id": str(asset.id), "upload_url": f"/api/v1/uploads/{token}",
            "expires_in": s.upload_url_ttl_seconds, "method": "PUT"}


def _ctype(ext: str) -> str:
    return {"wav": "audio/wav", "flac": "audio/flac", "mp3": "audio/mpeg", "m4a": "audio/mp4",
            "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp",
            "json": "application/json", "pdf": "application/pdf"}.get(ext, "application/octet-stream")


async def receive_upload(db: Session, token: str, body_iter) -> Asset:
    ticket = db.scalar(select(UploadTicket).where(UploadTicket.token_hash == sha256_hex(token)).with_for_update())
    if not ticket or ticket.used_at is not None or ticket.expires_at < utcnow():
        raise ApiError("upload_link_invalid", 410)
    asset = db.get(Asset, ticket.asset_id, with_for_update=True)
    if asset is None or asset.immutable or asset.status != "pending":
        raise ApiError("upload_link_invalid", 410)
    h = hashlib.sha256()
    size = 0
    tmpdir = get_settings().job_workdir
    os.makedirs(tmpdir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=tmpdir, prefix="upload-")
    try:
        with os.fdopen(fd, "wb") as fh:
            async for chunk in body_iter:
                size += len(chunk)
                if size > ticket.expected_size:
                    raise ApiError("upload_size_mismatch", 422)
                h.update(chunk)
                fh.write(chunk)
        digest = h.hexdigest()
        if size != ticket.expected_size:
            raise ApiError("upload_size_mismatch", 422)
        if ticket.expected_sha256 and digest != ticket.expected_sha256:
            raise ApiError("upload_checksum_mismatch", 422)
        if asset.kind == "source":
            try:
                asset.probe = probe(tmp).to_dict()
            except MediaError as e:
                asset.status = "rejected"
                asset.probe = {"error": e.code}
                ticket.used_at = utcnow()
                db.commit()
                raise ApiError(e.code if e.code in ("unsupported_format", "no_audio_stream") else
                               "unsupported_or_corrupt", 422)
            if asset.probe["channels"] > 2:
                asset.status = "rejected"
                ticket.used_at = utcnow()
                db.commit()
                raise ApiError("too_many_channels", 422)
            if asset.probe["duration"] > get_settings().max_duration_seconds:
                asset.status = "rejected"
                ticket.used_at = utcnow()
                db.commit()
                raise ApiError("too_long", 422)
        get_storage().put_file(asset.storage_key, tmp, asset.content_type)
        asset.size_bytes = size
        asset.sha256 = digest
        asset.status = "uploaded"
        ticket.used_at = utcnow()
        audit(db, asset.owner_id, "asset.uploaded", "asset", asset.id, {"size": size, "sha256": digest})
        db.commit()
        return asset
    finally:
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass


def store_generated(db: Session, *, owner_id, app_id, kind: str, path: str, ext: str, sha256: str,
                    immutable: bool = True, status: str = "ready") -> Asset:
    key = new_key(kind, owner_id, ext)
    get_storage().put_file(key, path, _ctype(ext))
    a = Asset(owner_id=owner_id, app_id=app_id, kind=kind, storage_key=key, content_type=_ctype(ext),
              size_bytes=os.path.getsize(path), sha256=sha256, status=status, immutable=immutable)
    db.add(a)
    return a


def can_read_asset(db: Session, p: Principal, asset: Asset) -> bool:
    if p.user is None:
        return False
    if asset.owner_id == p.user.id and p.method in ("session", "api_key"):
        return True
    if (p.has("moderator") or p.has("admin")) and asset.kind in (
            "source", "master", "delivery", "preview", "qa_report", "params", "cover", "public_preview"):
        return True
    return False


def signed_url(asset: Asset, filename: str | None = None) -> str:
    ext = asset.storage_key.rsplit(".", 1)[-1]
    return get_storage().signed_get_url(asset.storage_key, filename or f"{asset.kind}.{ext}", asset.content_type)
