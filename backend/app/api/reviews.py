"""Ratings and reviews API (NAP-065)."""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import ratelimit
from ..errors import ApiError
from ..models import App, Product, Review
from ..services import reviews as rv
from .deps import DB, P, as_uuid, locale_of

router = APIRouter(tags=["reviews"])


def _published_app(db, slug: str) -> App:
    app = db.scalar(select(App).where(App.slug == slug))
    if not app or not app.published_version_id:
        raise ApiError("not_found", 404, entity="app")
    return app


def _published_product(db, slug: str) -> Product:
    prod = db.scalar(select(Product).where(Product.slug == slug, Product.status == "published"))
    if not prod:
        raise ApiError("not_found", 404, entity="product")
    return prod


def _listing(db, p, app, sort, version, lang, rating, limit, offset) -> dict:
    items, total = rv.list_public(db, app, sort, version, lang, rating, limit, offset, p.id)
    mine = None
    if p.user:
        r = db.scalar(select(Review).where(rv._col(app) == app.id, Review.user_id == p.id))
        mine = rv.view(db, r, p.id, with_status=True) if r else None
    return {"items": items, "total": total, "limit": limit, "offset": offset, "summary": rv.summary(db, app),
            "versions": rv.versions_with_reviews(db, app), "eligibility": rv.eligibility(db, p.user, app),
            "mine": mine}


@router.get("/hardware/products/{slug}/reviews")
def list_product_reviews(slug: str, p=P, db=DB, sort: str = "helpful", version: str | None = None,
                         lang: str | None = None, rating: int | None = Query(None, ge=1, le=5),
                         limit: int = Query(10, ge=1, le=50), offset: int = Query(0, ge=0)):
    return _listing(db, p, _published_product(db, slug), sort, version, lang, rating, limit, offset)


@router.get("/catalog/apps/{slug}/reviews")
def list_reviews(slug: str, p=P, db=DB, sort: str = "helpful", version: str | None = None, lang: str | None = None,
                 rating: int | None = Query(None, ge=1, le=5), limit: int = Query(10, ge=1, le=50),
                 offset: int = Query(0, ge=0)):
    app = _published_app(db, slug)
    items, total = rv.list_public(db, app, sort, version, lang, rating, limit, offset, p.id)
    mine = None
    if p.user:
        r = db.scalar(select(Review).where(Review.app_id == app.id, Review.user_id == p.id))
        mine = rv.view(db, r, p.id, with_status=True) if r else None
    return {"items": items, "total": total, "limit": limit, "offset": offset, "summary": rv.summary(db, app),
            "versions": rv.versions_with_reviews(db, app), "eligibility": rv.eligibility(db, p.user, app),
            "mine": mine}


class ReviewIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    title: str | None = Field(None, max_length=100)
    body: str | None = Field(None, max_length=4000)
    lang: str | None = None


@router.put("/catalog/apps/{slug}/review")
def save_review(slug: str, body: ReviewIn, request: Request, p=P, db=DB):
    u = p.require_user()
    ratelimit.check(request, "review_write", u.id)
    app = _published_app(db, slug)
    r = rv.upsert(db, u, app, body.rating, body.title, body.body, body.lang or locale_of(request))
    db.commit()
    return rv.view(db, r, u.id, with_status=True)


@router.delete("/catalog/apps/{slug}/review", status_code=204)
def delete_review(slug: str, request: Request, p=P, db=DB):
    u = p.require_user()
    ratelimit.check(request, "review_write", u.id)
    rv.delete_own(db, u, _published_app(db, slug))
    db.commit()


@router.put("/hardware/products/{slug}/review")
def save_product_review(slug: str, body: ReviewIn, request: Request, p=P, db=DB):
    u = p.require_user()
    ratelimit.check(request, "review_write", u.id)
    r = rv.upsert(db, u, _published_product(db, slug), body.rating, body.title, body.body,
                  body.lang or locale_of(request))
    db.commit()
    return rv.view(db, r, u.id, with_status=True)


