from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..errors import ApiError
from ..models import (Asset, FreightQuote, HardwareOrder, Inventory, Parcel, Product, ReturnRequest, Seller, Sku,
                      Warehouse)
from ..security import seller_ids_for
from ..services import hardware_orders as ho
from ..services.common import audit
from ..storage import get_storage, new_key
from .deps import DB, P, as_uuid, idem, locale_of
from .hardware import order_view

router = APIRouter(prefix="/seller", tags=["seller/fulfillment"])


def ctx(p, db, roles=("seller", "fulfillment")):
    u = p.require_role(*roles)
    ids = seller_ids_for(db, u.id, tuple(r for r in roles if r in p.roles))
    return u, ids


def seller_order(db, p, order_id: str, roles=("seller", "fulfillment"), lock=False) -> HardwareOrder:
    u, ids = ctx(p, db, roles)
    o = db.get(HardwareOrder, as_uuid(order_id, "order"), with_for_update=lock)
    if not o or o.seller_id not in ids:
        raise ApiError("not_found", 404, entity="order")  # other sellers' orders do not exist for you
    if "seller" not in p.roles and (o.payment_status != "paid" or o.status != "confirmed"):
        raise ApiError("not_found", 404, entity="order")  # fulfillment sees only assigned, paid orders
    return o


def _prices_visible(p) -> bool:
    return "seller" in p.roles


@router.get("/orders")
def orders(request: Request, p=P, db=DB):
    u, ids = ctx(p, db)
    q = select(HardwareOrder).where(HardwareOrder.seller_id.in_(ids))
    if "seller" not in p.roles:
        q = q.where(HardwareOrder.payment_status == "paid", HardwareOrder.status == "confirmed")
    else:
        q = q.where((HardwareOrder.payment_status == "paid") | (HardwareOrder.status == "awaiting_quote"))
    rows = db.scalars(q.order_by(HardwareOrder.created_at.desc()))
    return {"items": [order_view(db, o, locale_of(request), for_buyer=False, prices=_prices_visible(p)) for o in rows]}


@router.get("/orders/{order_id}")
def order(order_id: str, request: Request, p=P, db=DB):
    o = seller_order(db, p, order_id)
    return order_view(db, o, locale_of(request), for_buyer=False, prices=_prices_visible(p))


@router.post("/orders/{order_id}/acknowledge")
def acknowledge(order_id: str, request: Request, p=P, db=DB):
    o = seller_order(db, p, order_id, lock=True)
    ho.get_fulfillment().acknowledge(db, o, p.id)
    db.commit()
    return order_view(db, o, locale_of(request), for_buyer=False, prices=_prices_visible(p))


class ParcelIn(BaseModel):
    items: list[dict]
    weight_g: int
    dims_mm: list[int]


@router.post("/orders/{order_id}/parcels", status_code=201)
def create_parcel(order_id: str, body: ParcelIn, request: Request, p=P, db=DB):
    o = seller_order(db, p, order_id, lock=True)
    ho.create_parcel(db, o, p.id, body.items, body.weight_g, body.dims_mm)
    db.commit()
    return order_view(db, o, locale_of(request), for_buyer=False, prices=_prices_visible(p))


def _parcel(db, o, parcel_id) -> Parcel:
    pc = db.get(Parcel, as_uuid(parcel_id, "parcel"), with_for_update=True)
    if not pc or pc.order_id != o.id:
        raise ApiError("not_found", 404, entity="parcel")
    return pc


@router.post("/orders/{order_id}/parcels/{parcel_id}/label")
def label(order_id: str, parcel_id: str, request: Request, p=P, db=DB):
    o = seller_order(db, p, order_id)
    pc = _parcel(db, o, parcel_id)
    ho.buy_label(db, o, pc, p.id)
    db.commit()
    return order_view(db, o, locale_of(request), for_buyer=False, prices=_prices_visible(p))


class ManualIn(BaseModel):
    carrier: str = Field(max_length=64)
    tracking_number: str = Field(max_length=64)


