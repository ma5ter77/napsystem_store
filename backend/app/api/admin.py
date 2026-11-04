from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..config import get_settings
from ..db import utcnow
from ..dsp.engine import Profile
from ..errors import ApiError
from ..models import (App, AppVersion, Asset, AuditLog, Category, ConversionJob, DigitalOrder, DspProfile,
                      Entitlement, HardwareOrder, LedgerEntry, Notification, Outbox, Product, Refund, RoleGrant, Seller, SellerMember,
                      User, WebhookInbox)
from ..models.system import ROLES
from ..services import apps as appsvc
from ..services import billing
from ..services.assets import signed_url
from ..services.common import audit
from .deps import DB, P, as_uuid, idem, locale_of
from .developer import version_view
from .hardware import order_view

router = APIRouter(tags=["moderation/admin"])


# ------------------------------------------------------------------ moderation

@router.get("/moderation/queue")
def queue(request: Request, p=P, db=DB):
    p.require_role("moderator", "admin")
    from ..services.version_diff import version_changes
    out = []
    for v in appsvc.moderation_queue(db):
        app = db.get(App, v.app_id)
        live = db.get(AppVersion, app.published_version_id) if app.published_version_id else None
        out.append({"app": {"id": str(app.id), "slug": app.slug, "author": app.author_name,
                            "title": appsvc.app_title(app, v, locale_of(request)),
                            "price_set": "price" not in appsvc.release_blockers(db, app, v),
                            "auto_release": v.auto_release},
                    # NAP-116: an update of an app on sale, or its first release
                    "live_label": live.version_label if live else None,
                    "changes": len(version_changes(live, v)),
                    "version": version_view(db, v, locale_of(request))})
    return {"items": out}


@router.get("/moderation/versions/{version_id}")
def mod_version(version_id: str, request: Request, p=P, db=DB):
    p.require_role("moderator", "admin")
    v = db.get(AppVersion, as_uuid(version_id, "version"))
    if not v:
        raise ApiError("not_found", 404, entity="version")
    app = db.get(App, v.app_id)
    sessions = []
    for s in v.sessions:
        j = db.get(ConversionJob, s.conversion_job_id)
        src = db.get(Asset, j.source_asset_id)
        from ..services.conversion import studio_info
        layouts = []   # NAP-113: the moderator hears and checks every version the listener can get
        for name, x in sorted((s.variants or {}).items()):
            vj = db.get(ConversionJob, uuid.UUID(x["conversion_job_id"]))
            layouts.append({"layout": name, "channels": x["channels"], "channel_order": x["channel_order"],
                            "qa": (vj.report or {}).get("qa"), "studio": studio_info(vj),
                            "own_files": {ch: (db.get(Asset, uuid.UUID(aid)).original_name if aid else None)
                                          for ch, aid in (vj.stems or {}).items()},
                            "delivery_url": signed_url(db.get(Asset, uuid.UUID(x["delivery_asset_id"])), f"{s.key}-{name}.flac")})
        sessions.append({"key": s.key, "titles": s.titles, "qa": (j.report or {}).get("qa"),
                         "params": (j.report or {}).get("params"), "profile": j.profile_snapshot,
                         "studio": studio_info(j), "layouts": layouts,
                         "source_url": signed_url(src, src.original_name or "source"),
                         "delivery_url": signed_url(db.get(Asset, s.delivery_asset_id), f"{s.key}.flac")})
    # NAP-116: which version is in the store now and what this one changes compared with it
    from ..services.version_diff import live_summary, version_changes
    live = db.get(AppVersion, app.published_version_id) if app.published_version_id else None
    return {"app": {"id": str(app.id), "slug": app.slug, "author": app.author_name, "cover_hue": app.cover_hue,
                    "title": appsvc.app_title(app, v, locale_of(request))},
            "version": version_view(db, v, locale_of(request)), "manifest": v.manifest, "sessions": sessions,
            "live": live_summary(live), "is_live": bool(live and live.id == v.id),
            "changes": version_changes(live, v),
            "integrity": appsvc.verify_integrity(db, app, v) if v.manifest else None}


class DecisionIn(BaseModel):
    decision: str
    comment: str = ""


@router.post("/moderation/versions/{version_id}/decision")
def decide(version_id: str, body: DecisionIn, request: Request, p=P, db=DB):
    u = p.require_role("moderator", "admin")
    v = db.get(AppVersion, as_uuid(version_id, "version"), with_for_update=True)
    if not v:
        raise ApiError("not_found", 404, entity="version")
    appsvc.moderate(db, v, u.id, body.decision, body.comment)
    db.commit()
    return version_view(db, v, locale_of(request))


