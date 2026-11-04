from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .. import ratelimit
from ..config import get_settings
from ..db import utcnow
from ..errors import ApiError
from ..models import (ApiKey, App, AppVersion, Asset, ConversionJob, DigitalOrder, DspProfile, ModerationDecision,
                      Price, RevenueShare, Subscription)
from ..security import API_SCOPES, generate_api_key
from ..services import apps as appsvc
from ..services import billing, conversion
from ..services.assets import create_upload, signed_url
from ..services.common import audit
from ..services.sessions import developer_analytics
from .deps import DB, P, as_uuid, idem, locale_of

router = APIRouter(prefix="/developer", tags=["developer"])


def dev(p, scope: str | None = None):
    u = p.require_role("developer")
    if scope:
        p.require_scope(scope)
    return u


def version_view(db, v: AppVersion, locale: str) -> dict:
    decisions = db.scalars(select(ModerationDecision).where(ModerationDecision.version_id == v.id)
                           .order_by(ModerationDecision.created_at)).all()
    return {"id": str(v.id), "number": v.number, "version_label": v.version_label, "state": v.state,
            "content_language": v.content_language, "metadata": v.metadata_, "requirements": v.requirements,
            "questionnaire_pre": v.questionnaire_pre, "questionnaire_post": v.questionnaire_post,
            "rights_confirmed": v.rights_confirmed_at is not None, "manifest_sha256": v.manifest_sha256,
            "sessions": [{"key": s.key, "position": s.position, "titles": s.titles,
                          "conversion_job_id": str(s.conversion_job_id), "duration_seconds": s.duration_seconds,
                          "sha256": s.sha256,
                          "variants": {k: x["conversion_job_id"] for k, x in (s.variants or {}).items()}}
                         for s in v.sessions],
            "moderation": [{"decision": d.decision, "comment": d.comment, "at": d.created_at.isoformat()}
                           for d in decisions],
            "auto_release": v.auto_release,
            "cover": ({"asset_id": str(v.cover_asset_id), "url": signed_url(db.get(Asset, v.cover_asset_id), "cover.webp")}
                      if v.cover_asset_id else None),
            "release_blockers": appsvc.release_blockers(db, db.get(App, v.app_id), v) if v.state == "approved" else [],
            "submitted_at": v.submitted_at.isoformat() if v.submitted_at else None,
            "published_at": v.published_at.isoformat() if v.published_at else None}


def job_view(db, j: ConversionJob, with_urls: bool = False) -> dict:
    src = db.get(Asset, j.source_asset_id)
    out = {"id": str(j.id), "app_id": str(j.app_id), "state": j.state, "stage_progress": j.stage_progress,
           "failed_stage": j.failed_stage, "layout": j.layout, "method": j.method,
           "channels": conversion.layout_channels(j.layout),
           "stems": {ch: {"id": aid, "name": (db.get(Asset, uuid.UUID(aid)).original_name if aid else None)}
                     for ch, aid in (j.stems or {}).items()},
           "attempt": j.attempt, "max_attempts": j.max_attempts, "error_code": j.error_code,
           "profile": j.profile_snapshot, "studio": conversion.studio_info(j),
           "source": {"id": str(src.id), "name": src.original_name or (conversion.studio_info(j) or {}).get("label"),
                                                     "probe": src.probe, "size_bytes": src.size_bytes},
           "retry_of": str(j.retry_of_id) if j.retry_of_id else None, "created_at": j.created_at.isoformat(),
           "started_at": j.started_at.isoformat() if j.started_at else None,
           "finished_at": j.finished_at.isoformat() if j.finished_at else None,
           "result": j.result, "report": (j.report or {}).get("qa"), "params": (j.report or {}).get("params")}
    if with_urls and j.state == "completed":
        out["urls"] = {"source": signed_url(src, src.original_name or "source"),
                       "delivery": signed_url(db.get(Asset, uuid.UUID(j.result["delivery"])), "result.flac"),
                       "preview": signed_url(db.get(Asset, uuid.UUID(j.result["preview"])), "preview.flac"),
                       "master": signed_url(db.get(Asset, uuid.UUID(j.result["master"])), "master.wav"),
                       "qa_report": signed_url(db.get(Asset, uuid.UUID(j.result["qa_report"])), "qa_report.json"),
                       "params": signed_url(db.get(Asset, uuid.UUID(j.result["params"])), "params.json")}
    elif with_urls:
        out["urls"] = {"source": signed_url(src, src.original_name or "source")} if src.status == "uploaded" else {}
    return out