@router.post("/orders/{order_id}/parcels/{parcel_id}/manual-shipment")
def manual(order_id: str, parcel_id: str, body: ManualIn, request: Request, p=P, db=DB):
    o = seller_order(db, p, order_id)
    pc = _parcel(db, o, parcel_id)
    ho.manual_shipment(db, o, pc, p.id, body.carrier, body.tracking_number)
    db.commit()
    return order_view(db, o, locale_of(request), for_buyer=False, prices=_prices_visible(p))


@router.post("/orders/{order_id}/parcels/{parcel_id}/documents")
async def upload_document(order_id: str, parcel_id: str, request: Request, kind: str = "commercial_invoice", p=P,
                          db=DB):
    o = seller_order(db, p, order_id)
    pc = _parcel(db, o, parcel_id)
    if kind not in ("commercial_invoice", "label", "customs", "other"):
        raise ApiError("validation_failed", 422)
    body = await request.body()
    if not body.startswith(b"%PDF") or len(body) > 10 * 1024 * 1024:
        raise ApiError("unsupported_format", 415)
    key = new_key("document", None, "pdf")
    with tempfile.NamedTemporaryFile(delete=False) as fh:
        fh.write(body)
        tmp = fh.name
    get_storage().put_file(key, tmp, "application/pdf")
    os.remove(tmp)
    a = Asset(owner_id=None, kind="document", storage_key=key, content_type="application/pdf", size_bytes=len(body),
              sha256=hashlib.sha256(body).hexdigest(), status="ready", immutable=True)
    db.add(a)
    db.flush()
    ho.attach_document(db, pc, kind, a)
    audit(db, p.id, "parcel.document_uploaded", "hardware_order", o.id, {"kind": kind})
    db.commit()
    return order_view(db, o, locale_of(request), for_buyer=False, prices=_prices_visible(p))


@router.post("/orders/{order_id}/parcels/{parcel_id}/handover")
def handover(order_id: str, parcel_id: str, request: Request, p=P, db=DB):
    o = seller_order(db, p, order_id, lock=True)
    pc = _parcel(db, o, parcel_id)
    ho.handover(db, o, pc, p.id)
    db.commit()
    return order_view(db, o, locale_of(request), for_buyer=False, prices=_prices_visible(p))


class EventIn(BaseModel):
    status: str
    description: str = ""
    occurred_at: datetime | None = None
    external_id: str | None = None


@router.post("/orders/{order_id}/parcels/{parcel_id}/events")
def event(order_id: str, parcel_id: str, body: EventIn, request: Request, p=P, db=DB):
    o = seller_order(db, p, order_id, lock=True)
    pc = _parcel(db, o, parcel_id)
    ho.supplier_event(db, o, pc, p.id, body.status, body.description, body.occurred_at, body.external_id)
    db.commit()
    return order_view(db, o, locale_of(request), for_buyer=False, prices=_prices_visible(p))


class ErrorIn(BaseModel):
    code: str = Field(max_length=64)


@router.post("/orders/{order_id}/exception")
def report_error(order_id: str, body: ErrorIn, request: Request, p=P, db=DB):
    o = seller_order(db, p, order_id, lock=True)
    ho.get_fulfillment().report_error(db, o, body.code, p.id)
    db.commit()
    return order_view(db, o, locale_of(request), for_buyer=False, prices=_prices_visible(p))


class QuoteIn(BaseModel):
    amount_minor: int
    currency: str
    eta_min_days: int
    eta_max_days: int
    valid_hours: int = 72
    note: str = ""


@router.post("/orders/{order_id}/quotes/{quote_id}")
def provide_quote(order_id: str, quote_id: str, body: QuoteIn, request: Request, p=P, db=DB):
    o = seller_order(db, p, order_id, roles=("seller",), lock=True)
    q = db.get(FreightQuote, as_uuid(quote_id, "quote"), with_for_update=True)
    if not q or q.order_id != o.id:
        raise ApiError("not_found", 404, entity="quote")
    ho.provide_quote(db, p.id, q, body.amount_minor, body.currency, body.eta_min_days, body.eta_max_days,
                     body.valid_hours, body.note)
    db.commit()
    return order_view(db, o, locale_of(request), for_buyer=False)


