"""Moderator catalog management (NAP-084).

Products: create for any supplier, edit any card (a published one stays on sale; text edits go live at once), publish,
take off sale, soft delete with a reason, restore within 30 days.
Apps: edit store texts and category of a published app (the version manifest, sound and program stay untouched — only
the author changes those through a new version), stop/resume sales, soft delete (buyers keep access), restore.
Prices of apps are not changed by moderators (decision 31.10.2026).
Every action is audited with the reason; the owner (supplier members / app developer) gets a notification.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..errors import ApiError
from ..models import (App, AppVersion, AuditLog, Category, Product, Seller, SellerMember, Translation, User,
                      Warehouse)
from . import products as prod
from .common import audit, get_translations, notify, upsert_translation

RESTORE_DAYS = 30
APP_TEXT_FIELDS = (("title", 120), ("subtitle", 200), ("description", 5000))


def _reason(reason: str | None, required: bool = True) -> str:
    r = (reason or "").strip()
    if required and not r:
        raise ApiError("reason_required", 422, details={"fields": ["reason"]})
    return r[:300]


def _actor_names(db: Session, ids: set) -> dict:
    ids = {i for i in ids if i}
    return {u.id: u.display_name or u.email for u in db.scalars(select(User).where(User.id.in_(ids)))} if ids else {}


def history(db: Session, entity_type: str, entity_id, limit: int = 50) -> list[dict]:
    """Full change log of one product/app as the moderator sees it (who, what, reason)."""
    rows = list(db.scalars(select(AuditLog).where(AuditLog.entity_type == entity_type,
                                                  AuditLog.entity_id == str(entity_id))
                           .order_by(AuditLog.created_at.desc()).limit(limit)))
    names = _actor_names(db, {r.actor_id for r in rows})
    return [{"action": r.action, "at": r.created_at.isoformat(), "actor": names.get(r.actor_id),
             "reason": (r.data or {}).get("reason") or (r.data or {}).get("comment") or None,
             "fields": (r.data or {}).get("fields")} for r in rows]


# ------------------------------------------------------------------ products

def _notify_seller(db: Session, p: Product, action: str, reason: str) -> None:
    n = db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.entity_type == "product",
                                                                    AuditLog.entity_id == str(p.id)))
    for uid, loc in db.execute(select(User.id, User.locale).join(SellerMember, SellerMember.user_id == User.id)
                               .where(SellerMember.seller_id == p.seller_id, SellerMember.role == "seller")):
        notify(db, uid, loc, "product_moderator_action", f"product:{p.id}:mod:{action}:{n}",
               product=f"{p.brand} {p.model}", action=action, reason=reason or "—")


def list_products(db: Session, locale: str, q: str | None, status: str | None, seller_id: str | None,
                  limit: int = 100, offset: int = 0) -> dict:
    stmt = select(Product)
    if status:
        stmt = stmt.where(Product.status == status)
    else:
        stmt = stmt.where(Product.status != "deleted")
    if seller_id:
        stmt = stmt.where(Product.seller_id == uuid.UUID(seller_id))
    if q:
        like = f"%{q.strip().lower()}%"
        ids = select(Translation.entity_id).where(Translation.entity_type == "product",
                                                  func.lower(Translation.data["title"].astext).like(like))
        stmt = stmt.where(or_(func.lower(Product.slug).like(like), func.lower(Product.brand).like(like),
                              func.lower(Product.model).like(like), Product.id.in_(ids)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = list(db.scalars(stmt.order_by(Product.created_at.desc()).limit(limit).offset(offset)))
    trs = get_translations(db, "product", [p.id for p in rows])
    sellers = {s.id: s.name for s in db.scalars(select(Seller))}
    items = []
    for p in rows:
        t = trs.get(p.id, {})
        loc = t.get(locale) or t.get("ru") or t.get("en")
        items.append({"id": str(p.id), "slug": p.slug, "status": p.status, "seller": sellers.get(p.seller_id),
                      "seller_id": str(p.seller_id), "brand": p.brand, "model": p.model,
                      "title": (loc.data.get("title") if loc else None) or f"{p.brand} {p.model}",
                      "skus": len(p.skus), "languages": sorted(t), "is_demo": p.is_demo,
                      "deleted_at": p.deleted_at.isoformat() if p.deleted_at else None,
                      "deleted_reason": p.deleted_reason, "restorable": _restorable(p.deleted_at)})
    return {"items": items, "total": total, "sellers": [{"id": str(k), "name": v} for k, v in sellers.items()]}


def _restorable(deleted_at) -> bool:
    return bool(deleted_at) and deleted_at > utcnow() - timedelta(days=RESTORE_DAYS)


def product_view(db: Session, p: Product, locale: str) -> dict:
    v = prod.view(db, p, locale)
    v["editable"] = p.status != "deleted"  # the moderator edits any card that is not deleted
    v["seller"] = db.get(Seller, p.seller_id).name
    v["seller_id"] = str(p.seller_id)
    v["deleted_at"] = p.deleted_at.isoformat() if p.deleted_at else None
    v["deleted_reason"] = p.deleted_reason
    v["restorable"] = _restorable(p.deleted_at)
    v["reason_required"] = reason_required(db, p)   # NAP-106: the editor asks for the reason before any change
    v["audit"] = history(db, "product", p.id)
    v["warehouses"] = [{"id": str(w.id), "name": w.name, "country": w.country}
                       for w in db.scalars(select(Warehouse).where(Warehouse.seller_id == p.seller_id))]
    return v


def create_product(db: Session, moderator: User, d: dict) -> Product:
    if not d.get("seller_id"):
        raise ApiError("validation_failed", 422, details={"fields": ["seller_id"]})
    sid = uuid.UUID(d["seller_id"])
    if not db.get(Seller, sid):
        raise ApiError("validation_failed", 422, details={"fields": ["seller_id"]})
    p = prod.create(db, moderator, {sid}, d)
    audit(db, moderator.id, "product.mod_created", "product", p.id, {"seller_id": str(sid)})
    return p


def reason_required(db: Session, p: Product) -> bool:
    """A moderator's edit of someone else's live card must say why (the owner is notified with the reason)."""
    own = db.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.entity_type == "product", AuditLog.entity_id == str(p.id), AuditLog.action == "product.mod_created"))
    return p.status == "published" and not own


def edit_product(db: Session, moderator: User, p: Product, kind: str, d: dict, reason: str | None,
                 locale: str | None = None, sku_id: uuid.UUID | None = None) -> None:
    """kind: fields | texts | sku_add | sku_update | photo_*. Editing someone else's live card needs a reason."""
    someone_elses_live = reason_required(db, p)
    r = _reason(reason, required=someone_elses_live)
    if kind == "fields":
        prod.update(db, moderator, p, d, force=True)
    elif kind == "texts":
        prod.save_texts(db, moderator, p, locale or "", d, force=True)
    elif kind == "sku_add":
        prod.add_sku(db, moderator, p, d, force=True)
    elif kind == "sku_update":
        prod.update_sku(db, moderator, p, sku_id, d, force=True)
    elif kind == "photo_add":  # NAP-047
        prod.add_photo(db, moderator, p, d["data"], d["filename"], force=True)
        d = {"photo": True}
    elif kind == "photo_remove":
        prod.remove_photo(db, moderator, p, d["asset_id"], force=True)
        d = {"photo": True}
    elif kind == "photo_order":
        prod.reorder_photos(db, moderator, p, d.get("ids") or [], force=True)
    else:
        raise ApiError("validation_failed", 422)
    audit(db, moderator.id, "product.mod_edited", "product", p.id,
          {"reason": r, "fields": [kind] + sorted(d.keys())[:15], **({"locale": locale} if locale else {})})
    if someone_elses_live:
        _notify_seller(db, p, "edited", r)


