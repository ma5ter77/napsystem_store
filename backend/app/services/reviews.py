"""Ratings and reviews of apps (NAP-065) and products (NAP-079).

Rules (decisions of 31.10.2026): apps — only users with access may rate, paid apps additionally need one completed
session; products — only buyers whose parcel with the product was delivered (verified purchase). The developer or
seller cannot rate own items and cannot edit, hide, delete or reset users' ratings and reviews — only reply
publicly or report. Reviews with links, contacts or forbidden words wait for a moderator.
"""
from __future__ import annotations

import re
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import utcnow
from ..errors import ApiError
from ..models import (App, AppVersion, HardwareOrder, HardwareOrderItem, Parcel, ParcelItem, PlaySession, Price,
                      Product, Review, ReviewHelpful, ReviewReply, ReviewReport, SellerMember, Sku, User)
from . import billing
from .common import audit, notify

URL_RE = re.compile(r"(https?://|www\.|\b[a-z0-9-]+\.(com|ru|net|org|io|rs|me|app|shop|online|site)\b)", re.I)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE_RE = re.compile(r"(?:\+?\d[\s()\-]*){9,}")
# a short, conservative list; anything flagged waits for a person, nothing is silently dropped
FORBIDDEN = re.compile(r"\b(бля\w*|сука|хуй\w*|пизд\w*|ебат\w*|fuck\w*|shit|cunt|bitch)\b", re.I)
MEDICAL = re.compile(r"\b(вылеч\w*|излеч\w*|лечит|исцел\w*|cures?|heals?|treats? (?:depression|anxiety|insomnia))\b", re.I)
REPORT_REASONS = ("spam", "abuse", "personal_data", "advertising", "medical_claims", "other")
SORTS = ("helpful", "new", "high", "low")


def flagged(text: str, *, reply: bool = False) -> list[str]:
    """Reasons a text needs a moderator before it is shown."""
    out = []
    if URL_RE.search(text):
        out.append("link")
    if EMAIL_RE.search(text) or PHONE_RE.search(text):
        out.append("contacts")
    if FORBIDDEN.search(text):
        out.append("forbidden_words")
    if reply and MEDICAL.search(text):
        out.append("medical_claims")
    return out


Target = App | Product


def _col(t: Target):
    return Review.app_id if isinstance(t, App) else Review.product_id


def _target_of(db: Session, r: Review) -> Target:
    return db.get(App, r.app_id) if r.app_id else db.get(Product, r.product_id)


def is_owner(db: Session, user_id: uuid.UUID, t: Target) -> bool:
    if isinstance(t, App):
        return t.developer_id == user_id
    return db.scalar(select(func.count()).select_from(SellerMember).where(
        SellerMember.seller_id == t.seller_id, SellerMember.user_id == user_id)) > 0


def _target_view(db: Session, t: Target) -> dict:
    if isinstance(t, App):
        return {"kind": "app", "id": str(t.id), "slug": t.slug, "author": t.author_name}
    return {"kind": "product", "id": str(t.id), "slug": t.slug, "author": f"{t.brand} {t.model}"}


def delivered_skus(db: Session, user_id: uuid.UUID, product: Product) -> list[uuid.UUID]:
    """SKUs of this product the user received in a delivered parcel (or kit components bought for this kit)."""
    rows = db.execute(select(HardwareOrderItem.sku_id, Sku.product_id, HardwareOrder.bom_snapshot)
                      .join(ParcelItem, ParcelItem.order_item_id == HardwareOrderItem.id)
                      .join(Parcel, Parcel.id == ParcelItem.parcel_id)
                      .join(HardwareOrder, HardwareOrder.id == HardwareOrderItem.order_id)
                      .join(Sku, Sku.id == HardwareOrderItem.sku_id)
                      .where(HardwareOrder.buyer_id == user_id, Parcel.status == "delivered")).all()
    out = [sku for sku, pid, _ in rows if pid == product.id]
    if not out and product.kind == "kit_component":  # a kit built from components has no SKU of its own
        for sku, _, snap in rows:
            if any(b.get("product_slug") == product.slug for b in (snap or [])):
                out.append(sku)
    return list(dict.fromkeys(out))