class ReturnActionIn(BaseModel):
    action: str
    note: str = ""
    results: dict = {}


@router.post("/orders/{order_id}/returns/{return_id}")
def return_action(order_id: str, return_id: str, body: ReturnActionIn, request: Request, p=P, db=DB):
    roles = ("seller",) if body.action in ("approve", "reject", "close") else ("seller", "fulfillment")
    o = seller_order(db, p, order_id, roles=roles)
    r = db.get(ReturnRequest, as_uuid(return_id, "return"), with_for_update=True)
    if not r or r.order_id != o.id or body.action == "ship":
        raise ApiError("not_found", 404, entity="return")
    ho.return_transition(db, r, body.action, p.id, {"note": body.note, "results": body.results})
    db.commit()
    return order_view(db, o, locale_of(request), for_buyer=False, prices=_prices_visible(p))


class HwRefundIn(BaseModel):
    goods_minor: int = 0
    shipping_minor: int = 0
    fees_minor: int = 0
    reason: str = ""


@router.post("/orders/{order_id}/refunds", status_code=201)
def hw_refund(order_id: str, body: HwRefundIn, request: Request, p=P, db=DB, key=Depends(idem)):
    o = seller_order(db, p, order_id, roles=("seller",), lock=True)
    if not key:
        raise ApiError("idempotency_key_required", 422)
    ho.request_hardware_refund(db, o, p.id, body.goods_minor, body.shipping_minor, body.fees_minor, body.reason,
                               f"hw:{o.id}:{key}")
    db.commit()
    from ..services.billing import dispatch_outbox
    dispatch_outbox()
    db.expire_all()
    return order_view(db, db.get(HardwareOrder, o.id), locale_of(request), for_buyer=False)


@router.post("/orders/{order_id}/cancel")
def cancel(order_id: str, request: Request, p=P, db=DB):
    o = seller_order(db, p, order_id, roles=("seller",), lock=True)
    ho.cancel_order(db, o, p.id, "seller")
    db.commit()
    return order_view(db, o, locale_of(request), for_buyer=False)


@router.get("/inventory")
def inventory(p=P, db=DB):
    u, ids = ctx(p, db, ("seller",))
    out = []
    for prod in db.scalars(select(Product).where(Product.seller_id.in_(ids))):
        for s in prod.skus:
            inv = db.scalar(select(Inventory).where(Inventory.sku_id == s.id, Inventory.warehouse_id == prod.warehouse_id))
            out.append({"sku_id": str(s.id), "code": s.code, "product_slug": prod.slug,
                        "made_to_order": prod.made_to_order,
                        "on_hand": inv.on_hand if inv else 0, "reserved": inv.reserved if inv else 0,
                        "allocated": inv.allocated if inv else 0,
                        "available": (inv.on_hand - inv.reserved - inv.allocated) if inv else 0})
    return {"items": out}


class AdjustIn(BaseModel):
    delta: int
    reason: str = Field(min_length=3, max_length=200)


@router.post("/inventory/{sku_id}/adjust")
def adjust(sku_id: str, body: AdjustIn, p=P, db=DB):
    u, ids = ctx(p, db, ("seller",))
    s = db.get(Sku, as_uuid(sku_id, "sku"))
    prod = db.get(Product, s.product_id) if s else None
    if not prod or prod.seller_id not in ids:
        raise ApiError("not_found", 404, entity="sku")
    inv = db.scalar(select(Inventory).where(Inventory.sku_id == s.id, Inventory.warehouse_id == prod.warehouse_id)
                    .with_for_update())
    if inv is None:
        inv = Inventory(sku_id=s.id, warehouse_id=prod.warehouse_id, on_hand=0)
        db.add(inv)
        db.flush()
    if inv.on_hand + body.delta < inv.reserved + inv.allocated:
        raise ApiError("inventory_below_committed", 409)
    inv.on_hand += body.delta
    audit(db, u.id, "inventory.adjusted", "sku", s.code, {"delta": body.delta, "reason": body.reason})
    db.commit()
    return {"on_hand": inv.on_hand, "available": inv.on_hand - inv.reserved - inv.allocated}