class AppIn(BaseModel):
    slug: str
    category: str | None = None
    author_name: str = Field(min_length=1, max_length=120)
    metadata: dict
    content_language: str = "ru"


@router.get("/apps")
def my_apps(request: Request, p=P, db=DB):
    u = dev(p, "apps:read")
    out = []
    for a in db.scalars(select(App).where(App.developer_id == u.id).order_by(App.created_at.desc())):
        latest = appsvc.latest_version(db, a.id)
        title = (latest.metadata_.get(locale_of(request)) or next(iter(latest.metadata_.values()), {})).get("title") \
            if latest and latest.metadata_ else a.slug
        out.append({"id": str(a.id), "slug": a.slug, "title": title, "sales_status": a.sales_status,
                    "published_version_id": str(a.published_version_id) if a.published_version_id else None,
                    "latest_version": {"id": str(latest.id), "state": latest.state, "label": latest.version_label}
                    if latest else None, "cover_hue": a.cover_hue,
                    # NAP-025: the draft's own cover first, so the author sees the new cover before publishing
                    "cover_url": (signed_url(db.get(Asset, latest.cover_asset_id), "cover.webp")
                                  if latest and latest.cover_asset_id else None)})
    return {"items": out}


@router.post("/apps", status_code=201)
def create_app(body: AppIn, p=P, db=DB):
    u = dev(p, "apps:write")
    app = appsvc.create_app(db, u, body.slug, body.category, body.author_name, body.metadata, body.content_language)
    db.commit()
    return {"id": str(app.id), "slug": app.slug, "version_id": str(appsvc.latest_version(db, app.id).id)}


@router.get("/apps/{app_id}")
def get_app(app_id: str, request: Request, p=P, db=DB):
    u = dev(p, "apps:read")
    app = appsvc.own_app(db, as_uuid(app_id), u.id)
    versions = db.scalars(select(AppVersion).where(AppVersion.app_id == app.id).order_by(AppVersion.number.desc()))
    prices = db.scalars(select(Price).where(Price.app_id == app.id, Price.active.is_(True)))
    return {"id": str(app.id), "slug": app.slug, "author_name": app.author_name, "sales_status": app.sales_status,
            "published_version_id": str(app.published_version_id) if app.published_version_id else None,
            "public_preview": app.public_preview_asset_id is not None, "cover_hue": app.cover_hue,
            "versions": [version_view(db, v, locale_of(request)) for v in versions],
            "prices": [{"kind": x.kind, "amount_minor": x.amount_minor, "currency": x.currency, "interval": x.interval}
                       for x in prices]}


@router.patch("/apps/{app_id}/versions/{version_id}")
def update_version(app_id: str, version_id: str, body: dict, request: Request, p=P, db=DB):
    u = dev(p, "apps:write")
    app = appsvc.own_app(db, as_uuid(app_id), u.id)
    v = appsvc.editable_version(db, app, as_uuid(version_id))
    appsvc.update_version(db, app, v, body, u.id)
    db.commit()
    return version_view(db, v, locale_of(request))


class ProgramIn(BaseModel):
    sessions: list[dict]


@router.put("/apps/{app_id}/versions/{version_id}/program")
def set_program(app_id: str, version_id: str, body: ProgramIn, request: Request, p=P, db=DB):
    u = dev(p, "apps:write")
    app = appsvc.own_app(db, as_uuid(app_id), u.id)
    v = appsvc.editable_version(db, app, as_uuid(version_id))
    appsvc.set_program(db, app, v, body.sessions, u.id)
    db.commit()
    return version_view(db, v, locale_of(request))


@router.get("/apps/{app_id}/versions/{version_id}/manifest")
def manifest(app_id: str, version_id: str, p=P, db=DB):
    u = dev(p, "apps:read")
    app = appsvc.own_app(db, as_uuid(app_id), u.id)
    v = db.get(AppVersion, as_uuid(version_id))
    if not v or v.app_id != app.id:
        raise ApiError("not_found", 404, entity="version")
    m = v.manifest or appsvc.build_manifest(db, app, v)
    return {"manifest": m, "errors": appsvc.validate_manifest(m), "frozen": v.manifest is not None}


