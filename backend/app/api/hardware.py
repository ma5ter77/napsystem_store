from __future__ import annotations

import secrets
import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import Response as RawResponse
from pydantic import BaseModel
from sqlalchemy import select

from .. import ratelimit
from ..adapters.shipping import get_shipping_adapter
from ..errors import ApiError
from ..models import (App, Asset, FreightQuote, HardwareOrder, Parcel, Product, ReturnRequest, Seller, ShippingRate,
                      TrackingEvent, Warehouse)
from ..services import hardware as hw
from ..services import hardware_orders as ho
from ..services.addresses import country_rules
from ..services.assets import signed_url
from ..services.common import get_translations, localized
from .deps import DB, P, as_uuid, idem, locale_of, page

router = APIRouter(prefix="/hardware", tags=["hardware"])
CART_COOKIE = "nap_cart"


@router.get("/products")
def products(request: Request, category: str | None = None, q: str | None = None, kind: str | None = None,
             limit: int = Query(50, le=100), offset: int = Query(0, ge=0), db=DB):
    items, total = hw.list_products(db, locale_of(request), category, q, kind, limit, offset)
    return page(items, total, limit, offset)


@router.get("/products/{slug}")
def product(slug: str, request: Request, db=DB):
    p = db.scalar(select(Product).where(Product.slug == slug, Product.status == "published"))
    if not p:
        raise ApiError("not_found", 404, entity="product")
    return hw.serialize_product(db, p, locale_of(request), detail=True)


@router.get("/kits/{slug}/bom")
def kit_bom(slug: str, request: Request, owned: str = "", app: str | None = None, db=DB):
    p = db.scalar(select(Product).where(Product.slug == slug, Product.status == "published"))
    if not p:
        raise ApiError("not_found", 404, entity="product")
    a = db.scalar(select(App).where(App.slug == app)) if app else None
    return hw.evaluate_bom(db, p, locale_of(request), {x for x in owned.split(",") if x}, a)


@router.get("/compatible")
def compatible(app: str, request: Request, db=DB):
    a = db.scalar(select(App).where(App.slug == app))
    if not a:
        raise ApiError("not_found", 404, entity="app")
    return {"app": app, "items": hw.compatible_for_app(db, a, locale_of(request))}


@router.get("/address-rules/{country}")
def address_rules(country: str, origin: str = "DE"):
    c = country.upper()
    return country_rules(c) | {"latin_required": get_shipping_adapter().requires_latin(origin.upper(), c)}


# ------------------------------------------------------------------ cart (works for guests via a cookie)

def _cart(request: Request, response: Response, p, db, create=True):
    tok = request.cookies.get(CART_COOKIE)
    if not p.user and not tok and create:
        tok = secrets.token_urlsafe(24)
        response.set_cookie(CART_COOKIE, tok, httponly=True, samesite="lax", max_age=3600 * 24 * 30, path="/")
    cart = hw.get_cart(db, p.id, tok, create)
    if p.user and tok:
        response.delete_cookie(CART_COOKIE, path="/")
    return cart


@router.get("/cart")
def get_cart(request: Request, response: Response, p=P, db=DB):
    cart = _cart(request, response, p, db, create=False)
    db.commit()
    return hw.serialize_cart(db, cart, locale_of(request))


class CartItemIn(BaseModel):
    sku_id: str
    quantity: int = 1


@router.post("/cart/items")
def add_item(body: CartItemIn, request: Request, response: Response, p=P, db=DB):
    cart = _cart(request, response, p, db)
    hw.cart_add(db, cart, as_uuid(body.sku_id, "sku"), body.quantity)
    db.commit()
    return hw.serialize_cart(db, cart, locale_of(request))


@router.put("/cart/items/{sku_id}")
def set_item(sku_id: str, body: CartItemIn, request: Request, response: Response, p=P, db=DB):
    cart = _cart(request, response, p, db)
    hw.cart_set(db, cart, as_uuid(sku_id, "sku"), body.quantity)
    db.commit()
    return hw.serialize_cart(db, cart, locale_of(request))


