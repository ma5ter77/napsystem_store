from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import ratelimit
from ..errors import ApiError
from ..models import App, AppVersion, Asset, Category, Price, User
from ..services import currency
from ..services import billing, reviews
from ..services.assets import limits_public, receive_upload, signed_url
from ..services.common import get_translations, localized
from ..storage import get_storage, verify_signed
from .deps import DB, P, as_uuid, locale_of, page

router = APIRouter(tags=["catalog"])


def price_view(p: Price) -> dict:
    return {"id": str(p.id), "kind": p.kind, "amount_minor": p.amount_minor, "currency": p.currency,
            "interval": p.interval, "trial_days": p.trial_days if p.kind == "subscription" else 0}


def preload_cards(db, apps: list[App]) -> dict:
    """NAP-050: everything the catalog cards need in a handful of queries instead of ~6 per app."""
    from sqlalchemy.orm import selectinload
    ids = [a.id for a in apps]
    vids = [a.published_version_id for a in apps if a.published_version_id]
    versions = {v.id: v for v in db.scalars(select(AppVersion).where(AppVersion.id.in_(vids))
                                            .options(selectinload(AppVersion.sessions)))} if vids else {}
    prices: dict = {}
    for pr in db.scalars(select(Price).where(Price.app_id.in_(ids), Price.active.is_(True))) if ids else []:
        prices.setdefault(pr.app_id, []).append(pr)
    return {"versions": versions, "translations": get_translations(db, "app", ids),
            "categories": {c.id: c for c in db.scalars(select(Category).where(Category.kind == "app"))},
            "prices": prices}


def app_card(db, app: App, locale: str, detail: bool = False, user_id=None, rating: dict | None = None,
             pre: dict | None = None) -> dict:
    if pre is not None:
        v = pre["versions"].get(app.published_version_id) if app.published_version_id else None
        tr = pre["translations"].get(app.id)
        cat = pre["categories"].get(app.category_id) if app.category_id else None
        all_prices = pre["prices"].get(app.id, [])
    else:
        v = db.get(AppVersion, app.published_version_id) if app.published_version_id else None
        tr = get_translations(db, "app", [app.id]).get(app.id)
        cat = db.get(Category, app.category_id) if app.category_id else None
        all_prices = list(db.scalars(select(Price).where(Price.app_id == app.id, Price.active.is_(True))))
    prices = [price_view(p) for p in currency.choose(all_prices)]  # NAP-012: the buyer's currency when available
    loc = localized(tr, locale)
    out = {"id": str(app.id), "slug": app.slug, "author": app.author_name, "cover_hue": app.cover_hue,
           "title": loc.get("title"), "subtitle": loc.get("subtitle"), "lang": loc["lang"],
           "translation_status": loc["translation_status"], "fallback": loc["fallback"],
           "category": {"slug": cat.slug, "name": cat.names.get(locale)} if cat else None,
           "content_language": v.content_language if v else None, "prices": prices,
           "currencies": sorted({p.currency for p in all_prices}),
           "sales_status": app.sales_status, "is_demo": app.is_demo,
           "sessions_count": len(v.sessions) if v else 0,
           "total_duration_seconds": round(sum(s.duration_seconds for s in v.sessions), 1) if v else 0,
           "requirements": v.requirements if v else {}, "version": v.version_label if v else None,
           "has_public_preview": app.public_preview_asset_id is not None,
           "cover_url": cover_url(db, app),
           "rating": rating if rating is not None else reviews.summaries(db, [app.id])[app.id]}
    if detail:
        out["rating_summary"] = reviews.summary(db, app)
        out["description"] = loc.get("description")
        out["program"] = [{"key": s.key, "position": s.position,
                           "title": s.titles.get(locale) or next(iter(s.titles.values())),
                           "title_lang": locale if locale in s.titles else next(iter(s.titles)),
                           "duration_seconds": s.duration_seconds,
                           "layouts": ["headphones", *sorted((s.variants or {}).keys())]}
                          for s in (v.sessions if v else [])]
        # NAP-108: equipment this app has versions for (every session has the headphones track)
        out["layouts"] = sorted({k for s in (v.sessions if v else []) for k in (s.variants or {})} | {"headphones"},
                                key=["headphones", "acoustic_3_0", "surround_5_1", "surround_7_1"].index)
        out["questionnaires"] = {"pre": v.questionnaire_pre, "post": v.questionnaire_post} if v else None
        out["dsp_profile"] = (v.manifest or {}).get("dsp") if v else None
        out["signal"] = _signal(db, out["dsp_profile"])
        out["recommended_kit"] = None
        if app.recommended_kit_product_id:
            from ..models import Product
            kit = db.get(Product, app.recommended_kit_product_id)
            out["recommended_kit"] = {"slug": kit.slug}
        if user_id:
            out["access"] = billing.access_for(db, user_id, app.id)
    return out