@router.post("/manifest/validate")
def validate_manifest(body: dict, p=P):
    dev(p)
    errs = appsvc.validate_manifest(body)
    return {"valid": not errs, "errors": errs}


@router.post("/apps/{app_id}/versions/{version_id}/submit")
def submit(app_id: str, version_id: str, request: Request, p=P, db=DB):
    u = dev(p, "versions:submit")
    app = appsvc.own_app(db, as_uuid(app_id), u.id)
    v = db.get(AppVersion, as_uuid(version_id), with_for_update=True)
    if not v or v.app_id != app.id:
        raise ApiError("not_found", 404, entity="version")
    appsvc.submit(db, app, v, u.id)
    db.commit()
    return version_view(db, v, locale_of(request))


@router.post("/apps/{app_id}/versions/{version_id}/withdraw")
def withdraw(app_id: str, version_id: str, request: Request, p=P, db=DB):
    u = dev(p, "versions:submit")
    app = appsvc.own_app(db, as_uuid(app_id), u.id)
    v = db.get(AppVersion, as_uuid(version_id), with_for_update=True)
    if not v or v.app_id != app.id:
        raise ApiError("not_found", 404, entity="version")
    appsvc.withdraw_version(db, app, v, u.id)
    db.commit()
    return version_view(db, v, locale_of(request))


@router.post("/apps/{app_id}/versions/{version_id}/publish")
def publish(app_id: str, version_id: str, request: Request, p=P, db=DB):
    u = dev(p, "versions:submit")
    app = appsvc.own_app(db, as_uuid(app_id), u.id)
    v = db.get(AppVersion, as_uuid(version_id), with_for_update=True)
    if not v or v.app_id != app.id:
        raise ApiError("not_found", 404, entity="version")
    appsvc.publish(db, app, v, u.id)
    db.commit()
    return version_view(db, v, locale_of(request))


class AutoReleaseIn(BaseModel):
    on: bool


@router.put("/apps/{app_id}/versions/{version_id}/auto-release")
def auto_release(app_id: str, version_id: str, body: AutoReleaseIn, request: Request, p=P, db=DB):
    """NAP-083: publish automatically after approval (default) or wait for the author's "Publish"."""
    u = dev(p, "versions:submit")
    app = appsvc.own_app(db, as_uuid(app_id), u.id)
    v = db.get(AppVersion, as_uuid(version_id), with_for_update=True)
    if not v or v.app_id != app.id:
        raise ApiError("not_found", 404, entity="version")
    appsvc.set_auto_release(db, app, v, body.on, u.id)
    db.commit()
    return version_view(db, v, locale_of(request))


@router.post("/apps/{app_id}/versions", status_code=201)
def new_version(app_id: str, request: Request, p=P, db=DB):
    u = dev(p, "apps:write")
    app = appsvc.own_app(db, as_uuid(app_id), u.id)
    v = appsvc.new_version(db, app, u.id)
    db.commit()
    return version_view(db, v, locale_of(request))


class PriceIn(BaseModel):
    kind: str
    amount_minor: int = 0
    currency: str = "EUR"
    interval: str | None = None


@router.put("/apps/{app_id}/price")
def set_price(app_id: str, body: PriceIn, p=P, db=DB):
    u = dev(p, "apps:write")
    app = appsvc.own_app(db, as_uuid(app_id), u.id)
    pr = appsvc.set_price(db, app, body.kind, body.amount_minor, body.currency, body.interval, u.id)
    db.commit()
    return {"id": str(pr.id), "kind": pr.kind, "amount_minor": pr.amount_minor, "currency": pr.currency,
            "interval": pr.interval}


class SalesIn(BaseModel):
    status: str


@router.put("/apps/{app_id}/sales")
def set_sales(app_id: str, body: SalesIn, p=P, db=DB):
    """Withdraw from / return to new sales. Existing paid libraries are NOT affected."""
    u = dev(p, "apps:write")
    app = appsvc.own_app(db, as_uuid(app_id), u.id)
    if body.status not in ("active", "withdrawn"):
        raise ApiError("validation_failed", 422)
    app.sales_status = body.status
    audit(db, u.id, "app.sales_status", "app", app.id, {"status": body.status})
    db.commit()
    return {"sales_status": app.sales_status}