class BomAddIn(BaseModel):
    product_slug: str
    owned_sku_ids: list[str] = []
    choices: dict[str, str] = {}
    include_optional: list[str] = []


@router.post("/cart/bom")
def add_bom(body: BomAddIn, request: Request, response: Response, p=P, db=DB):
    prod = db.scalar(select(Product).where(Product.slug == body.product_slug, Product.status == "published"))
    if not prod:
        raise ApiError("not_found", 404, entity="product")
    cart = _cart(request, response, p, db)
    n = hw.cart_add_bom(db, cart, prod, set(body.owned_sku_ids), body.choices, set(body.include_optional))
    db.commit()
    return hw.serialize_cart(db, cart, locale_of(request)) | {"added": n}


# ------------------------------------------------------------------ orders

def order_view(db, o: HardwareOrder, locale: str, for_buyer: bool = True, prices: bool = True) -> dict:
    seller = db.get(Seller, o.seller_id)
    wh = db.get(Warehouse, o.warehouse_id)
    parcels = db.scalars(select(Parcel).where(Parcel.order_id == o.id).order_by(Parcel.number)).all()
    items = []
    for it in o.items:
        s = it.snapshot
        title = s["titles"].get(locale) or next((v for v in s["titles"].values() if v), f"{s['brand']} {s['model']}")
        row = {"id": str(it.id), "sku": s["code"], "title": title, "quantity": it.quantity,
               "shipped_qty": it.shipped_qty, "returned_qty": it.returned_qty, "made_to_order": s["made_to_order"],
               "packed_qty": ho.packed_qty(db, it.id)}
        if prices:
            row["unit_price_minor"] = it.unit_price_minor
        items.append(row)
    out = {"id": str(o.id), "number": o.number, "status": o.status, "payment_status": o.payment_status,
           "fulfillment_status": o.fulfillment_status, "exception_reason": o.exception_reason,
           "seller": {"id": str(seller.id), "name": seller.name, "kind": seller.kind},
           "ship_from": wh.country, "currency": o.currency, "locale": o.locale, "address": o.address,
           "items": items, "group_ref": o.group_ref, "is_demo": o.is_demo, "bom_snapshot": o.bom_snapshot,
           "parcels": [{"id": str(p.id), "number": p.number, "status": p.status, "status_source": p.status_source,
                        "carrier": p.carrier, "tracking_number": p.tracking_number, "weight_g": p.weight_g,
                        "dims_mm": [p.length_mm, p.width_mm, p.height_mm],
                        "items": [{"order_item_id": str(x.order_item_id), "quantity": x.quantity} for x in p.items],
                        "documents": p.documents,
                        "events": [{"status": e.status, "source": e.source, "description": e.description,
                                    "location": e.location, "occurred_at": e.occurred_at.isoformat()}
                                   for e in db.scalars(select(TrackingEvent).where(TrackingEvent.parcel_id == p.id)
                                                       .order_by(TrackingEvent.occurred_at))]} for p in parcels],
           "created_at": o.created_at.isoformat()}
    if prices:
        out |= {"goods_minor": o.goods_minor, "shipping_minor": o.shipping_minor, "fees_minor": o.fees_minor,
                "total_minor": o.total_minor, "refunded_minor": o.refunded_minor, "shipping_rate": o.shipping_rate,
                "terms": o.terms}
    q = db.scalar(select(FreightQuote).where(FreightQuote.order_id == o.id).order_by(FreightQuote.created_at.desc()))
    out["freight_quote"] = {"id": str(q.id), "status": q.status, "amount_minor": q.amount_minor,
                            "currency": q.currency, "eta": [q.eta_min_days, q.eta_max_days],
                            "valid_until": q.valid_until.isoformat() if q.valid_until else None,
                            "note": q.note} if q else None
    out["returns"] = [{"id": str(r.id), "status": r.status, "items": r.items, "reason": r.reason,
                       "decision_note": r.decision_note, "return_tracking": r.return_tracking,
                       "inspection": r.inspection}
                      for r in db.scalars(select(ReturnRequest).where(ReturnRequest.order_id == o.id))]
    return out