def publish_product(db: Session, moderator: User, p: Product, reason: str | None) -> Product:
    if p.status in ("published", "deleted"):
        raise ApiError("invalid_state", 409)
    missing = [c["key"] for c in prod.checks(db, p) if not c["ok"] and not c.get("optional")]
    if missing:
        raise ApiError("product_incomplete", 422, details={"missing": missing})
    p.status = "published"
    for tr in db.scalars(select(Translation).where(Translation.entity_type == "product", Translation.entity_id == p.id)):
        tr.status = "published"
    r = _reason(reason, required=False)
    audit(db, moderator.id, "product.published", "product", p.id, {"reason": r, "by": "moderator"})
    _notify_seller(db, p, "published", r)
    return p


def unpublish_product(db: Session, moderator: User, p: Product, reason: str | None) -> Product:
    if p.status not in ("published", "submitted"):
        raise ApiError("invalid_state", 409)
    r = _reason(reason)
    p.status = "draft"
    for tr in db.scalars(select(Translation).where(Translation.entity_type == "product", Translation.entity_id == p.id)):
        tr.status = "draft"
    audit(db, moderator.id, "product.unpublished", "product", p.id, {"reason": r})
    _notify_seller(db, p, "unpublished", r)
    return p


def delete_product(db: Session, moderator: User, p: Product, reason: str | None) -> Product:
    if p.status == "deleted":
        raise ApiError("invalid_state", 409)
    r = _reason(reason)
    p.deleted_prev_status, p.status = p.status, "deleted"
    p.deleted_at, p.deleted_reason = utcnow(), r
    audit(db, moderator.id, "product.deleted", "product", p.id, {"reason": r, "was": p.deleted_prev_status})
    _notify_seller(db, p, "deleted", r)
    return p


