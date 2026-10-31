"""NAP-084: moderator catalog management (/moderation/catalog/...) and site content editing (/content-editor/...).

Moderators (and admins): products and apps of every owner — edit, take off sale, delete (soft, 30 days), restore.
Content editors (and admins): UI texts, e-mail templates and categories, with drafts, publication and rollback.
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..errors import ApiError
from ..i18n import catalog as server_catalog
from ..models import App, Category, Product
from ..services import catalog_admin as ca
from ..services import site_texts as st
from .deps import DB, P, as_uuid, locale_of

router = APIRouter(tags=["moderation/catalog", "content-editor"])


def mod(p):
    return p.require_role("moderator", "admin")


def editor(p):
    return p.require_role("content_editor", "admin")


class Reasoned(BaseModel):
    reason: str | None = Field(None, max_length=300)


class EditIn(Reasoned):
    data: dict = {}


# ------------------------------------------------------------------ products

def _product(db, product_id: str, lock=True) -> Product:
    x = db.get(Product, as_uuid(product_id, "product"), with_for_update=lock)
    if not x:
        raise ApiError("not_found", 404, entity="product")
    return x


@router.get("/moderation/catalog/products")
def products(request: Request, q: str | None = None, status: str | None = None, seller_id: str | None = None,
             limit: int = Query(100, le=200), offset: int = Query(0, ge=0), p=P, db=DB):
    mod(p)
    return ca.list_products(db, locale_of(request), q, status, seller_id, limit, offset)


@router.post("/moderation/catalog/products", status_code=201)
def create_product(body: dict, request: Request, p=P, db=DB):
    u = mod(p)
    x = ca.create_product(db, u, body)
    db.commit()
    return ca.product_view(db, x, locale_of(request))


@router.get("/moderation/catalog/products/{product_id}")
def product(product_id: str, request: Request, p=P, db=DB):
    mod(p)
    return ca.product_view(db, _product(db, product_id, lock=False), locale_of(request))


@router.put("/moderation/catalog/products/{product_id}")
def edit_product(product_id: str, body: EditIn, request: Request, p=P, db=DB):
    u = mod(p)
    x = _product(db, product_id)
    ca.edit_product(db, u, x, "fields", body.data, body.reason)
    db.commit()
    return ca.product_view(db, x, locale_of(request))


@router.put("/moderation/catalog/products/{product_id}/texts/{locale}")
def edit_product_texts(product_id: str, locale: str, body: EditIn, request: Request, p=P, db=DB):
    u = mod(p)
    x = _product(db, product_id)
    ca.edit_product(db, u, x, "texts", body.data, body.reason, locale=locale)
    db.commit()
    return ca.product_view(db, x, locale_of(request))


@router.post("/moderation/catalog/products/{product_id}/skus", status_code=201)
def add_product_sku(product_id: str, body: EditIn, request: Request, p=P, db=DB):
    u = mod(p)
    x = _product(db, product_id)
    ca.edit_product(db, u, x, "sku_add", body.data, body.reason)
    db.commit()
    db.refresh(x)
    return ca.product_view(db, x, locale_of(request))


@router.put("/moderation/catalog/products/{product_id}/skus/{sku_id}")
def edit_product_sku(product_id: str, sku_id: str, body: EditIn, request: Request, p=P, db=DB):
    u = mod(p)
    x = _product(db, product_id)
    ca.edit_product(db, u, x, "sku_update", body.data, body.reason, sku_id=as_uuid(sku_id, "sku"))
    db.commit()
    db.refresh(x)
    return ca.product_view(db, x, locale_of(request))


@router.post("/moderation/catalog/products/{product_id}/{action}")
def product_action(product_id: str, action: str, body: Reasoned, request: Request, p=P, db=DB):
    u = mod(p)
    x = _product(db, product_id)
    fn = {"publish": ca.publish_product, "unpublish": ca.unpublish_product, "delete": ca.delete_product}.get(action)
    if action == "restore":
        ca.restore_product(db, u, x)
    elif fn:
        fn(db, u, x, body.reason)
    else:
        raise ApiError("not_found", 404, entity="action")
    db.commit()
    return ca.product_view(db, x, locale_of(request))


# ------------------------------------------------------------------ apps

def _app(db, app_id: str, lock=True) -> App:
    x = db.get(App, as_uuid(app_id, "app"), with_for_update=lock)
    if not x:
        raise ApiError("not_found", 404, entity="app")
    return x


@router.get("/moderation/catalog/apps")
def apps(request: Request, q: str | None = None, status: str | None = None, limit: int = Query(100, le=200),
         offset: int = Query(0, ge=0), p=P, db=DB):
    mod(p)
    return ca.list_apps(db, locale_of(request), q, status, limit, offset)


@router.get("/moderation/catalog/apps/{app_id}")
def app(app_id: str, request: Request, p=P, db=DB):
    mod(p)
    return ca.app_view(db, _app(db, app_id, lock=False), locale_of(request))


@router.put("/moderation/catalog/apps/{app_id}")
def edit_app(app_id: str, body: EditIn, request: Request, p=P, db=DB):
    u = mod(p)
    x = _app(db, app_id)
    ca.edit_app(db, u, x, body.data, body.reason)
    db.commit()
    return ca.app_view(db, x, locale_of(request))


@router.post("/moderation/catalog/apps/{app_id}/{action}")
def app_action(app_id: str, action: str, body: Reasoned, request: Request, p=P, db=DB):
    u = mod(p)
    x = _app(db, app_id)
    if action == "stop-sales":
        ca.set_app_sales(db, u, x, "withdrawn", body.reason)
    elif action == "resume-sales":
        ca.set_app_sales(db, u, x, "active", body.reason)
    elif action == "delete":
        ca.delete_app(db, u, x, body.reason)
    elif action == "restore":
        ca.restore_app(db, u, x)
    else:
        raise ApiError("not_found", 404, entity="action")
    db.commit()
    return ca.app_view(db, x, locale_of(request))


# ------------------------------------------------------------------ site content

@router.get("/content/texts", tags=["content"])
def public_texts(locale: str = "ru", db=DB):
    """Published overrides of UI texts for one language (merged over the built-in dictionary by the site)."""
    if locale not in ("ru", "en"):
        raise ApiError("validation_failed", 422)
    return {"locale": locale, "texts": {k: v for k, v in st.published_map(db, locale).items()
                                        if not k.startswith("notifications.")}}


@router.get("/content-editor/texts")
def editor_texts(p=P, db=DB):
    editor(p)
    return {"items": st.list_texts(db)}


@router.get("/content-editor/mail-templates")
def mail_templates(p=P):
    """Built-in e-mail templates (server dictionary) so the editor can see and override them."""
    editor(p)
    out = {}
    for loc in ("ru", "en"):
        for k, v in server_catalog(loc).items():
            if k.startswith("notifications."):
                out.setdefault(k, {})[loc] = v
    return {"items": [{"key": k, **v} for k, v in sorted(out.items())]}


class TextIn(BaseModel):
    value: str = Field(max_length=5000)


@router.put("/content-editor/texts/{key}/{locale}")
def save_text(key: str, locale: str, body: TextIn, p=P, db=DB):
    u = editor(p)
    st.save_draft(db, u, key, locale, body.value)
    db.commit()
    return {"status": "draft"}


@router.post("/content-editor/texts/{key}/{locale}/{action}")
def text_action(key: str, locale: str, action: str, body: dict | None = None, p=P, db=DB):
    u = editor(p)
    if action == "publish":
        st.publish(db, u, key, locale)
    elif action == "discard":
        st.discard_draft(db, u, key, locale)
    elif action == "reset":
        st.reset(db, u, key, locale)
    elif action == "revert":
        st.revert(db, u, key, locale, as_uuid((body or {}).get("version_id", ""), "version"))
    else:
        raise ApiError("not_found", 404, entity="action")
    db.commit()
    return {"status": "ok"}


@router.get("/content-editor/texts/{key}/{locale}/history")
def text_history(key: str, locale: str, p=P, db=DB):
    editor(p)
    return {"items": st.history(db, key, locale)}


@router.get("/content-editor/categories")
def editor_categories(kind: str = "app", p=P, db=DB):
    editor(p)
    return {"items": st.categories(db, kind)}


@router.post("/content-editor/categories", status_code=201)
def create_category(body: dict, p=P, db=DB):
    u = editor(p)
    c = st.create_category(db, u, body)
    db.commit()
    return {"id": str(c.id)}


def _category(db, category_id: str) -> Category:
    c = db.get(Category, as_uuid(category_id, "category"))
    if not c:
        raise ApiError("not_found", 404, entity="category")
    return c


@router.put("/content-editor/categories/{category_id}")
def update_category(category_id: str, body: dict, p=P, db=DB):
    u = editor(p)
    st.update_category(db, u, _category(db, category_id), body)
    db.commit()
    return {"status": "ok"}


@router.delete("/content-editor/categories/{category_id}", status_code=204)
def delete_category(category_id: str, p=P, db=DB):
    u = editor(p)
    st.delete_category(db, u, _category(db, category_id))
    db.commit()