def _is_paid(db: Session, app: App) -> bool:
    kinds = {p.kind for p in db.scalars(select(Price).where(Price.app_id == app.id, Price.active.is_(True)))}
    return bool(kinds - {"free"})


def eligibility(db: Session, user: User | None, app: Target) -> dict:
    if user is None:
        return {"can_review": False, "reason": "login_required"}
    if isinstance(app, Product):
        if is_owner(db, user.id, app):
            return {"can_review": False, "reason": "own_product"}
        if not delivered_skus(db, user.id, app):
            return {"can_review": False, "reason": "no_delivered_purchase"}
        return {"can_review": True, "reason": None}
    if app.developer_id == user.id:
        return {"can_review": False, "reason": "own_app"}
    access = billing.access_for(db, user.id, app.id)
    if not access["has_access"]:
        return {"can_review": False, "reason": "no_access"}
    if access["source"] != "free" and _is_paid(db, app):
        done = db.scalar(select(func.count()).select_from(PlaySession).where(
            PlaySession.user_id == user.id, PlaySession.app_id == app.id, PlaySession.state == "completed"))
        if not done:
            return {"can_review": False, "reason": "no_completed_session"}
    return {"can_review": True, "reason": None}


def _clean(s: str | None, limit: int) -> str | None:
    s = (s or "").strip()
    if len(s) > limit:
        raise ApiError("validation_failed", 422, details={"field": "title" if limit == 100 else "body"})
    return s or None


def upsert(db: Session, user: User, app: Target, rating: int, title: str | None, body: str | None,
           lang: str) -> Review:
    el = eligibility(db, user, app)
    if not el["can_review"]:
        raise ApiError(f"review_{el['reason']}", 403)
    if not isinstance(rating, int) or not 1 <= rating <= 5:
        raise ApiError("validation_failed", 422, details={"field": "rating"})
    title, body = _clean(title, 100), _clean(body, 4000)
    if title and not body:
        raise ApiError("validation_failed", 422, details={"field": "body"})
    since = utcnow() - timedelta(days=1)
    recent = db.scalar(select(func.count()).select_from(Review).where(Review.user_id == user.id,
                                                                      Review.updated_at >= since))
    if recent >= get_settings().reviews_per_day:
        raise ApiError("review_rate_limited", 429)
    # load everything before add(): a query after add() would autoflush an incomplete row
    sku = db.get(Sku, delivered_skus(db, user.id, app)[0]) if isinstance(app, Product) else None
    v = db.get(AppVersion, app.published_version_id) if isinstance(app, App) else None
    r = db.scalar(select(Review).where(_col(app) == app.id, Review.user_id == user.id).with_for_update())
    reasons = flagged(f"{title or ''}\n{body or ''}")
    status = "pending" if reasons else "published"
    if r is None:
        r = Review(app_id=app.id, user_id=user.id) if isinstance(app, App) else \
            Review(product_id=app.id, user_id=user.id)
        db.add(r)
    if v is not None:
        r.version_id, r.version_label = v.id, v.version_label
    else:
        r.sku_id = sku.id
        r.version_label = (" / ".join(str(x) for x in (sku.variant or {}).values()) or sku.code)[:64]
    r.rating, r.title, r.body = rating, title, body
    r.lang = lang if lang in ("ru", "en") else "ru"
    r.status = status
    r.moderation_note = ",".join(reasons) or None
    r.updated_at = utcnow()
    db.flush()
    audit(db, user.id, "review.saved", "review", r.id, {"rating": rating, "status": status, "flags": reasons})
    return r


def delete_own(db: Session, user: User, app: Target) -> None:
    r = db.scalar(select(Review).where(_col(app) == app.id, Review.user_id == user.id))
    if r is None:
        raise ApiError("not_found", 404, entity="review")
    audit(db, user.id, "review.deleted", "review", r.id, {"rating": r.rating})
    db.delete(r)