def _signal(db, dsp: dict | None) -> dict | None:
    """NAP-094: the carriers of the app's DSP profile for the chart on the app page (L, R, difference over time)."""
    if not dsp or not dsp.get("profile") or dsp["profile"] == "none":
        return None
    from ..models import DspProfile
    prof = db.scalar(select(DspProfile).where(DspProfile.slug == dsp["profile"],
                                              DspProfile.version == dsp.get("profile_version")))
    if not prof or "carrier_center_hz" not in prof.params or "beat_hz" not in prof.params:
        return None
    pr = prof.params
    return {"carrier_center_hz": float(pr["carrier_center_hz"]), "beat_hz": float(pr["beat_hz"]),
            "beat_curve": [[float(a), float(b)] for a, b in pr.get("beat_curve") or []]}


@router.get("/categories")
def categories(request: Request, kind: str = "app", db=DB):
    loc = locale_of(request)
    rows = db.scalars(select(Category).where(Category.kind == kind).order_by(Category.position))
    return {"items": [{"slug": c.slug, "name": c.names.get(loc) or c.names.get("ru")} for c in rows]}


@router.get("/catalog/apps")
def catalog(request: Request, q: str | None = None, category: str | None = None, price: str | None = None,
            content_language: str | None = None, min_rating: float | None = Query(None, ge=1, le=5),
            sort: str | None = None, limit: int = Query(20, le=100), offset: int = Query(0, ge=0), db=DB):
    loc = locale_of(request)
    apps = list(db.scalars(select(App).where(App.published_version_id.isnot(None), App.sales_status == "active")
                           .order_by(App.created_at)))
    ratings = reviews.summaries(db, [a.id for a in apps])
    pre = preload_cards(db, apps)
    cards = [app_card(db, a, loc, rating=ratings[a.id], pre=pre) for a in apps]
    if min_rating:  # only apps whose average is shown (enough ratings) and high enough
        cards = [c for c in cards if c["rating"]["average"] is not None and c["rating"]["average"] >= min_rating]
    if sort == "rating":
        cards.sort(key=lambda c: (c["rating"]["average"] is not None, c["rating"]["average"] or 0,
                                  c["rating"]["count"]), reverse=True)
    elif sort == "new":
        cards.reverse()
    if category:
        cards = [c for c in cards if c["category"] and c["category"]["slug"] == category]
    if price in ("free", "one_time", "subscription"):
        cards = [c for c in cards if any(p["kind"] == price for p in c["prices"])]
    if content_language:
        cards = [c for c in cards if c["content_language"] == content_language]
    if q:  # NAP-028: full-text search with RU/EN morphology; without an explicit sort the best matches go first
        from ..services.search import rank_apps
        ranks = rank_apps(db, [a.id for a in apps], q)
        if ranks is not None:
            cards = [c for c in cards if uuid.UUID(c["id"]) in ranks]
            if not sort:
                cards.sort(key=lambda c: -ranks[uuid.UUID(c["id"])])
    return page(cards[offset:offset + limit], len(cards), limit, offset)


def cover_url(db, app: App) -> str | None:
    """NAP-025: public URL of the moderated cover; the sha in the query makes it cacheable for good."""
    if not app.cover_asset_id:
        return None
    a = db.get(Asset, app.cover_asset_id)
    return f"/api/v1/catalog/apps/{app.slug}/cover?v={a.sha256[:12]}" if a and a.sha256 else None


