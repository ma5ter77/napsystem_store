"""Apps, versions, program builder, manifest, moderation and publication."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..dsp.engine import ENGINE_VERSION
from ..errors import ApiError
from ..models import (App, AppVersion, Asset, Category, ConversionJob, ModerationDecision, Price, ProgramSession,
                      User)
from ..storage import get_storage
from .common import audit, notify, upsert_translation

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")
CAPS = {"stereo_output", "stereo_separation", "vibroacoustic_output", "heart_rate_sensor", "hrv_sensor"}


@lru_cache
def manifest_validator() -> Draft202012Validator:
    schema = json.loads((Path(__file__).parent.parent / "schemas" / "manifest.v1.json").read_text())
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_manifest(m: dict) -> list[dict]:
    return [{"path": "/".join(str(p) for p in e.absolute_path), "message": e.message}
            for e in sorted(manifest_validator().iter_errors(m), key=lambda e: list(e.absolute_path))]


def manifest_hash(m: dict) -> str:
    return hashlib.sha256(json.dumps(m, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def own_app(db: Session, app_id: uuid.UUID, dev_id: uuid.UUID) -> App:
    app = db.get(App, app_id)
    if not app or app.developer_id != dev_id:
        raise ApiError("not_found", 404, entity="app")
    return app


def latest_version(db: Session, app_id: uuid.UUID) -> AppVersion | None:
    return db.scalar(select(AppVersion).where(AppVersion.app_id == app_id).order_by(AppVersion.number.desc()).limit(1))


def create_app(db: Session, dev: User, slug: str, category_slug: str | None, author_name: str,
               metadata: dict, content_language: str) -> App:
    if not SLUG_RE.match(slug):
        raise ApiError("validation_failed", 422, details={"field": "slug"})
    if db.scalar(select(App).where(App.slug == slug)):
        raise ApiError("slug_taken", 409)
    cat = None
    if category_slug:
        cat = db.scalar(select(Category).where(Category.kind == "app", Category.slug == category_slug))
        if not cat:
            raise ApiError("validation_failed", 422, details={"field": "category"})
    _check_meta(metadata)
    app = App(developer_id=dev.id, slug=slug, category_id=cat.id if cat else None, author_name=author_name[:120],
              cover_hue=int(hashlib.sha1(slug.encode()).hexdigest()[:4], 16) % 360)
    db.add(app)
    db.flush()
    v = AppVersion(app_id=app.id, number=1, version_label="1.0", content_language=content_language,
                   metadata_=metadata, requirements={"min_api_version": "1.0",
                                                     "required_capabilities": ["stereo_output", "stereo_separation"]},
                   questionnaire_pre="state-v1", questionnaire_post="state-v1")
    db.add(v)
    for loc, data in metadata.items():
        upsert_translation(db, "app", app.id, loc, data, "draft")
    audit(db, dev.id, "app.created", "app", app.id, {"slug": slug})
    return app


def _check_meta(metadata: dict) -> None:
    if not metadata or not set(metadata) <= {"ru", "en"}:
        raise ApiError("validation_failed", 422, details={"field": "metadata"})
    for loc, m in metadata.items():
        if not isinstance(m, dict) or not str(m.get("title", "")).strip():
            raise ApiError("validation_failed", 422, details={"field": f"metadata.{loc}.title"})
        if set(m) - {"title", "subtitle", "description"}:
            raise ApiError("validation_failed", 422, details={"field": f"metadata.{loc}"})


def editable_version(db: Session, app: App, version_id: uuid.UUID) -> AppVersion:
    v = db.get(AppVersion, version_id)
    if not v or v.app_id != app.id:
        raise ApiError("not_found", 404, entity="version")
    if v.state != "draft":
        raise ApiError("version_frozen", 409)
    return v


def update_version(db: Session, app: App, v: AppVersion, data: dict, actor_id: uuid.UUID) -> AppVersion:
    if "metadata" in data:
        _check_meta(data["metadata"])
        v.metadata_ = data["metadata"]
        for loc, m in data["metadata"].items():
            upsert_translation(db, "app", app.id, loc, m, "draft") if app.published_version_id is None else None
    if "content_language" in data:
        if not re.match(r"^[a-z]{2,3}(-[A-Z]{2})?$|^none$", data["content_language"]):
            raise ApiError("validation_failed", 422, details={"field": "content_language"})
        v.content_language = data["content_language"]
    if "version_label" in data:
        if not re.match(r"^[0-9]+\.[0-9]+(\.[0-9]+)?$", data["version_label"]):
            raise ApiError("validation_failed", 422, details={"field": "version_label"})
        v.version_label = data["version_label"]
    if "required_capabilities" in data or "min_api_version" in data:
        caps = data.get("required_capabilities", v.requirements.get("required_capabilities", []))
        if not set(caps) <= CAPS:
            raise ApiError("validation_failed", 422, details={"field": "required_capabilities"})
        api = data.get("min_api_version", v.requirements.get("min_api_version", "1.0"))
        if not re.match(r"^[0-9]+\.[0-9]+$", api):
            raise ApiError("validation_failed", 422, details={"field": "min_api_version"})
        v.requirements = {"min_api_version": api, "required_capabilities": sorted(set(caps))}
    for phase in ("questionnaire_pre", "questionnaire_post"):
        if phase in data:
            setattr(v, phase, data[phase] or None)
    if data.get("confirm_rights"):
        v.rights_confirmed_at = utcnow()
        audit(db, actor_id, "version.rights_confirmed", "app_version", v.id)
    if "category" in data:
        cat = db.scalar(select(Category).where(Category.kind == "app", Category.slug == data["category"]))
        if not cat:
            raise ApiError("validation_failed", 422, details={"field": "category"})
        app.category_id = cat.id
    audit(db, actor_id, "version.updated", "app_version", v.id, {"fields": sorted(data)})
    return v


def set_program(db: Session, app: App, v: AppVersion, sessions: list[dict], actor_id: uuid.UUID) -> AppVersion:
    if not sessions:
        raise ApiError("validation_failed", 422, details={"field": "sessions"})
    keys = set()
    new = []
    for i, s in enumerate(sessions, start=1):
        if not isinstance(s, dict):
            raise ApiError("validation_failed", 422, details={"field": f"sessions.{i}"})
        key = s.get("key", "")
        if not isinstance(key, str) or not re.match(r"^[a-z0-9][a-z0-9-]{0,63}$", key) or key in keys:
            raise ApiError("validation_failed", 422, details={"field": f"sessions.{i}.key"})
        keys.add(key)
        titles = s.get("titles") or {}
        if not isinstance(titles, dict) or not titles or not set(titles) <= {"ru", "en"} \
                or not all(str(x).strip() for x in titles.values()):
            raise ApiError("validation_failed", 422, details={"field": f"sessions.{i}.titles"})
        try:  # NAP-068: a session without a chosen track used to crash with 500
            job_id = uuid.UUID(str(s.get("conversion_job_id") or ""))
        except ValueError:
            raise ApiError("validation_failed", 422, details={"field": f"sessions.{i}.track"}) from None
        job = db.get(ConversionJob, job_id)
        if not job or job.app_id != app.id or job.owner_id != app.developer_id:
            raise ApiError("not_found", 404, entity="conversion")
        if job.state != "completed" or not job.result:
            raise ApiError("conversion_not_completed", 409)
        new.append(_session_from_job(db, job, i, key, titles))
    v.sessions.clear()
    db.flush()
    v.sessions.extend(new)
    audit(db, actor_id, "version.program_set", "app_version", v.id, {"sessions": len(new)})
    return v


def _session_from_job(db: Session, job: ConversionJob, position: int, key: str, titles: dict) -> ProgramSession:
    delivery = db.get(Asset, uuid.UUID(job.result["delivery"]))
    return ProgramSession(position=position, key=key, titles=titles, conversion_job_id=job.id,
                          delivery_asset_id=delivery.id, master_asset_id=uuid.UUID(job.result["master"]),
                          duration_seconds=job.result["duration_seconds"], sha256=delivery.sha256)


def autofill_program(db: Session, job: ConversionJob) -> AppVersion | None:
    """NAP-069: the first completed track becomes session 1 of an empty draft program, titled like the app,
    so a single-track meditation needs no manual program step. The author can still edit or extend it."""
    v = latest_version(db, job.app_id)
    if v is None or v.state != "draft" or v.sessions:
        return None
    titles = {loc: str(m["title"]).strip() for loc, m in (v.metadata_ or {}).items()
              if loc in ("ru", "en") and isinstance(m, dict) and str(m.get("title") or "").strip()}
    if not titles:
        app = db.get(App, job.app_id)
        titles = {"ru": app.slug}
    v.sessions.append(_session_from_job(db, job, 1, "s1", titles))
    audit(db, job.owner_id, "version.program_autofilled", "app_version", v.id, {"conversion_job_id": str(job.id)})
    return v


def build_manifest(db: Session, app: App, v: AppVersion) -> dict:
    dsp = None
    sessions = []
    for s in v.sessions:
        job = db.get(ConversionJob, s.conversion_job_id)
        snap = job.profile_snapshot
        dsp = dsp or {"profile": snap["slug"], "profile_version": snap["version"], "engine_version": ENGINE_VERSION}
        sessions.append({"id": s.key, "position": s.position, "title": s.titles, "asset_id": str(s.delivery_asset_id),
                         "sha256": s.sha256, "duration_seconds": round(s.duration_seconds, 6),
                         "dsp_profile": snap["slug"], "dsp_profile_version": snap["version"]})
    cat = db.get(Category, app.category_id) if app.category_id else None
    caps = v.requirements.get("required_capabilities", [])
    return {
        "schema_version": "1.0", "app_id": str(app.id), "app_version": v.version_label, "version_number": v.number,
        "author": {"name": app.author_name, "developer_id": str(app.developer_id)},
        "metadata": v.metadata_, "category": cat.slug if cat else None, "content_language": v.content_language,
        "device": {"min_api_version": v.requirements.get("min_api_version", "1.0"), "required_capabilities": caps},
        "playback": {"delivery_format": "flac", "sample_rate": 48000, "channels": 2,
                     "requires_channel_separation": "stereo_separation" in caps},
        "dsp": dsp or {"profile": "none", "profile_version": 0, "engine_version": ENGINE_VERSION},
        "sessions": sessions,
        "questionnaires": {"pre": v.questionnaire_pre, "post": v.questionnaire_post},
    }


def submit(db: Session, app: App, v: AppVersion, actor_id: uuid.UUID) -> AppVersion:
    if v.state != "draft":
        raise ApiError("version_frozen", 409)
    if not v.rights_confirmed_at:
        raise ApiError("rights_not_confirmed", 422)
    if not v.sessions:
        raise ApiError("program_empty", 422)
    m = build_manifest(db, app, v)
    errors = validate_manifest(m)
    if errors:
        raise ApiError("manifest_invalid", 422, details={"errors": errors})
    v.manifest = m
    v.manifest_sha256 = manifest_hash(m)
    v.state = "submitted"
    v.submitted_at = utcnow()
    for s in v.sessions:  # freeze referenced objects
        for aid in (s.delivery_asset_id, s.master_asset_id):
            db.get(Asset, aid).immutable = True
    audit(db, actor_id, "version.submitted", "app_version", v.id, {"manifest_sha256": v.manifest_sha256})
    return v


def verify_integrity(db: Session, app: App, v: AppVersion, deep: bool = False) -> list[str]:
    """Detect substitution of files/manifest/profile after submission."""
    problems = []
    current = build_manifest(db, app, v)
    if v.manifest is None or manifest_hash(current) != v.manifest_sha256 or manifest_hash(v.manifest) != v.manifest_sha256:
        problems.append("manifest_changed")
    for s in v.sessions:
        a = db.get(Asset, s.delivery_asset_id)
        if a.sha256 != s.sha256 or not a.immutable:
            problems.append(f"asset_changed:{s.key}")
        elif deep:
            try:  # streams the object: works for the local volume and the S3 bucket alike
                if get_storage().sha256(a.storage_key) != a.sha256:
                    problems.append(f"object_changed:{s.key}")
            except Exception:
                problems.append(f"object_missing:{s.key}")
    # the DSP profile snapshot of every session is part of the manifest hash above
    return problems


def moderate(db: Session, v: AppVersion, moderator_id: uuid.UUID, decision: str, comment: str) -> AppVersion:
    if decision not in ("approve", "reject", "comment"):
        raise ApiError("validation_failed", 422)
    app = db.get(App, v.app_id)
    if decision == "comment":
        db.add(ModerationDecision(version_id=v.id, moderator_id=moderator_id, decision="comment", comment=comment))
        audit(db, moderator_id, "moderation.comment", "app_version", v.id)
        return v
    if v.state != "submitted":
        raise ApiError("invalid_state", 409)
    if decision == "reject" and not comment.strip():
        raise ApiError("comment_required", 422)
    if decision == "approve":
        problems = verify_integrity(db, app, v, deep=True)
        if problems:
            raise ApiError("integrity_failed", 409, details={"problems": problems})
    v.state = "approved" if decision == "approve" else "rejected"
    v.decided_at = utcnow()
    db.add(ModerationDecision(version_id=v.id, moderator_id=moderator_id,
                              decision="approved" if decision == "approve" else "rejected", comment=comment))
    audit(db, moderator_id, f"moderation.{v.state}", "app_version", v.id, {"comment": comment[:500]})
    # NAP-083: approval puts the app in the store right away when the author chose automatic release and set a price
    released = v.state == "approved" and try_auto_release(db, app, v, moderator_id)
    dev = db.get(User, app.developer_id)
    template = "version_published_auto" if released else f"version_{v.state}"
    notify(db, dev.id, dev.locale, template, f"mod:{v.id}:{v.state}",
           app=app_title(app, v, dev.locale), version=v.version_label, comment=comment or "—")
    return v


def app_title(app: App, v: AppVersion, locale: str) -> str:
    m = v.metadata_ or {}
    return (m.get(locale) or {}).get("title") or (m.get("ru") or {}).get("title") or (m.get("en") or {}).get("title") \
        or app.slug


def release_blockers(db: Session, app: App, v: AppVersion) -> list[str]:
    """Why an approved version is not in the store yet (empty = it can be published now)."""
    out = []
    if not db.scalar(select(Price).where(Price.app_id == app.id, Price.active.is_(True))):
        out.append("price")
    if not v.auto_release:
        out.append("manual_release")
    return out


def try_auto_release(db: Session, app: App, v: AppVersion, actor_id: uuid.UUID) -> bool:
    if v.state != "approved" or release_blockers(db, app, v):
        return False
    publish(db, app, v, actor_id)
    audit(db, actor_id, "version.auto_released", "app_version", v.id)
    return True


def publish(db: Session, app: App, v: AppVersion, actor_id: uuid.UUID) -> AppVersion:
    if v.state != "approved":
        raise ApiError("invalid_state", 409)
    if not db.scalar(select(Price).where(Price.app_id == app.id, Price.active.is_(True))):
        raise ApiError("price_required", 422)
    problems = verify_integrity(db, app, v)
    if problems:
        raise ApiError("integrity_failed", 409, details={"problems": problems})
    if app.published_version_id:
        prev = db.get(AppVersion, app.published_version_id)
        if prev and prev.id != v.id:
            prev.state = "archived"
    v.state = "published"
    v.published_at = utcnow()
    app.published_version_id = v.id
    for loc, m in v.metadata_.items():
        upsert_translation(db, "app", app.id, loc, m, "published")
    audit(db, actor_id, "version.published", "app_version", v.id)
    return v


def new_version(db: Session, app: App, actor_id: uuid.UUID) -> AppVersion:
    last = latest_version(db, app.id)
    if last and last.state in ("draft", "submitted"):
        raise ApiError("draft_exists", 409)
    major, minor = (last.version_label.split(".") + ["0"])[:2] if last else ("1", "0")
    v = AppVersion(app_id=app.id, number=(last.number + 1) if last else 1,
                   version_label=f"{major}.{int(minor) + 1}", content_language=last.content_language if last else "ru",
                   metadata_=dict(last.metadata_) if last else {}, requirements=dict(last.requirements) if last else {},
                   questionnaire_pre=last.questionnaire_pre if last else None,
                   questionnaire_post=last.questionnaire_post if last else None,
                   auto_release=last.auto_release if last else True)
    db.add(v)
    db.flush()
    if last:
        for s in last.sessions:
            v.sessions.append(ProgramSession(position=s.position, key=s.key, titles=s.titles,
                                             conversion_job_id=s.conversion_job_id,
                                             delivery_asset_id=s.delivery_asset_id, master_asset_id=s.master_asset_id,
                                             duration_seconds=s.duration_seconds, sha256=s.sha256))
    audit(db, actor_id, "version.created", "app_version", v.id, {"number": v.number})
    return v


def withdraw_version(db: Session, app: App, v: AppVersion, actor_id: uuid.UUID) -> AppVersion:
    if v.state not in ("submitted", "approved"):
        raise ApiError("invalid_state", 409)
    v.state = "withdrawn"
    audit(db, actor_id, "version.withdrawn", "app_version", v.id)
    return v


def set_price(db: Session, app: App, kind: str, amount_minor: int, currency: str, interval: str | None,
              actor_id: uuid.UUID) -> Price:
    if kind not in ("one_time", "subscription", "free"):
        raise ApiError("validation_failed", 422, details={"field": "kind"})
    if not re.match(r"^[A-Z]{3}$", currency):
        raise ApiError("validation_failed", 422, details={"field": "currency"})
    if kind == "free":
        amount_minor, interval = 0, None
    elif amount_minor < 50 or amount_minor > 100_000_00:
        raise ApiError("validation_failed", 422, details={"field": "amount_minor"})
    if kind == "subscription" and interval not in ("month", "year"):
        raise ApiError("validation_failed", 422, details={"field": "interval"})
    if kind != "subscription":
        interval = None
    for p in db.scalars(select(Price).where(Price.app_id == app.id, Price.active.is_(True), Price.kind == kind)):
        p.active = False
    if kind == "free":
        for p in db.scalars(select(Price).where(Price.app_id == app.id, Price.active.is_(True))):
            p.active = False
    else:
        for p in db.scalars(select(Price).where(Price.app_id == app.id, Price.active.is_(True), Price.kind == "free")):
            p.active = False
    price = Price(app_id=app.id, kind=kind, amount_minor=amount_minor, currency=currency, interval=interval)
    db.add(price)
    audit(db, actor_id, "price.set", "app", app.id, {"kind": kind, "amount_minor": amount_minor, "currency": currency})
    db.flush()
    # NAP-083: an approved version that was only waiting for a price goes to the store now
    waiting = latest_version(db, app.id)
    if waiting and waiting.state == "approved" and waiting.auto_release:
        try_auto_release(db, app, waiting, actor_id)
    return price


def set_auto_release(db: Session, app: App, v: AppVersion, on: bool, actor_id: uuid.UUID) -> AppVersion:
    if v.state not in ("draft", "submitted", "approved"):
        raise ApiError("invalid_state", 409)
    v.auto_release = bool(on)
    audit(db, actor_id, "version.auto_release", "app_version", v.id, {"on": v.auto_release})
    if v.auto_release:
        try_auto_release(db, app, v, actor_id)
    return v


def moderation_queue(db: Session) -> list[AppVersion]:
    return list(db.scalars(select(AppVersion).where(AppVersion.state == "submitted").order_by(AppVersion.submitted_at)))


def app_counts(db: Session, dev_id: uuid.UUID) -> int:
    return db.scalar(select(func.count()).select_from(App).where(App.developer_id == dev_id)) or 0