def summary(db: Session, app: Target) -> dict:
    rows = db.execute(select(Review.rating, func.count()).where(_col(app) == app.id,
                                                                Review.status == "published")
                      .group_by(Review.rating)).all()
    dist = {str(k): 0 for k in range(1, 6)}
    for k, n in rows:
        dist[str(k)] = n
    count = sum(dist.values())
    avg = round(sum(int(k) * n for k, n in dist.items()) / count, 2) if count else None
    cur_count = cur_avg = None
    if isinstance(app, App) and app.published_version_id:
        cur_count, cur_avg = db.execute(select(func.count(), func.avg(Review.rating)).where(
            Review.app_id == app.id, Review.status == "published",
            Review.version_id == app.published_version_id)).one()
        cur_avg = round(float(cur_avg), 2) if cur_avg is not None else None
    minimum = get_settings().reviews_min_for_average
    return {"count": count, "average": avg if count >= minimum else None, "distribution": dist,
            "enough": count >= minimum, "min_for_average": minimum,
            "current_version": {"count": cur_count or 0,
                                "average": cur_avg if (cur_count or 0) >= minimum else None}}


def summaries(db: Session, app_ids: list[uuid.UUID], kind: str = "app") -> dict[uuid.UUID, dict]:
    """Average and count per app (or product) for catalog cards (one query)."""
    minimum = get_settings().reviews_min_for_average
    out = {a: {"count": 0, "average": None} for a in app_ids}
    if not app_ids:
        return out
    col = Review.app_id if kind == "app" else Review.product_id
    for app_id, n, avg in db.execute(select(col, func.count(), func.avg(Review.rating)).where(
            col.in_(app_ids), Review.status == "published").group_by(col)):
        out[app_id] = {"count": n, "average": round(float(avg), 2) if n >= minimum else None}
    return out


def view(db: Session, r: Review, viewer_id: uuid.UUID | None = None, *, with_status: bool = False) -> dict:
    author = db.get(User, r.user_id)
    out = {"id": str(r.id), "rating": r.rating, "title": r.title, "body": r.body, "lang": r.lang,
           "version": r.version_label, "helpful_count": r.helpful_count,
           "verified_purchase": r.product_id is not None,
           "author": (author.display_name if author and not author.deleted_at else None),
           "created_at": r.created_at.isoformat(), "updated_at": r.updated_at.isoformat(),
           "edited": (r.updated_at - r.created_at).total_seconds() > 60, "mine": viewer_id == r.user_id,
           "reply": None}
    if viewer_id:
        out["helpful_by_me"] = db.get(ReviewHelpful, (r.id, viewer_id)) is not None
    rp = r.reply
    if rp and (rp.status == "published" or with_status):
        out["reply"] = {"body": rp.body, "created_at": rp.created_at.isoformat(),
                        "updated_at": rp.updated_at.isoformat()}
        if with_status:
            out["reply"]["status"] = rp.status
    if with_status:
        out["status"] = r.status
        out["moderation_note"] = r.moderation_note
    return out


def list_public(db: Session, app: Target, sort: str, version: str | None, lang: str | None, rating: int | None,
                limit: int, offset: int, viewer_id: uuid.UUID | None) -> tuple[list[dict], int]:
    q = select(Review).where(_col(app) == app.id, Review.status == "published")
    if version:
        q = q.where(Review.version_label == version)
    if lang in ("ru", "en"):
        q = q.where(Review.lang == lang)
    if rating:
        q = q.where(Review.rating == rating)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    order = {"helpful": (Review.helpful_count.desc(), Review.created_at.desc()),
             "new": (Review.created_at.desc(),),
             "high": (Review.rating.desc(), Review.created_at.desc()),
             "low": (Review.rating.asc(), Review.created_at.desc())}[sort if sort in SORTS else "helpful"]
    rows = db.scalars(q.order_by(*order).limit(limit).offset(offset)).all()
    return [view(db, r, viewer_id) for r in rows], total


def versions_with_reviews(db: Session, app: Target) -> list[str]:
    return list(db.scalars(select(Review.version_label).where(_col(app) == app.id, Review.status == "published")
                           .group_by(Review.version_label).order_by(func.max(Review.created_at).desc())))


