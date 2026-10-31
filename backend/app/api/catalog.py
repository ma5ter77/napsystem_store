from __future__ import annotations

import os

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy import select

from ..errors import ApiError
from ..models import App, AppVersion, Asset, Category, Price, User
from ..services import billing
from ..services.assets import limits_public, receive_upload, signed_url
from ..services.common import get_translations, localized
from ..storage import get_storage, verify_signed
from .deps import DB, P, as_uuid, locale_of, page

router = APIRouter(tags=["catalog"])


def price_view(p: Price) -> dict:
    return {"id": str(p.id), "kind": p.kind, "amount_minor": p.amount_minor, "currency": p.currency,
            "interval": p.interval}


def app_card(db, app: App, locale: str, detail: bool = False, user_id=None) -> dict:
    v = db.get(AppVersion, app.published_version_id) if app.published_version_id else None
    tr = get_translations(db, "app", [app.id]).get(app.id)
    loc = localized(tr, locale)
    cat = db.get(Category, app.category_id) if app.category_id else None
    prices = [price_view(p) for p in db.scalars(select(Price).where(Price.app_id == app.id, Price.active.is_(True)))]
    out = {"id": str(app.id), "slug": app.slug, "author": app.author_name, "cover_hue": app.cover_hue,
           "title": loc.get("title"), "subtitle": loc.get("subtitle"), "lang": loc["lang"],
           "translation_status": loc["translation_status"], "fallback": loc["fallback"],
           "category": {"slug": cat.slug, "name": cat.names.get(locale)} if cat else None,
           "content_language": v.content_language if v else None, "prices": prices,
           "sales_status": app.sales_status, "is_demo": app.is_demo,
           "sessions_count": len(v.sessions) if v else 0,
           "total_duration_seconds": round(sum(s.duration_seconds for s in v.sessions), 1) if v else 0,
           "requirements": v.requirements if v else {}, "version": v.version_label if v else None,
           "has_public_preview": app.public_preview_asset_id is not None}
    if detail:
        out["description"] = loc.get("description")
        out["program"] = [{"key": s.key, "position": s.position,
                           "title": s.titles.get(locale) or next(iter(s.titles.values())),
                           "title_lang": locale if locale in s.titles else next(iter(s.titles)),
                           "duration_seconds": s.duration_seconds} for s in (v.sessions if v else [])]
        out["questionnaires"] = {"pre": v.questionnaire_pre, "post": v.questionnaire_post} if v else None
        out["dsp_profile"] = (v.manifest or {}).get("dsp") if v else None
        out["recommended_kit"] = None
        if app.recommended_kit_product_id:
            from ..models import Product
            kit = db.get(Product, app.recommended_kit_product_id)
            out["recommended_kit"] = {"slug": kit.slug}
        if user_id:
            out["access"] = billing.access_for(db, user_id, app.id)
    return out


@router.get("/categories")
def categories(request: Request, kind: str = "app", db=DB):
    loc = locale_of(request)
    rows = db.scalars(select(Category).where(Category.kind == kind).order_by(Category.position))
    return {"items": [{"slug": c.slug, "name": c.names.get(loc) or c.names.get("ru")} for c in rows]}


@router.get("/catalog/apps")
def catalog(request: Request, q: str | None = None, category: str | None = None, price: str | None = None,
            content_language: str | None = None, limit: int = Query(20, le=100), offset: int = Query(0, ge=0), db=DB):
    loc = locale_of(request)
    apps = list(db.scalars(select(App).where(App.published_version_id.isnot(None), App.sales_status == "active")
                           .order_by(App.created_at)))
    cards = [app_card(db, a, loc) for a in apps]
    if category:
        cards = [c for c in cards if c["category"] and c["category"]["slug"] == category]
    if price in ("free", "one_time", "subscription"):
        cards = [c for c in cards if any(p["kind"] == price for p in c["prices"])]
    if content_language:
        cards = [c for c in cards if c["content_language"] == content_language]
    if q:
        ql = q.lower().strip()
        trs = get_translations(db, "app", [a.id for a in apps])
        hay = {str(a.id): " ".join([a.slug, a.author_name] + [" ".join(str(x) for x in t.data.values())
                                                             for t in trs.get(a.id, {}).values()
                                                             if t.status == "published"]).lower() for a in apps}
        cards = [c for c in cards if ql in hay[c["id"]]]
    return page(cards[offset:offset + limit], len(cards), limit, offset)


@router.get("/catalog/apps/{slug}")
def app_detail(slug: str, request: Request, p=P, db=DB):
    app = db.scalar(select(App).where(App.slug == slug))
    if not app or not app.published_version_id:
        raise ApiError("not_found", 404, entity="app")
    return app_card(db, app, locale_of(request), detail=True, user_id=p.id)


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