@router.post("/checkout", status_code=201)
def checkout(request: Request, response: Response, p=P, db=DB):
    """Split the hardware cart into independent orders (one seller + warehouse + currency each)."""
    u = p.require_user()
    ratelimit.check(request, "checkout", u.id)
    cart = _cart(request, response, p, db)
    orders = ho.create_orders_from_cart(db, db.merge(u), cart, locale_of(request))
    db.commit()
    return {"orders": [order_view(db, o, locale_of(request)) for o in orders], "separate_orders": len(orders) > 1}


@router.get("/orders")
def my_orders(request: Request, p=P, db=DB):
    u = p.require_user()
    rows = db.scalars(select(HardwareOrder).where(HardwareOrder.buyer_id == u.id)
                      .order_by(HardwareOrder.created_at.desc()))
    return {"items": [order_view(db, o, locale_of(request)) for o in rows]}


@router.get("/orders/{order_id}")
def get_order(order_id: str, request: Request, p=P, db=DB):
    u = p.require_user()
    return order_view(db, ho.buyer_order(db, as_uuid(order_id, "order"), u.id), locale_of(request))


class AddressIn(BaseModel):
    country: str
    fields: dict


@router.put("/orders/{order_id}/address")
def set_address(order_id: str, body: AddressIn, request: Request, p=P, db=DB):
    u = p.require_user()
    o = ho.buyer_order(db, as_uuid(order_id, "order"), u.id, lock=True)
    ho.set_address(db, o, body.country, body.fields)
    db.commit()
    return order_view(db, o, locale_of(request))


class QtyIn(BaseModel):
    quantity: int


@router.put("/orders/{order_id}/items/{item_id}")
def change_qty(order_id: str, item_id: str, body: QtyIn, request: Request, p=P, db=DB):
    u = p.require_user()
    o = ho.buyer_order(db, as_uuid(order_id, "order"), u.id, lock=True)
    ho.change_quantity(db, o, as_uuid(item_id), body.quantity)
    db.commit()
    return order_view(db, o, locale_of(request))


@router.post("/orders/{order_id}/rates")
def rates(order_id: str, p=P, db=DB):
    u = p.require_user()
    o = ho.buyer_order(db, as_uuid(order_id, "order"), u.id, lock=True)
    try:
        rs = ho.quote_rates(db, o)
    finally:
        db.commit()
    prep = ho.prep_days(o)
    return {"items": [{"id": str(r.id), "service": r.service_code, "carrier": r.carrier, "amount_minor": r.amount_minor,
                       "currency": r.currency, "eta_days": [r.eta_min_days + prep, r.eta_max_days + prep],
                       "eta_is_estimate": True, "prep_days": prep, "expires_at": r.expires_at.isoformat(),
                       "packages": len(r.packages), "duties_payer": r.duties_payer, "adapter": r.adapter,
                       "test_mode": r.adapter == "mock"} for r in rs]}


class RateIn(BaseModel):
    rate_id: str


@router.post("/orders/{order_id}/shipping")
def choose_rate(order_id: str, body: RateIn, request: Request, p=P, db=DB):
    u = p.require_user()
    o = ho.buyer_order(db, as_uuid(order_id, "order"), u.id, lock=True)
    ho.select_rate(db, o, as_uuid(body.rate_id, "rate"))
    db.commit()
    return order_view(db, o, locale_of(request))


@router.post("/orders/{order_id}/quote-request")
def quote_request(order_id: str, request: Request, p=P, db=DB):
    u = p.require_user()
    o = ho.buyer_order(db, as_uuid(order_id, "order"), u.id, lock=True)
    ho.request_quote(db, o)
    db.commit()
    from ..services.billing import dispatch_outbox
    dispatch_outbox()
    return order_view(db, o, locale_of(request))


class AcceptIn(BaseModel):
    quote_id: str