def toggle_helpful(db: Session, user: User, review_id: uuid.UUID) -> dict:
    r = db.get(Review, review_id, with_for_update=True)
    if r is None or r.status != "published":
        raise ApiError("not_found", 404, entity="review")
    if r.user_id == user.id:
        raise ApiError("review_own_vote", 403)
    mark = db.get(ReviewHelpful, (r.id, user.id))
    if mark:
        db.delete(mark)
        r.helpful_count = max(0, r.helpful_count - 1)
    else:
        db.add(ReviewHelpful(review_id=r.id, user_id=user.id))
        r.helpful_count += 1
    return {"helpful_count": r.helpful_count, "helpful_by_me": mark is None}


def report(db: Session, user: User, review_id: uuid.UUID, target: str, reason: str, comment: str | None) -> None:
    r = db.get(Review, review_id)
    if r is None or (target == "reply" and r.reply is None):
        raise ApiError("not_found", 404, entity="review")
    if target not in ("review", "reply") or reason not in REPORT_REASONS:
        raise ApiError("validation_failed", 422, details={"field": "reason" if target in ("review", "reply")
                                                         else "target"})
    exists = db.scalar(select(ReviewReport).where(ReviewReport.review_id == r.id, ReviewReport.target == target,
                                                  ReviewReport.reporter_id == user.id,
                                                  ReviewReport.resolved_at.is_(None)))
    if exists:
        return  # one open report per person is enough
    db.add(ReviewReport(review_id=r.id, target=target, reporter_id=user.id, reason=reason,
                        comment=(comment or "").strip()[:500] or None))
    audit(db, user.id, "review.reported", "review", r.id, {"target": target, "reason": reason})


def reply(db: Session, dev: User, review_id: uuid.UUID, body: str) -> ReviewReply:
    r = db.get(Review, review_id, with_for_update=True)
    if r is None:
        raise ApiError("not_found", 404, entity="review")
    app = _target_of(db, r)
    if not is_owner(db, dev.id, app):
        raise ApiError("not_found", 404, entity="review")
    body = (body or "").strip()
    if not body or len(body) > 4000:
        raise ApiError("validation_failed", 422, details={"field": "body"})
    reasons = flagged(body, reply=True)
    rp = r.reply
    if rp is None:
        rp = ReviewReply(review_id=r.id, developer_id=dev.id, body=body)
        db.add(rp)
        r.reply = rp
    rp.body = body
    rp.status = "pending" if reasons else "published"
    rp.moderation_note = ",".join(reasons) or None
    rp.updated_at = utcnow()
    db.flush()
    if rp.status == "published":
        _notify_reply(db, r, app)
    audit(db, dev.id, "review.replied", "review", r.id, {"status": rp.status, "flags": reasons})
    return rp


def _notify_reply(db: Session, r: Review, app: Target) -> None:
    author = db.get(User, r.user_id)
    if not author or author.deleted_at:
        return
    from .common import get_translations, localized
    kind = "app" if isinstance(app, App) else "product"
    title = localized(get_translations(db, kind, [app.id]).get(app.id), author.locale).get("title") or app.slug
    notify(db, author.id, author.locale, "review_reply", f"review_reply:{r.id}:{r.reply.updated_at.isoformat()}",
           app=title)


def delete_reply(db: Session, dev: User, review_id: uuid.UUID) -> None:
    r = db.get(Review, review_id)
    if r is None or r.reply is None or not is_owner(db, dev.id, _target_of(db, r)):
        raise ApiError("not_found", 404, entity="review")
    audit(db, dev.id, "review.reply_deleted", "review", r.id, {})
    r.reply = None


def developer_list(db: Session, dev: User, app_id: uuid.UUID | None, filt: str | None, rating: int | None,
                   version: str | None, limit: int, offset: int) -> dict:
    apps = {a.id: a for a in db.scalars(select(App).where(App.developer_id == dev.id))}
    ids = [app_id] if app_id in apps else list(apps)
    q = select(Review).where(Review.app_id.in_(ids), Review.status != "hidden")
    if filt == "unanswered":
        q = q.where(~Review.reply.has())
    if rating:
        q = q.where(Review.rating == rating)
    if version:
        q = q.where(Review.version_label == version)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(Review.created_at.desc()).limit(limit).offset(offset)).all()
    items = []
    for r in rows:
        d = view(db, r, dev.id, with_status=True)
        d["app"] = {"id": str(r.app_id), "slug": apps[r.app_id].slug}
        items.append(d)
    month = func.date_trunc("month", Review.created_at)
    trend = [{"month": m.date().isoformat()[:7], "count": n, "average": round(float(a), 2)}
             for m, n, a in db.execute(select(month, func.count(), func.avg(Review.rating))
                                       .where(Review.app_id.in_(ids), Review.status == "published")
                                       .group_by(month).order_by(month))]
    return {"items": items, "total": total, "limit": limit, "offset": offset, "trend": trend,
            "apps": [{"id": str(a.id), "slug": a.slug} for a in apps.values()],
            "summary": summary(db, apps[app_id]) if app_id in apps else None}