@router.get("/catalog/apps/{slug}/cover")
def app_cover(slug: str, db=DB):
    """Cover of a published app — public like the store card itself; drafts and other files are never exposed."""
    app = db.scalar(select(App).where(App.slug == slug))
    if not app or not app.published_version_id or app.deleted_at or not app.cover_asset_id:
        raise ApiError("not_found", 404, entity="cover")
    a = db.get(Asset, app.cover_asset_id)
    st = get_storage()
    headers = {"Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"}
    path = st.local_path(a.storage_key)
    if path:
        if not os.path.exists(path):
            raise ApiError("not_found", 404, entity="cover")
        return FileResponse(path, media_type="image/webp", headers=headers)
    body, _, length = st.open_range(a.storage_key, None)
    return StreamingResponse(body.iter_chunks(1 << 16), media_type="image/webp",
                             headers=headers | {"Content-Length": str(length)})


@router.get("/catalog/apps/{slug}")
def app_detail(slug: str, request: Request, p=P, db=DB):
    app = db.scalar(select(App).where(App.slug == slug))
    if not app or not app.published_version_id or app.deleted_at:  # deleted by a moderator: gone from the store
        raise ApiError("not_found", 404, entity="app")
    return app_card(db, app, locale_of(request), detail=True, user_id=p.id)


class ViewIn(BaseModel):
    visitor: str | None = Field(None, max_length=64)  # ignored: nothing is stored on the device (NAP-062)


@router.post("/catalog/apps/{slug}/view", status_code=204)
def app_view(slug: str, request: Request, body: ViewIn | None = None, p=P, db=DB):
    """NAP-027: the app page reports one view (browser only, so bots without JS are not counted)."""
    ratelimit.check(request, "app_view", user_id=p.id)
    app = db.scalar(select(App).where(App.slug == slug))
    if not app or not app.published_version_id or app.deleted_at:
        raise ApiError("not_found", 404, entity="app")
    from ..services.analytics import record_view
    # cookieless: a signed-in user counts once; a guest is a hash of address + browser + day (never stored as such,
    # only folded into the HyperLogLog sketch), so no identifier lives on the device and none on the server
    from datetime import date as _date
    ip = ratelimit.who(request)
    record_view(app.id, f"u:{p.id}" if p.id else f"a:{ip}|{request.headers.get('user-agent', '')[:200]}|{_date.today()}")
    return Response(status_code=204)


@router.get("/catalog/apps/{slug}/preview")
def app_preview(slug: str, db=DB):
    """Public, explicitly published fragment (<= 30 s) — never the full delivery file."""
    app = db.scalar(select(App).where(App.slug == slug))
    if not app or not app.public_preview_asset_id or not app.published_version_id:
        raise ApiError("not_found", 404, entity="preview")
    a = db.get(Asset, app.public_preview_asset_id)
    return {"url": signed_url(a, f"{slug}-preview.flac"), "content_type": a.content_type}


@router.get("/converter/limits")
def converter_limits():
    return limits_public()


@router.put("/uploads/{token}")
async def upload(token: str, request: Request, db=DB):
    ratelimit.check(request, "upload_chunk")
    a = await receive_upload(db, token, request.stream(), request.headers.get("content-range"))
    if isinstance(a, dict):  # chunk accepted, more to come
        return a
    return {"asset_id": str(a.id), "size_bytes": a.size_bytes, "sha256": a.sha256, "status": a.status,
            "probe": a.probe}


@router.get("/files/{token}")
def file(token: str, request: Request):
    payload = verify_signed(token)
    if payload is None:
        raise ApiError("link_expired", 410)
    st = get_storage()
    path = st.local_path(payload["k"])
    headers = {"Cache-Control": "private, max-age=60", "Accept-Ranges": "bytes",
               "Content-Disposition": f'inline; filename="{payload.get("fn", "file")}"'}
    if path is None:  # S3-compatible private bucket: stream through the API (Range supported)
        try:
            body, content_range, length = st.open_range(payload["k"], request.headers.get("range"))
        except Exception:
            raise ApiError("not_found", 404, entity="file")
        if content_range:
            headers["Content-Range"] = content_range
        headers["Content-Length"] = str(length)
        return StreamingResponse(body.iter_chunks(1 << 20), status_code=206 if content_range else 200,
                                 media_type=payload.get("ct", "application/octet-stream"), headers=headers)
    if not os.path.exists(path):
        raise ApiError("not_found", 404, entity="file")
    # FileResponse supports HTTP Range requests (audio seeking)
    return FileResponse(path, media_type=payload.get("ct", "application/octet-stream"),
                        headers={"Cache-Control": "private, max-age=60",
                                 "Content-Disposition": f'inline; filename="{payload.get("fn", "file")}"'})