@router.post("/orders/{order_id}/quote-accept")
def quote_accept(order_id: str, body: AcceptIn, request: Request, p=P, db=DB):
    u = p.require_user()
    o = ho.buyer_order(db, as_uuid(order_id, "order"), u.id, lock=True)
    try:
        ho.accept_quote(db, o, as_uuid(body.quote_id, "quote"))
    finally:
        db.commit()
    return order_view(db, o, locale_of(request))


@router.post("/orders/{order_id}/pay")
def pay(order_id: str, p=P, db=DB):
    u = p.require_user()
    o = ho.buyer_order(db, as_uuid(order_id, "order"), u.id, lock=True)
    return ho.start_payment(db, o, db.merge(u))


@router.post("/orders/{order_id}/cancel")
def cancel(order_id: str, request: Request, p=P, db=DB):
    u = p.require_user()
    o = ho.buyer_order(db, as_uuid(order_id, "order"), u.id, lock=True)
    if o.payment_status == "paid":
        raise ApiError("contact_support_to_cancel", 409)
    ho.cancel_order(db, o, u.id, "buyer")
    db.commit()
    return order_view(db, o, locale_of(request))


class ReturnIn(BaseModel):
    items: list[dict]
    reason: str


@router.post("/orders/{order_id}/returns", status_code=201)
def create_return(order_id: str, body: ReturnIn, request: Request, p=P, db=DB):
    u = p.require_user()
    o = ho.buyer_order(db, as_uuid(order_id, "order"), u.id, lock=True)
    ho.request_return(db, o, body.items, body.reason)
    db.commit()
    return order_view(db, o, locale_of(request))


class ReturnShipIn(BaseModel):
    tracking_number: str


@router.post("/orders/{order_id}/returns/{return_id}/ship")
def return_ship(order_id: str, return_id: str, body: ReturnShipIn, request: Request, p=P, db=DB):
    u = p.require_user()
    o = ho.buyer_order(db, as_uuid(order_id, "order"), u.id)
    r = db.get(ReturnRequest, as_uuid(return_id))
    if not r or r.order_id != o.id:
        raise ApiError("not_found", 404, entity="return")
    ho.return_transition(db, r, "ship", u.id, {"tracking_number": body.tracking_number})
    db.commit()
    return order_view(db, o, locale_of(request))


def can_see_order_docs(db, p, o: HardwareOrder) -> bool:
    if p.user is None:
        return False
    if o.buyer_id == p.user.id or p.has("admin"):
        return True
    from ..security import seller_ids_for
    return o.seller_id in seller_ids_for(db, p.user.id)


@router.get("/orders/{order_id}/documents/packing-list")
def packing_list(order_id: str, parcel: str | None = None, format: str = "json", p=P, db=DB):
    o = db.get(HardwareOrder, as_uuid(order_id, "order"))
    if not o or not can_see_order_docs(db, p, o):
        raise ApiError("not_found", 404, entity="order")
    pc = db.get(Parcel, as_uuid(parcel)) if parcel else None
    if pc and pc.order_id != o.id:
        raise ApiError("not_found", 404, entity="parcel")
    doc = ho.packing_list(db, o, pc)
    if format == "pdf":
        return RawResponse(ho.packing_list_pdf(doc), media_type="application/pdf")
    return doc


@router.get("/orders/{order_id}/documents/{asset_id}")
def order_document(order_id: str, asset_id: str, p=P, db=DB):
    o = db.get(HardwareOrder, as_uuid(order_id, "order"))
    if not o or not can_see_order_docs(db, p, o):
        raise ApiError("not_found", 404, entity="order")
    ids = {d["asset_id"] for pc in db.scalars(select(Parcel).where(Parcel.order_id == o.id)) for d in pc.documents}
    if asset_id not in ids:
        raise ApiError("not_found", 404, entity="document")
    a = db.get(Asset, as_uuid(asset_id))
    return {"url": signed_url(a, f"{o.number}-{a.kind}.pdf")}