def restore_product(db: Session, moderator: User, p: Product) -> Product:
    if p.status != "deleted":
        raise ApiError("invalid_state", 409)
    if not _restorable(p.deleted_at):
        raise ApiError("restore_expired", 409, days=RESTORE_DAYS)
    p.status = p.deleted_prev_status or "draft"
    p.deleted_at = p.deleted_reason = p.deleted_prev_status = None
    audit(db, moderator.id, "product.restored", "product", p.id, {"status": p.status})
    _notify_seller(db, p, "restored", "")
    return p


# ------------------------------------------------------------------ apps

def _notify_dev(db: Session, app: App, action: str, reason: str) -> None:
    dev = db.get(User, app.developer_id)
    n = db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.entity_type == "app",
                                                                    AuditLog.entity_id == str(app.id)))
    trs = get_translations(db, "app", [app.id]).get(app.id, {})
    title = ((trs.get(dev.locale) or trs.get("ru") or trs.get("en")) and
             (trs.get(dev.locale) or trs.get("ru") or trs.get("en")).data.get("title")) or app.slug
    notify(db, dev.id, dev.locale, "app_moderator_action", f"app:{app.id}:mod:{action}:{n}",
           app=title, action=action, reason=reason or "—")


def _latest(db: Session, app_id) -> AppVersion | None:
    return db.scalars(select(AppVersion).where(AppVersion.app_id == app_id).order_by(AppVersion.number.desc())).first()