class UploadIn(BaseModel):
    filename: str = Field(max_length=255)
    size_bytes: int
    sha256: str | None = None
    kind: str = "source"


@router.post("/apps/{app_id}/uploads", status_code=201)
def create_upload_ep(app_id: str, body: UploadIn, request: Request, p=P, db=DB):
    u = dev(p, "assets:write")
    ratelimit.check(request, "upload_ticket", u.id)
    app = appsvc.own_app(db, as_uuid(app_id), u.id)
    res = create_upload(db, u.id, app, body.kind, body.filename, body.size_bytes, body.sha256)
    db.commit()
    return res


@router.get("/apps/{app_id}/assets")
def list_assets(app_id: str, p=P, db=DB):
    u = dev(p, "apps:read")
    app = appsvc.own_app(db, as_uuid(app_id), u.id)
    rows = db.scalars(select(Asset).where(Asset.app_id == app.id, Asset.kind == "source")
                      .order_by(Asset.created_at.desc()))
    return {"items": [{"id": str(a.id), "name": a.original_name, "status": a.status, "size_bytes": a.size_bytes,
                       "sha256": a.sha256, "probe": a.probe, "created_at": a.created_at.isoformat()} for a in rows]}


@router.get("/profiles")
def profiles(request: Request, p=P, db=DB):
    dev(p)
    loc = locale_of(request)
    rows = db.scalars(select(DspProfile).where(DspProfile.status == "active").order_by(DspProfile.slug,
                                                                                       DspProfile.version))
    return {"items": [{"id": str(x.id), "slug": x.slug, "version": x.version, "name": x.names.get(loc),
                       "description": x.descriptions.get(loc), "params": x.params} for x in rows]}


class ConversionIn(BaseModel):
    app_id: str
    source_asset_id: str
    profile_id: str
    layout: str = "headphones"   # NAP-108: headphones | acoustic_3_0 | surround_5_1 | surround_7_1
    stems: dict[str, str] = {}   # {"FC": asset_id, ...}: the author's own file per channel
    # NAP-117: the beat from the track itself by default; "shift_layer" adds the carriers, "layer" is the MVP method
    method: str = "shift"
    beat: dict | None = None     # editor: {beat_hz, to_hz, over_minutes, layer_dbfs}


@router.post("/conversions", status_code=202)
def create_conversion(body: ConversionIn, request: Request, p=P, db=DB, key=Depends(idem)):
    u = dev(p, "conversions:write")
    ratelimit.check(request, "conversion", u.id)
    job, created = conversion.create_job(db, u.id, as_uuid(body.app_id), as_uuid(body.source_asset_id),
                                         as_uuid(body.profile_id, "profile"), key, body.layout,
                                         {ch: as_uuid(a, "asset") for ch, a in body.stems.items() if a},
                                         body.method, body.beat)
    db.commit()
    if created:
        from ..worker import dispatch_job
        dispatch_job(job.id)
    db.refresh(job)
    return job_view(db, job) | {"created": created}


class PreviewIn(BaseModel):
    app_id: str
    source_asset_id: str
    profile_id: str
    method: str = "shift"
    beat: dict | None = None
    start_seconds: float = 0.0
    seconds: float = 20.0


@router.post("/conversions/preview")
def preview_conversion(body: PreviewIn, request: Request, p=P, db=DB):
    """NAP-117: listen to a fragment with these beat settings before converting (nothing is stored)."""
    import json as _json
    import os as _os
    import tempfile
    from fastapi.responses import Response
    from ..dsp.media import MediaError
    from ..dsp.preview import preview_fragment
    from ..storage import get_storage
    u = dev(p, "conversions:write")
    ratelimit.check(request, "conversion", u.id)
    if body.method not in ("shift", "shift_layer", "layer"):
        raise ApiError("validation_failed", 422, details={"field": "method"})
    asset = conversion._own_source(db, u.id, as_uuid(body.app_id), as_uuid(body.source_asset_id))
    prof = db.get(DspProfile, as_uuid(body.profile_id, "profile"))
    if not prof or prof.status != "active":
        raise ApiError("profile_unavailable", 422)
    snap = conversion.edited_snapshot(conversion.profile_snapshot(prof), body.beat, body.method)
    with tempfile.TemporaryDirectory() as tmp:
        path = _os.path.join(tmp, "source.bin")
        get_storage().get_to_file(asset.storage_key, path)
        try:
            wav, info = preview_fragment(path, snap, body.method, body.start_seconds, body.seconds)
        except MediaError as e:
            raise ApiError("preview_failed", 422, details={"reason": e.code}) from None
    return Response(wav, media_type="audio/wav", headers={"Cache-Control": "no-store",
                                                         "X-Preview": _json.dumps(info, separators=(",", ":"))})