@router.delete("/hardware/products/{slug}/review", status_code=204)
def delete_product_review(slug: str, request: Request, p=P, db=DB):
    u = p.require_user()
    ratelimit.check(request, "review_write", u.id)
    rv.delete_own(db, u, _published_product(db, slug))
    db.commit()


@router.post("/reviews/{review_id}/helpful")
def helpful(review_id: str, p=P, db=DB):
    u = p.require_user()
    out = rv.toggle_helpful(db, u, as_uuid(review_id, "review"))
    db.commit()
    return out


class ReportIn(BaseModel):
    target: str = "review"
    reason: str
    comment: str | None = Field(None, max_length=500)


@router.post("/reviews/{review_id}/report", status_code=202)
def report(review_id: str, body: ReportIn, request: Request, p=P, db=DB):
    u = p.require_user()
    ratelimit.check(request, "review_report", u.id)
    rv.report(db, u, as_uuid(review_id, "review"), body.target, body.reason, body.comment)
    db.commit()
    return {"status": "received"}


# ------------------------------------------------------------------ developer

class ReplyIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


@router.get("/developer/reviews")
def developer_reviews(p=P, db=DB, app_id: str | None = None, filter: str | None = None,
                      rating: int | None = Query(None, ge=1, le=5), version: str | None = None,
                      limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    u = p.require_role("developer")
    p.require_scope("apps:read")
    return rv.developer_list(db, u, as_uuid(app_id, "app") if app_id else None, filter, rating, version, limit, offset)


@router.put("/developer/reviews/{review_id}/reply")
def save_reply(review_id: str, body: ReplyIn, request: Request, p=P, db=DB):
    u = p.require_role("developer")
    ratelimit.check(request, "review_write", u.id)
    p.require_scope("apps:write")
    rp = rv.reply(db, u, as_uuid(review_id, "review"), body.body)
    db.commit()
    return {"status": rp.status, "body": rp.body, "moderation_note": rp.moderation_note}


@router.delete("/developer/reviews/{review_id}/reply", status_code=204)
def remove_reply(review_id: str, p=P, db=DB):
    u = p.require_role("developer")
    p.require_scope("apps:write")
    rv.delete_reply(db, u, as_uuid(review_id, "review"))
    db.commit()


# ------------------------------------------------------------------ seller (NAP-079)

@router.get("/seller/reviews")
def seller_reviews(p=P, db=DB, product_id: str | None = None, filter: str | None = None,
                   rating: int | None = Query(None, ge=1, le=5), limit: int = Query(20, ge=1, le=100),
                   offset: int = Query(0, ge=0)):
    u = p.require_role("seller", "fulfillment")
    return rv.seller_list(db, u, as_uuid(product_id, "product") if product_id else None, filter, rating, limit,
                          offset)


@router.put("/seller/reviews/{review_id}/reply")
def seller_reply(review_id: str, body: ReplyIn, request: Request, p=P, db=DB):
    u = p.require_role("seller")
    ratelimit.check(request, "review_write", u.id)
    rp = rv.reply(db, u, as_uuid(review_id, "review"), body.body)
    db.commit()
    return {"status": rp.status, "body": rp.body, "moderation_note": rp.moderation_note}


@router.delete("/seller/reviews/{review_id}/reply", status_code=204)
def seller_remove_reply(review_id: str, p=P, db=DB):
    u = p.require_role("seller")
    rv.delete_reply(db, u, as_uuid(review_id, "review"))
    db.commit()


# ------------------------------------------------------------------ moderation

@router.get("/moderation/reviews")
def moderation_reviews(p=P, db=DB):
    p.require_role("moderator", "admin")
    return {"items": rv.moderation_queue(db)}


class DecisionIn(BaseModel):
    target: str = "review"
    action: str
    note: str | None = Field(None, max_length=300)


@router.post("/moderation/reviews/{review_id}/decision")
def moderation_decision(review_id: str, body: DecisionIn, p=P, db=DB):
    u = p.require_role("moderator", "admin")
    rv.moderate(db, u, as_uuid(review_id, "review"), body.target, body.action, body.note)
    db.commit()
    return {"status": "ok"}