@router.get("/moderation/products")
def product_queue(request: Request, p=P, db=DB):
    p.require_role("moderator", "admin")
    from ..services import products as prod
    return {"items": prod.moderation_queue(db, locale_of(request))}


@router.get("/moderation/products/{product_id}")
def product_for_moderation(product_id: str, request: Request, p=P, db=DB):
    p.require_role("moderator", "admin")
    from ..services import products as prod
    x = db.get(Product, as_uuid(product_id, "product"))
    if not x:
        raise ApiError("not_found", 404, entity="product")
    v = prod.view(db, x, locale_of(request))
    from ..models import Seller
    v["seller"] = db.get(Seller, x.seller_id).name
    return v


@router.post("/moderation/products/{product_id}/decision")
def decide_product(product_id: str, body: DecisionIn, request: Request, p=P, db=DB):
    u = p.require_role("moderator", "admin")
    from ..services import products as prod
    x = db.get(Product, as_uuid(product_id, "product"), with_for_update=True)
    if not x:
        raise ApiError("not_found", 404, entity="product")
    prod.decide(db, u, x, body.decision, body.comment)
    db.commit()
    return {"status": x.status}


# ------------------------------------------------------------------ admin

def admin(p):
    return p.require_role("admin")


@router.get("/admin/users")
def users(q: str | None = None, p=P, db=DB):
    admin(p)
    stmt = select(User).order_by(User.created_at.desc()).limit(100)
    if q:
        stmt = stmt.where(User.email.ilike(f"%{q}%"))
    return {"items": [{"id": str(u.id), "email": u.email, "display_name": u.display_name, "locale": u.locale,
                       "roles": sorted(u.role_names), "deleted": u.deleted_at is not None} for u in db.scalars(stmt)]}


class RoleIn(BaseModel):
    role: str
    seller_id: str | None = None


@router.post("/admin/users/{user_id}/roles")
def grant_role(user_id: str, body: RoleIn, p=P, db=DB):
    a = admin(p)
    u = db.get(User, as_uuid(user_id, "user"))
    if not u or body.role not in ROLES or body.role == "device":
        raise ApiError("validation_failed", 422)
    if body.role not in u.role_names:
        db.add(RoleGrant(user_id=u.id, role=body.role, granted_by=a.id))
    if body.role in ("seller", "fulfillment"):
        if not body.seller_id:
            raise ApiError("validation_failed", 422, details={"field": "seller_id"})
        sid = as_uuid(body.seller_id, "seller")
        if not db.scalar(select(SellerMember).where(SellerMember.seller_id == sid, SellerMember.user_id == u.id,
                                                    SellerMember.role == body.role)):
            db.add(SellerMember(seller_id=sid, user_id=u.id, role=body.role))
    audit(db, a.id, "role.granted", "user", u.id, {"role": body.role, "seller_id": body.seller_id})
    db.commit()
    return {"roles": sorted(db.get(User, u.id).role_names | {body.role})}


@router.delete("/admin/users/{user_id}/roles/{role}")
def revoke_role(user_id: str, role: str, p=P, db=DB):
    a = admin(p)
    g = db.scalar(select(RoleGrant).where(RoleGrant.user_id == as_uuid(user_id, "user"), RoleGrant.role == role,
                                          RoleGrant.revoked_at.is_(None)))
    if not g:
        raise ApiError("not_found", 404, entity="role")
    g.revoked_at = utcnow()
    audit(db, a.id, "role.revoked", "user", user_id, {"role": role})
    db.commit()
    return {"revoked": True}


@router.get("/admin/profiles")
def list_profiles(p=P, db=DB):
    admin(p)
    rows = db.scalars(select(DspProfile).order_by(DspProfile.slug, DspProfile.version))
    return {"items": [{"id": str(x.id), "slug": x.slug, "version": x.version, "status": x.status, "names": x.names,
                       "params": x.params, "created_at": x.created_at.isoformat()} for x in rows]}


class ProfileIn(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,63}$")
    names: dict
    descriptions: dict = {}
    params: dict