@router.get("/conversions")
def list_conversions(app_id: str | None = None, p=P, db=DB):
    u = dev(p, "conversions:read")
    q = select(ConversionJob).where(ConversionJob.owner_id == u.id)
    if app_id:
        q = q.where(ConversionJob.app_id == as_uuid(app_id))
    rows = db.scalars(q.order_by(ConversionJob.created_at.desc()).limit(100))
    return {"items": [job_view(db, j) for j in rows]}


@router.get("/conversions/{job_id}")
def get_conversion(job_id: str, p=P, db=DB):
    u = dev(p, "conversions:read")
    j = db.get(ConversionJob, as_uuid(job_id))
    if not j or j.owner_id != u.id:
        raise ApiError("not_found", 404, entity="conversion")
    return job_view(db, j, with_urls=True)


@router.post("/conversions/{job_id}/cancel")
def cancel_conversion(job_id: str, p=P, db=DB):
    u = dev(p, "conversions:write")
    j = conversion.cancel_job(db, as_uuid(job_id), u.id)
    db.commit()
    return job_view(db, j)


@router.post("/conversions/{job_id}/retry", status_code=202)
def retry_conversion(job_id: str, p=P, db=DB):
    u = dev(p, "conversions:write")
    j = conversion.retry_job(db, as_uuid(job_id), u.id)
    db.commit()
    from ..worker import dispatch_job
    dispatch_job(j.id)
    return job_view(db, j)


@router.post("/apps/{app_id}/public-preview")
def publish_preview(app_id: str, body: dict, p=P, db=DB):
    """Explicitly publish the <=30 s preview of one completed conversion as the public fragment."""
    u = dev(p, "apps:write")
    app = appsvc.own_app(db, as_uuid(app_id), u.id)
    j = db.get(ConversionJob, as_uuid(body.get("conversion_job_id", "")))
    if not j or j.app_id != app.id or j.state != "completed":
        raise ApiError("conversion_not_completed", 409)
    app.public_preview_asset_id = uuid.UUID(j.result["preview"])
    audit(db, u.id, "app.public_preview_published", "app", app.id)
    db.commit()
    return {"public_preview": True}


@router.get("/finance")
def finance(p=P, db=DB):
    u = dev(p, "analytics:read")
    orders = db.scalars(select(DigitalOrder).where(DigitalOrder.developer_id == u.id,
                                                   DigitalOrder.status != "pending")
                        .order_by(DigitalOrder.created_at.desc()).limit(50))
    from ..models import Payout
    payouts = db.scalars(select(Payout).where(Payout.developer_id == u.id).order_by(Payout.created_at.desc()))
    active_subs = db.scalar(select(func.count()).select_from(Subscription).join(App, App.id == Subscription.app_id)
                            .where(App.developer_id == u.id, Subscription.status == "active")) or 0
    return {"summary": billing.finance_summary(db, u.id), "active_subscriptions": active_subs,
            "commission_bps": get_settings().platform_commission_bps,
            "hold_days": get_settings().payout_hold_days,
            "orders": [{"id": str(o.id), "app_id": str(o.app_id), "kind": o.kind, "amount_minor": o.amount_minor,
                        "refunded_minor": o.refunded_minor, "currency": o.currency, "status": o.status,
                        "commission_bps": o.commission_bps,
                        "created_at": o.created_at.isoformat()} for o in orders],
            "payouts": [{"id": str(x.id), "amount_minor": x.amount_minor, "currency": x.currency, "status": x.status,
                         "mock": x.mock, "created_at": x.created_at.isoformat()} for x in payouts]}


class ConnectIn(BaseModel):
    country: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")