def moderation_queue(db: Session) -> list[dict]:
    open_reports = select(ReviewReport.review_id).where(ReviewReport.resolved_at.is_(None))
    rows = db.scalars(select(Review).where(
        (Review.status == "pending") | Review.id.in_(open_reports) |
        Review.reply.has(ReviewReply.status == "pending")).order_by(Review.updated_at)).all()
    out = []
    for r in rows:
        d = view(db, r, with_status=True)
        d["app"] = _target_view(db, _target_of(db, r))  # "app" kept for compatibility; see app.kind
        d["reports"] = [{"target": x.target, "reason": x.reason, "comment": x.comment,
                         "created_at": x.created_at.isoformat()}
                        for x in db.scalars(select(ReviewReport).where(ReviewReport.review_id == r.id,
                                                                       ReviewReport.resolved_at.is_(None)))]
        out.append(d)
    return out


def moderate(db: Session, moderator: User, review_id: uuid.UUID, target: str, action: str, note: str | None) -> None:
    r = db.get(Review, review_id, with_for_update=True)
    if r is None or (target == "reply" and r.reply is None):
        raise ApiError("not_found", 404, entity="review")
    if target not in ("review", "reply") or action not in ("publish", "hide"):
        raise ApiError("validation_failed", 422, details={"field": "action"})
    obj = r if target == "review" else r.reply
    was = obj.status
    obj.status = "published" if action == "publish" else "hidden"
    obj.moderation_note = (note or "").strip()[:300] or None
    for rep in db.scalars(select(ReviewReport).where(ReviewReport.review_id == r.id, ReviewReport.target == target,
                                                     ReviewReport.resolved_at.is_(None))):
        rep.resolved_at = utcnow()
    if target == "reply" and was != "published" and obj.status == "published":
        _notify_reply(db, r, _target_of(db, r))
    audit(db, moderator.id, f"review.moderated.{action}", "review", r.id, {"target": target, "note": note})


def seller_list(db: Session, user: User, product_id: uuid.UUID | None, filt: str | None, rating: int | None,
                limit: int, offset: int) -> dict:
    seller_ids = list(db.scalars(select(SellerMember.seller_id).where(SellerMember.user_id == user.id)))
    products = {p.id: p for p in db.scalars(select(Product).where(Product.seller_id.in_(seller_ids)))}
    ids = [product_id] if product_id in products else list(products)
    q = select(Review).where(Review.product_id.in_(ids), Review.status != "hidden")
    if filt == "unanswered":
        q = q.where(~Review.reply.has())
    if rating:
        q = q.where(Review.rating == rating)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(Review.created_at.desc()).limit(limit).offset(offset)).all()
    items = []
    for r in rows:
        d = view(db, r, user.id, with_status=True)
        d["app"] = {"id": str(r.product_id), "slug": products[r.product_id].slug}
        items.append(d)
    month = func.date_trunc("month", Review.created_at)
    trend = [{"month": m.date().isoformat()[:7], "count": n, "average": round(float(a), 2)}
             for m, n, a in db.execute(select(month, func.count(), func.avg(Review.rating))
                                       .where(Review.product_id.in_(ids), Review.status == "published")
                                       .group_by(month).order_by(month))]
    return {"items": items, "total": total, "limit": limit, "offset": offset, "trend": trend,
            "apps": [{"id": str(p.id), "slug": p.slug} for p in products.values()],
            "summary": summary(db, products[product_id]) if product_id in products else None}