@router.post("/admin/profiles", status_code=201)
def create_profile(body: ProfileIn, p=P, db=DB):
    """Editing a profile always creates a new version; arbitrary DSP code/filters are never accepted."""
    a = admin(p)
    allowed = {"carrier_center_hz", "beat_hz", "source_gain_db", "tone_gain_dbfs", "fade_in_seconds",
               "fade_out_seconds"}
    # NAP-021: an optional frequency transition [[t_seconds, beat_hz], ...] on top of the fixed parameters
    if set(body.params) - {"beat_curve"} != allowed:
        raise ApiError("validation_failed", 422, details={"field": "params", "allowed": sorted(allowed | {"beat_curve"})})
    if "beat_curve" in body.params and not body.params["beat_curve"]:
        body.params.pop("beat_curve")
    last = db.scalar(select(func.max(DspProfile.version)).where(DspProfile.slug == body.slug)) or 0
    try:
        Profile.from_dict({"slug": body.slug, "version": last + 1, **body.params})
    except (ValueError, TypeError) as e:
        raise ApiError("validation_failed", 422, details={"reason": str(e)})
    prof = DspProfile(slug=body.slug, version=last + 1, names=body.names, descriptions=body.descriptions,
                      params=body.params, created_by=a.id)
    db.add(prof)
    audit(db, a.id, "dsp_profile.version_created", "dsp_profile", f"{body.slug}@{last + 1}", body.params)
    db.commit()
    return {"id": str(prof.id), "slug": prof.slug, "version": prof.version}


@router.post("/admin/profiles/{profile_id}/retire")
def retire_profile(profile_id: str, p=P, db=DB):
    a = admin(p)
    prof = db.get(DspProfile, as_uuid(profile_id, "profile"))
    prof.status = "retired"
    audit(db, a.id, "dsp_profile.retired", "dsp_profile", f"{prof.slug}@{prof.version}")
    db.commit()
    return {"status": "retired"}


@router.get("/admin/audit")
def audit_log(action: str | None = None, limit: int = 100, p=P, db=DB):
    admin(p)
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(min(limit, 500))
    if action:
        stmt = stmt.where(AuditLog.action.like(f"{action}%"))
    return {"items": [{"action": x.action, "entity_type": x.entity_type, "entity_id": x.entity_id,
                       "actor_id": str(x.actor_id) if x.actor_id else None, "data": x.data,
                       "request_id": x.request_id, "at": x.created_at.isoformat()} for x in db.scalars(stmt)]}


class RefundIn(BaseModel):
    order_id: str
    amount_minor: int
    reason: str = ""


@router.post("/admin/refunds/digital", status_code=201)
def refund(body: RefundIn, p=P, db=DB, key=Depends(idem)):
    a = admin(p)
    if not key:
        raise ApiError("idempotency_key_required", 422)
    r = billing.request_refund(db, a.id, as_uuid(body.order_id, "order"), body.amount_minor, body.reason, key)
    db.commit()
    billing.dispatch_outbox()
    db.refresh(r)
    return {"id": str(r.id), "status": r.status, "amount_minor": r.amount_minor}


@router.get("/admin/refund-requests")
def refund_requests(status: str = "requested", p=P, db=DB):
    """NAP-024: buyer refund requests (requested / pending / succeeded / failed / rejected / all)."""
    admin(p)
    stmt = select(Refund).where(Refund.context == "digital", Refund.lines["source"].astext == "buyer_request")
    if status != "all":
        stmt = stmt.where(Refund.status == status)
    items = []
    for r in db.scalars(stmt.order_by(Refund.created_at.desc()).limit(200)):
        o = db.get(DigitalOrder, r.order_id)
        buyer = db.get(User, o.buyer_id)
        items.append(billing.refund_request_view(r) | {
            "order": {"id": str(o.id), "app_slug": db.get(App, o.app_id).slug, "kind": o.kind,
                      "amount_minor": o.amount_minor, "refunded_minor": o.refunded_minor, "status": o.status,
                      "paid_at": o.paid_at.isoformat() if o.paid_at else None},
            "buyer": {"id": str(buyer.id), "email": buyer.email}})
    return {"items": items}


class RefundDecisionIn(BaseModel):
    approve: bool
    note: str = ""
    amount_minor: int | None = None


@router.post("/admin/refund-requests/{refund_id}/decision")
def decide_refund_request(refund_id: str, body: RefundDecisionIn, p=P, db=DB):
    a = admin(p)
    r = billing.decide_refund_request(db, a.id, as_uuid(refund_id, "refund_request"), body.approve, body.note,
                                      body.amount_minor)
    db.commit()
    billing.dispatch_outbox()
    db.refresh(r)
    return billing.refund_request_view(r)