@router.post("/payouts/connect")
def payouts_connect(body: ConnectIn, request: Request, p=P, db=DB):
    """NAP-011: start or continue payout onboarding with the payment provider (Stripe Connect Express)."""
    u = dev(p, "analytics:read")
    user = db.merge(u)
    from ..adapters.payments import ProviderUnavailable, get_payment_provider
    base = f"{get_settings().public_base_url}/{locale_of(request)}/developer?tab=finance"
    try:
        acct, url = get_payment_provider().connect_onboarding(
            user_id=str(user.id), email=user.email, country=body.country, existing=user.payout_account,
            refresh_url=base + "&connect=retry", return_url=base + "&connect=done")
    except ProviderUnavailable as e:
        raise ApiError("payment_provider_unavailable", 503, reason=str(e))
    if user.payout_account != acct:
        user.payout_account = acct
        audit(db, user.id, "payout_account.linked", "user", user.id, {"provider": get_payment_provider().name})
        db.commit()
    return {"url": url}


@router.get("/payouts/account")
def payouts_account(p=P, db=DB):
    u = dev(p, "analytics:read")
    if not u.payout_account:
        return {"connected": False}
    from ..adapters.payments import ProviderUnavailable, get_payment_provider
    try:
        st = get_payment_provider().connect_status(u.payout_account)
    except ProviderUnavailable as e:
        raise ApiError("payment_provider_unavailable", 503, reason=str(e))
    return {"connected": True, **st}


@router.get("/analytics/apps/{app_id}")
def app_analytics(app_id: str, days: int = 30, p=P, db=DB):
    """NAP-027: funnel, weekly trend and retention for one app (aggregates only, small groups hidden)."""
    from ..services import analytics as an
    u = dev(p, "analytics:read")
    a = db.get(App, as_uuid(app_id, "app"))
    if not a or a.developer_id != u.id:
        raise ApiError("not_found", 404, entity="app")
    if days not in an.PERIODS:
        raise ApiError("validation_failed", 422, details={"fields": ["days"]})
    return {"app_id": str(a.id), "slug": a.slug} | an.app_report(db, a, days)


@router.get("/analytics")
def analytics(p=P, db=DB):
    u = dev(p, "analytics:read")
    out = []
    for a in db.scalars(select(App).where(App.developer_id == u.id)):
        sales = db.scalar(select(func.count()).select_from(DigitalOrder).where(
            DigitalOrder.app_id == a.id, DigitalOrder.status.in_(("paid", "partially_refunded", "refunded")))) or 0
        out.append({"app_id": str(a.id), "slug": a.slug, "paid_orders": sales, "usage": developer_analytics(db, a)})
    return {"items": out}


class KeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    scopes: list[str]


@router.get("/keys")
def keys(p=P, db=DB):
    u = p.require_role("developer")
    if p.method != "session":
        raise ApiError("forbidden", 403)
    rows = db.scalars(select(ApiKey).where(ApiKey.user_id == u.id).order_by(ApiKey.created_at.desc()))
    return {"items": [{"id": str(k.id), "name": k.name, "prefix": k.prefix, "scopes": k.scopes,
                       "created_at": k.created_at.isoformat(),
                       "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
                       "revoked": k.revoked_at is not None} for k in rows], "available_scopes": list(API_SCOPES)}


@router.post("/keys", status_code=201)
def create_key(body: KeyIn, p=P, db=DB):
    u = p.require_role("developer")
    if p.method != "session":  # keys are managed only from the cabinet, never by another key
        raise ApiError("forbidden", 403)
    if not body.scopes or not set(body.scopes) <= set(API_SCOPES):
        raise ApiError("validation_failed", 422, details={"field": "scopes"})
    full, prefix, h = generate_api_key()
    k = ApiKey(user_id=u.id, name=body.name, prefix=prefix, key_hash=h, scopes=sorted(set(body.scopes)))
    db.add(k)
    audit(db, u.id, "api_key.created", "api_key", prefix, {"scopes": k.scopes})
    db.commit()
    return {"id": str(k.id), "key": full, "prefix": prefix, "scopes": k.scopes,
            "note": "shown once; store it server-side only"}


@router.delete("/keys/{key_id}")
def revoke_key(key_id: str, p=P, db=DB):
    u = p.require_role("developer")
    if p.method != "session":
        raise ApiError("forbidden", 403)
    k = db.get(ApiKey, as_uuid(key_id))
    if not k or k.user_id != u.id:
        raise ApiError("not_found", 404, entity="api_key")
    k.revoked_at = k.revoked_at or utcnow()
    audit(db, u.id, "api_key.revoked", "api_key", k.prefix)
    db.commit()
    return {"revoked": True}