def list_apps(db: Session, locale: str, q: str | None, status: str | None, limit: int = 100, offset: int = 0) -> dict:
    stmt = select(App)
    if status == "deleted":
        stmt = stmt.where(App.deleted_at.isnot(None))
    else:
        stmt = stmt.where(App.deleted_at.is_(None))
        if status == "published":
            stmt = stmt.where(App.published_version_id.isnot(None), App.sales_status == "active")
        elif status == "withdrawn":
            stmt = stmt.where(App.sales_status == "withdrawn")
        elif status == "unpublished":
            stmt = stmt.where(App.published_version_id.is_(None))
    if q:
        like = f"%{q.strip().lower()}%"
        ids = select(Translation.entity_id).where(Translation.entity_type == "app",
                                                  func.lower(Translation.data["title"].astext).like(like))
        stmt = stmt.where(or_(func.lower(App.slug).like(like), func.lower(App.author_name).like(like), App.id.in_(ids)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = list(db.scalars(stmt.order_by(App.created_at.desc()).limit(limit).offset(offset)))
    trs = get_translations(db, "app", [a.id for a in rows])
    items = []
    for a in rows:
        t = trs.get(a.id, {})
        loc = t.get(locale) or t.get("ru") or t.get("en")
        v = _latest(db, a.id)
        title = (loc.data.get("title") if loc else None) or ((v.metadata_ or {}).get(locale) or {}).get("title") \
            or ((v.metadata_ or {}).get("ru") or {}).get("title") if v else None
        items.append({"id": str(a.id), "slug": a.slug, "title": title or a.slug, "author": a.author_name,
                      "published": a.published_version_id is not None, "sales_status": a.sales_status,
                      "latest_state": v.state if v else None, "latest_label": v.version_label if v else None,
                      "is_demo": a.is_demo, "deleted_at": a.deleted_at.isoformat() if a.deleted_at else None,
                      "deleted_reason": a.deleted_reason, "restorable": _restorable(a.deleted_at)})
    return {"items": items, "total": total}


def _live(db: Session, a: App) -> dict | None:
    """NAP-116: the version buyers get now (the latest one may be a newer draft or an update under review)."""
    lv = db.get(AppVersion, a.published_version_id) if a.published_version_id else None
    return {"id": str(lv.id), "label": lv.version_label,
            "published_at": lv.published_at.isoformat() if lv.published_at else None} if lv else None


def app_view(db: Session, a: App, locale: str) -> dict:
    trs = get_translations(db, "app", [a.id]).get(a.id, {})
    v = _latest(db, a.id)
    cat = db.get(Category, a.category_id) if a.category_id else None
    texts = {loc: {"title": tr.data.get("title"), "subtitle": tr.data.get("subtitle"),
                   "description": tr.data.get("description"), "status": tr.status} for loc, tr in trs.items()}
    return {"id": str(a.id), "slug": a.slug, "author": a.author_name, "category": cat.slug if cat else None,
            "published": a.published_version_id is not None, "sales_status": a.sales_status,
            "latest": {"id": str(v.id), "state": v.state, "label": v.version_label} if v else None,
            "texts": texts, "is_demo": a.is_demo, "cover_hue": a.cover_hue, "live": _live(db, a),
            "deleted_at": a.deleted_at.isoformat() if a.deleted_at else None, "deleted_reason": a.deleted_reason,
            "restorable": _restorable(a.deleted_at), "audit": history(db, "app", a.id)}


def edit_app(db: Session, moderator: User, a: App, d: dict, reason: str | None) -> App:
    if a.deleted_at:
        raise ApiError("invalid_state", 409)
    r = _reason(reason)
    changed = []
    if "category" in d and d["category"]:
        cat = db.scalar(select(Category).where(Category.kind == "app", Category.slug == d["category"]))
        if not cat:
            raise ApiError("validation_failed", 422, details={"fields": ["category"]})
        if cat.id != a.category_id:
            a.category_id = cat.id
            changed.append("category")
    trs = get_translations(db, "app", [a.id]).get(a.id, {})
    for loc in ("ru", "en"):
        if loc not in (d.get("texts") or {}):
            continue
        src = d["texts"][loc] or {}
        cur = dict(trs[loc].data) if loc in trs else {}
        bad = [k for k, lim in APP_TEXT_FIELDS if len((src.get(k) or "").strip()) > lim]
        if bad:
            raise ApiError("validation_failed", 422, details={"fields": bad})
        for k, _ in APP_TEXT_FIELDS:
            if k in src:
                cur[k] = (src.get(k) or "").strip()
        if not cur.get("title"):
            raise ApiError("validation_failed", 422, details={"fields": ["title"]})
        status = trs[loc].status if loc in trs else ("published" if a.published_version_id else "draft")
        upsert_translation(db, "app", a.id, loc, cur, status)
        changed.append(f"texts.{loc}")
    if not changed:
        raise ApiError("nothing_changed", 422)
    audit(db, moderator.id, "app.mod_edited", "app", a.id, {"reason": r, "fields": changed})
    _notify_dev(db, a, "edited", r)
    return a


def set_app_sales(db: Session, moderator: User, a: App, status: str, reason: str | None) -> App:
    if status not in ("active", "withdrawn") or a.deleted_at:
        raise ApiError("invalid_state", 409)
    r = _reason(reason, required=status == "withdrawn")
    a.sales_status = status
    audit(db, moderator.id, f"app.mod_sales_{status}", "app", a.id, {"reason": r})
    _notify_dev(db, a, "sales_stopped" if status == "withdrawn" else "sales_resumed", r)
    return a


def delete_app(db: Session, moderator: User, a: App, reason: str | None) -> App:
    if a.deleted_at:
        raise ApiError("invalid_state", 409)
    r = _reason(reason)
    a.deleted_prev_sales, a.sales_status = a.sales_status, "withdrawn"
    a.deleted_at, a.deleted_reason = utcnow(), r
    audit(db, moderator.id, "app.deleted", "app", a.id, {"reason": r})
    _notify_dev(db, a, "deleted", r)
    return a


def restore_app(db: Session, moderator: User, a: App) -> App:
    if not a.deleted_at:
        raise ApiError("invalid_state", 409)
    if not _restorable(a.deleted_at):
        raise ApiError("restore_expired", 409, days=RESTORE_DAYS)
    a.sales_status = a.deleted_prev_sales or "active"
    a.deleted_at = a.deleted_reason = a.deleted_prev_sales = None
    audit(db, moderator.id, "app.restored", "app", a.id, {})
    _notify_dev(db, a, "restored", "")
    return a