class RevokeIn(BaseModel):
    user_id: str
    app_id: str
    reason: str = Field(min_length=3)


@router.post("/admin/entitlements/revoke")
def revoke_access(body: RevokeIn, p=P, db=DB):
    """Restricting already-paid access is a separate, audited action (not a side effect of withdrawal)."""
    a = admin(p)
    n = 0
    for e in db.scalars(select(Entitlement).where(Entitlement.user_id == as_uuid(body.user_id),
                                                  Entitlement.app_id == as_uuid(body.app_id),
                                                  Entitlement.status == "active")):
        e.status, e.revoked_reason, e.revoked_at = "revoked", "admin", utcnow()
        n += 1
    audit(db, a.id, "entitlement.revoked", "app", body.app_id, {"user": body.user_id, "reason": body.reason})
    db.commit()
    return {"revoked": n}


@router.get("/admin/finance")
def finance(p=P, db=DB):
    admin(p)
    devs = db.scalars(select(User).join(RoleGrant, RoleGrant.user_id == User.id).where(RoleGrant.role == "developer",
                                                                                        RoleGrant.revoked_at.is_(None)))
    rows = db.execute(select(LedgerEntry.account, LedgerEntry.currency, func.sum(LedgerEntry.amount_minor))
                      .group_by(LedgerEntry.account, LedgerEntry.currency).order_by(LedgerEntry.account)).all()
    return {"developers": [{"id": str(d.id), "name": d.display_name, "summary": billing.finance_summary(db, d.id)}
                           for d in devs],
            "ledger_balances": [{"account": a, "currency": c, "balance_minor": int(s)} for a, c, s in rows],
            "provider": get_settings().payment_provider}


class PayoutIn(BaseModel):
    developer_id: str
    currency: str


@router.post("/admin/payouts", status_code=201)
def payout(body: PayoutIn, p=P, db=DB):
    a = admin(p)
    po = billing.create_payout(db, a.id, as_uuid(body.developer_id, "user"), body.currency)
    db.commit()
    billing.dispatch_outbox()
    db.refresh(po)
    return {"id": str(po.id), "status": po.status, "amount_minor": po.amount_minor, "mock": po.mock}


@router.get("/admin/orders")
def orders(request: Request, p=P, db=DB):
    admin(p)
    rows = db.scalars(select(HardwareOrder).order_by(HardwareOrder.created_at.desc()).limit(100))
    return {"items": [order_view(db, o, locale_of(request), for_buyer=False) for o in rows]}


@router.get("/admin/sellers")
def sellers(p=P, db=DB):
    admin(p)
    return {"items": [{"id": str(s.id), "name": s.name, "kind": s.kind, "country": s.country, "status": s.status}
                      for s in db.scalars(select(Seller))]}


@router.get("/admin/ops")
def ops(p=P, db=DB):
    admin(p)
    return {"webhooks_unprocessed": db.scalar(select(func.count()).select_from(WebhookInbox)
                                              .where(WebhookInbox.processed_at.is_(None))),
            "outbox_pending": db.scalar(select(func.count()).select_from(Outbox).where(Outbox.sent_at.is_(None))),
            "jobs": dict(db.execute(select(ConversionJob.state, func.count()).group_by(ConversionJob.state)).all()),
            "failed_webhooks": [{"source": w.source, "event_id": w.event_id, "type": w.event_type,
                                 "attempts": w.attempts, "error": w.last_error}
                                for w in db.scalars(select(WebhookInbox).where(WebhookInbox.processed_at.is_(None))
                                                    .limit(20))],
            "emails_failed": db.scalar(select(func.count()).select_from(Notification).where(
                Notification.emailed_at.is_(None), Notification.email_error.isnot(None))),
            "backup": _backup()}


def _backup() -> dict | None:
    from ..main import backup_status
    b = backup_status()
    if b:
        b["stale"] = b["age_seconds"] > get_settings().backup_max_age_hours * 3600 or b["status"] != "ok"
    return b


@router.post("/admin/ops/run")
def run_ops(p=P):
    admin(p)
    from ..services.conversion import recover_expired_leases
    from ..services.hardware import expire_reservations
    return {"inbox_processed": billing.process_inbox(), "outbox_sent": billing.dispatch_outbox(),
            "reservations_expired": expire_reservations(), "leases_recovered": recover_expired_leases(),
            "reconciled": billing.reconcile_attempts()}


@router.get("/admin/i18n/missing")
def i18n_missing(p=P):
    admin(p)
    from ..i18n import missing_keys
    return missing_keys()
