"""Hardware orders: split checkout, address/rates/quotes, payment, fulfillment, parcels, tracking, returns, refunds.

Order status, payment status and fulfillment status are independent. A hardware payment never creates a digital
entitlement and never produces a 30/70 developer split.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..adapters.payments import ProviderUnavailable, get_payment_provider
from ..adapters.shipping import (CarrierTimeout, ManualQuoteRequired, Package, PackageTooLarge, RouteUnsupported,
                                 get_shipping_adapter, minimal_pdf)
from ..config import get_settings
from ..db import session_scope, utcnow
from ..errors import ApiError
from ..models import (Asset, Bom, Cart, FreightQuote, HardwareOrder, HardwareOrderItem, Parcel, ParcelItem,
                      PaymentAttempt, Product, Refund, ReturnRequest, Seller, ShippingRate, Sku, TrackingEvent, User,
                      Warehouse)
from ..storage import get_storage, new_key
from . import money
from .addresses import validate_address
from .common import audit, enqueue, get_translations, localized, notify
from .hardware import OutOfStock, allocate_order, release_order, reserve_order, restock, ship_quantity

TERMS_DAP = {"incoterm": "DAP", "duties_payer": "recipient", "duties_amount": None,
             "duties_note_code": "duties_not_included_unknown"}
STATUS_RANK = {"packing": 0, "label_created": 1, "handed_over": 2, "in_transit": 3, "customs": 4,
               "out_for_delivery": 5, "delivered": 9, "exception": 6, "returned_to_sender": 8, "cancelled": 7}


def _composition(items: list[tuple[uuid.UUID, int]]) -> str:
    return hashlib.sha256(json.dumps(sorted((str(s), q) for s, q in items)).encode()).hexdigest()


def _addr_hash(addr: dict | None) -> str:
    return hashlib.sha256(json.dumps(addr or {}, sort_keys=True).encode()).hexdigest()


def _order_number() -> str:
    return "HW-" + secrets.token_hex(4).upper()


def _sku_snapshot(db: Session, sku: Sku) -> dict:
    prod = db.get(Product, sku.product_id)
    tr = get_translations(db, "product", [prod.id]).get(prod.id) or {}
    return {"code": sku.code, "product_slug": prod.slug, "brand": prod.brand, "model": prod.model,
            "titles": {loc: t.data.get("title") for loc, t in tr.items()}, "variant": sku.variant,
            "weight_g": sku.weight_g, "dims_mm": [sku.length_mm, sku.width_mm, sku.height_mm],
            "package_count": sku.package_count, "origin_country": sku.origin_country, "hs_code": sku.hs_code,
            "customs_description": sku.customs_description, "contains_battery": sku.contains_battery,
            "made_to_order": prod.made_to_order, "lead_time_days": prod.lead_time_days, "prep_days": prod.prep_days,
            "freight_mode": prod.freight_mode, "warranty_months": prod.warranty_months,
            "assembly_included": prod.assembly_included, "return_terms": {loc: t.data.get("return_terms")
                                                                          for loc, t in tr.items()}}


# ------------------------------------------------------------------ checkout from cart

def create_orders_from_cart(db: Session, user: User, cart: Cart, locale: str) -> list[HardwareOrder]:
    """One order per seller + warehouse + currency; shown to the buyer before payment."""
    if not cart.items:
        raise ApiError("cart_empty", 409)
    groups: dict[tuple, list] = {}
    for it in cart.items:
        sku = db.get(Sku, it.sku_id)
        prod = db.get(Product, sku.product_id)
        groups.setdefault((prod.seller_id, prod.warehouse_id, sku.currency), []).append((it, sku, prod))
    group_ref = uuid.uuid4().hex[:12]
    orders = []
    try:
        for (seller_id, wh_id, cur), lines in groups.items():
            custom = any(p.freight_mode == "custom_quote" for _, _, p in lines)
            bom_ids = {it.source_bom_id for it, _, _ in lines if it.source_bom_id}
            bom_snap = None
            if bom_ids:
                bom_snap = []
                for bid in bom_ids:
                    b = db.get(Bom, bid)
                    bom_snap.append({"bom_id": str(b.id), "version": b.version,
                                     "product_slug": db.get(Product, b.product_id).slug,
                                     "items": [{"sku_id": str(i.sku_id), "qty": i.quantity, "required": i.required,
                                                "role": i.role} for i in b.items],
                                     "owned_sku_ids": cart.owned_sku_ids})
            o = HardwareOrder(number=_order_number(), buyer_id=user.id, seller_id=seller_id, warehouse_id=wh_id,
                              currency=cur, status="awaiting_quote" if custom else "awaiting_payment",
                              locale=locale, terms=TERMS_DAP | {"return_window_days": 14}, bom_snapshot=bom_snap,
                              goods_minor=sum(s.price_minor * it.quantity for it, s, _ in lines),
                              composition_hash=_composition([(s.id, it.quantity) for it, s, _ in lines]),
                              group_ref=group_ref, is_demo=True)
            db.add(o)
            db.flush()
            for pos, (it, sku, prod) in enumerate(lines, 1):
                db.add(HardwareOrderItem(order_id=o.id, position=pos, sku_id=sku.id, quantity=it.quantity,
                                         unit_price_minor=sku.price_minor, snapshot=_sku_snapshot(db, sku)))
            db.flush()
            db.refresh(o)
            reserve_order(db, o)
            orders.append(o)
            audit(db, user.id, "hw_order.created", "hardware_order", o.id, {"group": group_ref})
    except OutOfStock as e:
        db.rollback()
        raise ApiError("out_of_stock", 409, sku=e.sku_code)
    cart.items.clear()
    return orders


def buyer_order(db: Session, order_id: uuid.UUID, user_id: uuid.UUID, lock: bool = False) -> HardwareOrder:
    o = db.get(HardwareOrder, order_id, with_for_update=lock)
    if not o or o.buyer_id != user_id:
        raise ApiError("not_found", 404, entity="order")
    return o


def _assert_unpaid(o: HardwareOrder) -> None:
    if o.payment_status not in ("unpaid", "failed") or o.status not in ("awaiting_payment", "awaiting_quote"):
        raise ApiError("order_locked", 409)


def _invalidate_shipping(db: Session, o: HardwareOrder) -> None:
    o.shipping_rate, o.shipping_minor, o.total_minor = None, None, None
    for q in db.scalars(select(FreightQuote).where(FreightQuote.order_id == o.id,
                                                   FreightQuote.status.in_(("requested", "quoted", "accepted")))):
        q.status = "expired"
    if any(it.snapshot.get("freight_mode") == "custom_quote" for it in o.items):
        o.status = "awaiting_quote"


def set_address(db: Session, o: HardwareOrder, country: str, fields: dict) -> HardwareOrder:
    _assert_unpaid(o)
    wh = db.get(Warehouse, o.warehouse_id)
    adapter = get_shipping_adapter()
    clean = validate_address(country, fields, adapter.requires_latin(wh.country, country.upper()))
    products_dest = set()
    for it in o.items:
        prod = db.scalar(select(Product).where(Product.slug == it.snapshot["product_slug"]))
        if prod.destinations and country.upper() not in prod.destinations:
            products_dest.add(it.snapshot["code"])
    if products_dest:
        raise ApiError("destination_not_served", 422, details={"skus": sorted(products_dest)})
    new = {"country": country.upper(), "fields": clean}
    from .addresses import save_to_book  # NAP-023: the address book remembers what was used
    save_to_book(db, o.buyer_id, country, clean)
    if new != o.address:
        o.address = new
        _invalidate_shipping(db, o)
    audit(db, o.buyer_id, "hw_order.address_set", "hardware_order", o.id, {"country": country.upper()})
    return o


def change_quantity(db: Session, o: HardwareOrder, item_id: uuid.UUID, qty: int) -> HardwareOrder:
    _assert_unpaid(o)
    it = next((i for i in o.items if i.id == item_id), None)
    if not it or not 1 <= qty <= 99:
        raise ApiError("validation_failed", 422)
    release_order(db, o.id)
    it.quantity = qty
    db.flush()
    db.refresh(o)
    try:
        with db.begin_nested():
            reserve_order(db, o)
    except OutOfStock as e:
        raise ApiError("out_of_stock", 409, sku=e.sku_code)
    o.goods_minor = sum(i.unit_price_minor * i.quantity for i in o.items)
    o.composition_hash = _composition([(i.sku_id, i.quantity) for i in o.items])
    _invalidate_shipping(db, o)
    return o


def packages_for(o: HardwareOrder) -> list[Package]:
    pk = []
    for it in o.items:
        s = it.snapshot
        for _ in range(it.quantity):
            for _ in range(s["package_count"]):
                pk.append(Package(s["weight_g"] // s["package_count"], *s["dims_mm"]))
    return pk


def quote_rates(db: Session, o: HardwareOrder) -> list[ShippingRate]:
    _assert_unpaid(o)
    if not o.address:
        raise ApiError("address_required", 422)
    if o.status == "awaiting_quote":
        raise ApiError("custom_quote_required", 409)
    wh = db.get(Warehouse, o.warehouse_id)
    adapter = get_shipping_adapter()
    try:
        rates = adapter.rates(origin={"country": wh.country, "fields": wh.address}, destination=o.address,
                              packages=packages_for(o), currency=o.currency)
    except RouteUnsupported:
        raise ApiError("route_unsupported", 422)
    except PackageTooLarge:
        raise ApiError("package_too_large", 422)
    except ManualQuoteRequired:
        o.status = "awaiting_quote"
        raise ApiError("custom_quote_required", 409)
    exp = utcnow() + timedelta(minutes=get_settings().rate_ttl_minutes)
    out = []
    pk = [p.__dict__ for p in packages_for(o)]
    for r in rates:
        sr = ShippingRate(order_id=o.id, adapter=adapter.name, service_code=r.service_code, carrier=r.carrier,
                          amount_minor=r.amount_minor, currency=r.currency, eta_min_days=r.eta_min_days,
                          eta_max_days=r.eta_max_days, packages=pk, insurance_minor=r.insurance_minor,
                          duties_payer=o.terms.get("duties_payer", "recipient"), external_ref=r.external_ref,
                          composition_hash=o.composition_hash + ":" + _addr_hash(o.address), expires_at=exp)
        db.add(sr)
        out.append(sr)
    db.flush()
    return out


def prep_days(o: HardwareOrder) -> int:
    return max((it.snapshot.get("lead_time_days", 0) if it.snapshot.get("made_to_order") else 0) +
               it.snapshot.get("prep_days", 1) for it in o.items)


def select_rate(db: Session, o: HardwareOrder, rate_id: uuid.UUID) -> HardwareOrder:
    _assert_unpaid(o)
    r = db.get(ShippingRate, rate_id)
    if not r or r.order_id != o.id:
        raise ApiError("not_found", 404, entity="rate")
    _check_rate_valid(o, r)
    p = prep_days(o)
    o.shipping_rate = {"type": "carrier_rate", "rate_id": str(r.id), "adapter": r.adapter, "service": r.service_code,
                       "carrier": r.carrier, "amount_minor": r.amount_minor, "currency": r.currency,
                       "eta_days": [r.eta_min_days + p, r.eta_max_days + p], "eta_is_estimate": True,
                       "prep_days": p, "insurance_minor": r.insurance_minor, "expires_at": r.expires_at.isoformat(),
                       "duties_payer": r.duties_payer, "packages": len(r.packages)}
    o.shipping_minor = r.amount_minor
    o.total_minor = o.goods_minor + o.shipping_minor + o.fees_minor
    return o


def _check_rate_valid(o: HardwareOrder, r: ShippingRate) -> None:
    if r.expires_at < utcnow():
        raise ApiError("rate_expired", 409)
    if r.composition_hash != o.composition_hash + ":" + _addr_hash(o.address):
        raise ApiError("rate_stale", 409)
    if r.currency != o.currency:
        raise ApiError("rate_stale", 409)


# ------------------------------------------------------------------ custom freight quotes

def request_quote(db: Session, o: HardwareOrder) -> FreightQuote:
    _assert_unpaid(o)
    if not o.address:
        raise ApiError("address_required", 422)
    open_q = db.scalar(select(FreightQuote).where(FreightQuote.order_id == o.id,
                                                  FreightQuote.status.in_(("requested", "quoted"))))
    if open_q:
        return open_q
    o.status = "awaiting_quote"
    q = FreightQuote(order_id=o.id, composition_hash=o.composition_hash + ":" + _addr_hash(o.address))
    db.add(q)
    db.flush()
    enqueue(db, "fulfillment.quote_requested", {"order_id": str(o.id), "quote_id": str(q.id)}, f"quote:{q.id}")
    audit(db, o.buyer_id, "freight_quote.requested", "hardware_order", o.id)
    return q


def provide_quote(db: Session, actor_id: uuid.UUID, q: FreightQuote, amount_minor: int, currency: str,
                  eta_min: int, eta_max: int, valid_hours: int, note: str) -> FreightQuote:
    o = db.get(HardwareOrder, q.order_id)
    if q.status != "requested":
        raise ApiError("invalid_state", 409)
    if currency != o.currency or amount_minor <= 0 or not 0 < eta_min <= eta_max or not 1 <= valid_hours <= 24 * 30:
        raise ApiError("validation_failed", 422)
    q.amount_minor, q.currency, q.eta_min_days, q.eta_max_days = amount_minor, currency, eta_min, eta_max
    q.valid_until = utcnow() + timedelta(hours=valid_hours)
    q.note, q.status, q.quoted_by = note[:2000], "quoted", actor_id
    buyer = db.get(User, o.buyer_id)
    notify(db, buyer.id, o.locale, "quote_ready", f"quote:{q.id}", order=o.number,
           amount=f"{amount_minor / 100:.2f} {currency}")
    audit(db, actor_id, "freight_quote.provided", "hardware_order", o.id, {"amount_minor": amount_minor})
    return q


def accept_quote(db: Session, o: HardwareOrder, quote_id: uuid.UUID) -> HardwareOrder:
    _assert_unpaid(o)
    q = db.get(FreightQuote, quote_id, with_for_update=True)
    if not q or q.order_id != o.id or q.status != "quoted":
        raise ApiError("invalid_state", 409)
    if q.valid_until < utcnow():
        q.status = "expired"
        raise ApiError("quote_expired", 409)
    if q.composition_hash != o.composition_hash + ":" + _addr_hash(o.address):
        q.status = "expired"
        raise ApiError("rate_stale", 409)
    q.status = "accepted"
    p = prep_days(o)
    o.shipping_rate = {"type": "freight_quote", "quote_id": str(q.id), "adapter": "manual", "service": "freight",
                       "carrier": None, "amount_minor": q.amount_minor, "currency": q.currency,
                       "eta_days": [q.eta_min_days + p, q.eta_max_days + p], "eta_is_estimate": True, "prep_days": p,
                       "expires_at": q.valid_until.isoformat(), "duties_payer": o.terms.get("duties_payer"),
                       "note": q.note}
    o.shipping_minor = q.amount_minor
    o.total_minor = o.goods_minor + o.shipping_minor + o.fees_minor
    o.status = "awaiting_payment"
    return o


# ------------------------------------------------------------------ payment

def start_payment(db: Session, o: HardwareOrder, user: User) -> dict:
    _assert_unpaid(o)
    if o.status != "awaiting_payment" or not o.shipping_rate or o.shipping_minor is None:
        raise ApiError("shipping_required", 409)
    sr = o.shipping_rate
    if sr["type"] == "carrier_rate":
        _check_rate_valid(o, db.get(ShippingRate, uuid.UUID(sr["rate_id"])))
    else:
        q = db.get(FreightQuote, uuid.UUID(sr["quote_id"]))
        if q.valid_until < utcnow() or q.composition_hash != o.composition_hash + ":" + _addr_hash(o.address):
            raise ApiError("quote_expired", 409)
    total = o.goods_minor + o.shipping_minor + o.fees_minor
    if total != o.total_minor:
        raise ApiError("rate_stale", 409)
    o.payment_status = "pending"
    db.commit()
    from ..adapters.payments import provider_for_currency
    provider = provider_for_currency(o.currency)  # NAP-012: RUB → YooKassa
    s = get_settings()
    n = db.scalar(select(func.count()).select_from(PaymentAttempt).where(PaymentAttempt.order_id == o.id)) or 0
    f = (o.address or {}).get("fields") or {}
    ship_to = {"name": f.get("name"), "line1": f.get("line1"), "line2": f.get("line2"), "city": f.get("city"),
               "state": f.get("region"), "postal_code": f.get("postal_code"), "country": (o.address or {}).get("country")}
    lines = [{"name": f"NAP Store order {o.number}", "amount_minor": o.goods_minor + o.fees_minor, "kind": "goods"}]
    if o.shipping_minor:
        lines.append({"name": "Shipping", "amount_minor": o.shipping_minor, "kind": "shipping"})
    try:
        from .billing import ensure_customer
        customer_ref = ensure_customer(db, provider, user, shipping=ship_to)  # NAP-020: tax by delivery address
        cs = provider.create_checkout(
            context="hardware", order_id=str(o.id), amount_minor=total, currency=o.currency,
            description=f"NAP Store order {o.number}", mode="payment", interval=None, subscription_id=None,
            success_url=f"{s.public_base_url}/{o.locale}/orders/{o.id}", cancel_url=f"{s.public_base_url}/{o.locale}/orders/{o.id}",
            idempotency_key=f"hardware:{o.id}:{o.total_minor}:{n}", customer_email=user.email, lines=lines,
            customer_ref=customer_ref)
    except ProviderUnavailable as e:
        raise ApiError("payment_provider_unavailable", 503, reason=str(e))
    att = db.scalar(select(PaymentAttempt).where(PaymentAttempt.provider_ref == cs.provider_ref))
    if att is None:
        att = PaymentAttempt(context="hardware", order_id=o.id, provider=provider.name, provider_ref=cs.provider_ref,
                             amount_minor=total, currency=o.currency, checkout_url=cs.url, test_mode=cs.test_mode)
        db.add(att)
    audit(db, user.id, "hw_order.payment_started", "hardware_order", o.id, {"amount_minor": total})
    db.commit()
    return {"order_id": str(o.id), "checkout_url": cs.url, "test_mode": cs.test_mode}


def confirm_payment(db: Session, att: PaymentAttempt, tax_minor: int = 0) -> None:
    o = db.get(HardwareOrder, att.order_id, with_for_update=True)
    if o.payment_status == "paid":
        return
    if att.amount_minor != o.total_minor:
        o.exception_reason = "amount_mismatch"
    o.payment_status, o.paid_at = "paid", utcnow()
    o.tax_minor = tax_minor  # NAP-020: sales tax / VAT computed by the provider; held for the tax authority
    seller = db.get(Seller, o.seller_id)
    money.post(db, f"hw_sale:{o.id}", "hardware_sale", o.currency, [
        (f"cash:{att.provider}", att.amount_minor + tax_minor), ("tax:payable", -tax_minor),
        (f"hardware:{seller.id}:goods", -o.goods_minor),
        (f"hardware:{seller.id}:shipping", -(o.shipping_minor or 0)), (f"hardware:{seller.id}:fees", -o.fees_minor),
        (f"hardware:{seller.id}:unreconciled", -(att.amount_minor - (o.total_minor or 0)))],
        memo=f"hardware order {o.number} ({seller.kind}); separate hardware settlement policy")
    if allocate_order(db, o):
        o.status = "confirmed"
        enqueue(db, "fulfillment.submit", {"order_id": str(o.id)}, f"fulfill:{o.id}")
        notify(db, o.buyer_id, o.locale, "hw_paid", f"hwpaid:{o.id}", order=o.number)
    else:
        o.status = "confirmed"
        o.fulfillment_status = "exception"
        o.exception_reason = "stock_unavailable_after_payment"
        notify(db, o.buyer_id, o.locale, "hw_exception", f"hwexc:{o.id}", order=o.number)
    audit(db, None, "hw_order.paid", "hardware_order", o.id, {"provider": att.provider})


def payment_failed(db: Session, att: PaymentAttempt) -> None:
    o = db.get(HardwareOrder, att.order_id, with_for_update=True)
    if o.payment_status == "pending":
        o.payment_status = "failed"


def cancel_order(db: Session, o: HardwareOrder, actor_id: uuid.UUID, reason: str) -> HardwareOrder:
    if o.status in ("cancelled", "closed"):
        return o
    if any(it.shipped_qty > 0 for it in o.items):
        raise ApiError("already_shipped", 409)
    release_order(db, o.id)
    o.status = "cancelled"
    audit(db, actor_id, "hw_order.cancelled", "hardware_order", o.id, {"reason": reason[:200]})
    if o.payment_status == "paid":
        notify(db, o.buyer_id, o.locale, "hw_cancelled_refund_pending", f"hwcancel:{o.id}", order=o.number)
    return o


# ------------------------------------------------------------------ fulfillment

class FulfillmentAdapter(Protocol):
    name: str
    def submit(self, order_id: uuid.UUID) -> None: ...
    def acknowledge(self, db: Session, order: HardwareOrder, actor_id: uuid.UUID) -> None: ...
    def report_error(self, db: Session, order: HardwareOrder, code: str, actor_id: uuid.UUID) -> None: ...


class PlatformFulfillment:
    """Supplier works in the NAP Store seller cabinet (pick/pack/parcels/handover). A 3PL adapter would
    implement the same contract via its API."""
    name = "platform"

    def submit(self, order_id):
        with session_scope() as db:
            o = db.get(HardwareOrder, order_id)
            for m in _seller_users(db, o.seller_id):
                notify(db, m.id, m.locale, "seller_new_order", f"sellernew:{o.id}:{m.id}", order=o.number)

    def acknowledge(self, db, order, actor_id):
        if order.status != "confirmed" or order.payment_status != "paid":
            raise ApiError("invalid_state", 409)
        if order.fulfillment_status == "unfulfilled":
            order.fulfillment_status = "processing"
        audit(db, actor_id, "fulfillment.acknowledged", "hardware_order", order.id)

    def report_error(self, db, order, code, actor_id):
        order.fulfillment_status, order.exception_reason = "exception", code[:64]
        audit(db, actor_id, "fulfillment.error", "hardware_order", order.id, {"code": code})
        notify(db, order.buyer_id, order.locale, "hw_exception", f"hwexc:{order.id}:{code}", order=order.number)


def get_fulfillment() -> FulfillmentAdapter:
    return PlatformFulfillment()


def _seller_users(db: Session, seller_id: uuid.UUID) -> list[User]:
    from ..models import SellerMember
    ids = db.scalars(select(SellerMember.user_id).where(SellerMember.seller_id == seller_id)).all()
    return [db.get(User, i) for i in set(ids)]


def dispatch_fulfillment(topic: str, payload: dict) -> None:
    if topic == "fulfillment.submit":
        get_fulfillment().submit(uuid.UUID(payload["order_id"]))
    elif topic == "fulfillment.quote_requested":
        with session_scope() as db:
            o = db.get(HardwareOrder, uuid.UUID(payload["order_id"]))
            for m in _seller_users(db, o.seller_id):
                notify(db, m.id, m.locale, "seller_quote_requested", f"sellerq:{payload['quote_id']}:{m.id}",
                       order=o.number)


def packed_qty(db: Session, item_id: uuid.UUID) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(ParcelItem.quantity), 0)).join(Parcel).where(
        ParcelItem.order_item_id == item_id, Parcel.status != "cancelled")) or 0)


def create_parcel(db: Session, o: HardwareOrder, actor_id: uuid.UUID, items: list[dict], weight_g: int,
                  dims_mm: list[int]) -> Parcel:
    if o.status != "confirmed" or o.payment_status not in ("paid", "partially_refunded"):
        raise ApiError("invalid_state", 409)
    if o.fulfillment_status == "unfulfilled":
        o.fulfillment_status = "processing"
    if not items or weight_g <= 0 or len(dims_mm) != 3 or min(dims_mm) <= 0:
        raise ApiError("validation_failed", 422)
    db.execute(select(HardwareOrderItem.id).where(HardwareOrderItem.order_id == o.id).with_for_update())
    number = (db.scalar(select(func.max(Parcel.number)).where(Parcel.order_id == o.id)) or 0) + 1
    p = Parcel(order_id=o.id, number=number, weight_g=weight_g, length_mm=dims_mm[0], width_mm=dims_mm[1],
               height_mm=dims_mm[2], status="packing")
    db.add(p)
    db.flush()
    for row in items:
        it = next((i for i in o.items if str(i.id) == str(row["order_item_id"])), None)
        q = int(row["quantity"])
        if not it or q <= 0:
            raise ApiError("validation_failed", 422)
        if packed_qty(db, it.id) + q > it.quantity:
            raise ApiError("exceeds_ordered_quantity", 422, sku=it.snapshot["code"])
        db.add(ParcelItem(parcel_id=p.id, order_item_id=it.id, quantity=q))
        db.flush()
    audit(db, actor_id, "parcel.created", "hardware_order", o.id, {"parcel": number})
    return p


def _customs_lines(o: HardwareOrder, p: Parcel) -> list[dict]:
    out = []
    for pi in p.items:
        it = next(i for i in o.items if i.id == pi.order_item_id)
        s = it.snapshot
        out.append({"description": s.get("customs_description") or f"{s['brand']} {s['model']}",
                    "quantity": pi.quantity, "value": round(it.unit_price_minor * pi.quantity / 100, 2),
                    "currency": o.currency, "weight": round(s["weight_g"] * pi.quantity / 28.3495, 1),
                    "origin_country": s["origin_country"], "hs_tariff_number": s.get("hs_code")})
    return out


def buy_label(db: Session, o: HardwareOrder, p: Parcel, actor_id: uuid.UUID, service_code: str | None = None) -> Parcel:
    if p.status not in ("packing",) or p.label_asset_id:
        if p.label_asset_id:
            return p  # idempotent
        raise ApiError("invalid_state", 409)
    if o.shipping_rate and o.shipping_rate.get("type") == "freight_quote":
        raise ApiError("manual_shipment_required", 409)
    adapter = get_shipping_adapter()
    if p.label_idempotency_key is None:
        p.label_idempotency_key = f"label:{p.id}"
        db.commit()  # persist the key before calling the carrier
    wh = db.get(Warehouse, o.warehouse_id)
    service = service_code or (o.shipping_rate or {}).get("service", "standard")
    try:
        res = adapter.create_shipment(idempotency_key=p.label_idempotency_key, service_code=service,
                                      origin={"country": wh.country, "fields": wh.address}, destination=o.address,
                                      package=Package(p.weight_g, p.length_mm, p.width_mm, p.height_mm),
                                      customs=_customs_lines(o, p), existing_external_id=p.external_shipment_id)
    except CarrierTimeout:
        raise ApiError("carrier_timeout", 504)
    except (RouteUnsupported, ManualQuoteRequired):
        raise ApiError("manual_shipment_required", 409)
    key = new_key("label", None, "pdf")
    import tempfile, os
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as fh:
        fh.write(res.label_pdf)
        tmp = fh.name
    get_storage().put_file(key, tmp, "application/pdf")
    os.remove(tmp)
    a = Asset(owner_id=None, kind="label", storage_key=key, content_type="application/pdf",
              size_bytes=len(res.label_pdf), sha256=hashlib.sha256(res.label_pdf).hexdigest(), status="ready",
              immutable=True)
    db.add(a)
    db.flush()
    p.label_asset_id = a.id
    p.external_shipment_id, p.tracking_number, p.carrier, p.adapter = res.external_id, res.tracking_number, res.carrier, adapter.name
    p.status, p.status_source = "label_created", "carrier" if adapter.name != "mock" else "simulator"
    p.documents = list(p.documents or []) + [{"kind": "label", "asset_id": str(a.id)}]
    _add_event(db, p, "simulator" if adapter.name == "mock" else "carrier", f"label:{p.id}", "label_created",
               utcnow(), "Label created")
    audit(db, actor_id, "parcel.label_created", "hardware_order", o.id, {"parcel": p.number})
    return p


def manual_shipment(db: Session, o: HardwareOrder, p: Parcel, actor_id: uuid.UUID, carrier: str,
                    tracking_number: str) -> Parcel:
    if p.status != "packing":
        raise ApiError("invalid_state", 409)
    if not carrier.strip() or not tracking_number.strip():
        raise ApiError("validation_failed", 422)
    p.carrier, p.tracking_number, p.adapter = carrier.strip()[:64], tracking_number.strip()[:64], "manual"
    p.status, p.status_source = "label_created", "supplier"
    _add_event(db, p, "supplier", f"manual-label:{p.id}", "label_created", utcnow(), "Shipment arranged externally")
    audit(db, actor_id, "parcel.manual_shipment", "hardware_order", o.id, {"parcel": p.number})
    return p


def attach_document(db: Session, p: Parcel, kind: str, asset: Asset) -> None:
    p.documents = list(p.documents or []) + [{"kind": kind, "asset_id": str(asset.id)}]


def handover(db: Session, o: HardwareOrder, p: Parcel, actor_id: uuid.UUID) -> Parcel:
    """Physical hand-over to the carrier: this (not the label) ships stock."""
    if p.status != "label_created":
        raise ApiError("invalid_state", 409)
    for pi in p.items:
        it = next(i for i in o.items if i.id == pi.order_item_id)
        if it.shipped_qty + pi.quantity > it.quantity:
            raise ApiError("exceeds_ordered_quantity", 422, sku=it.snapshot["code"])
        it.shipped_qty += pi.quantity
        if not it.snapshot.get("made_to_order"):
            ship_quantity(db, o.id, it.sku_id, pi.quantity)
    _add_event(db, p, "supplier", f"handover:{p.id}", "handed_over", utcnow(), "Handed over to carrier")
    db.flush()
    recompute_fulfillment(db, o)
    notify(db, o.buyer_id, o.locale, "hw_shipped", f"hwship:{p.id}", order=o.number,
           tracking=p.tracking_number or "—", carrier=p.carrier or "—")
    audit(db, actor_id, "parcel.handed_over", "hardware_order", o.id, {"parcel": p.number})
    return p


def _add_event(db: Session, p: Parcel, source: str, external_id: str, status: str, occurred_at: datetime,
               description: str = "", location: str | None = None) -> bool:
    res = db.execute(insert(TrackingEvent).values(
        id=uuid.uuid4(), parcel_id=p.id, source=source, external_id=external_id, status=status,
        description=description[:500], location=location, occurred_at=occurred_at)
        .on_conflict_do_nothing(index_elements=["parcel_id", "source", "external_id"]).returning(TrackingEvent.id))
    inserted = res.scalar() is not None
    if inserted:
        db.flush()
        _recompute_parcel(db, p)
    return inserted


def _recompute_parcel(db: Session, p: Parcel) -> None:
    """Reconcile out-of-order events: terminal statuses win, otherwise the latest occurrence."""
    evs = list(db.scalars(select(TrackingEvent).where(TrackingEvent.parcel_id == p.id)))
    if not evs:
        return
    terminal = [e for e in evs if e.status in ("delivered", "returned_to_sender", "cancelled")]
    pick = max(terminal or evs, key=lambda e: (e.occurred_at, STATUS_RANK.get(e.status, 0)))
    p.status, p.status_source = pick.status, pick.source


def recompute_fulfillment(db: Session, o: HardwareOrder) -> None:
    parcels = list(db.scalars(select(Parcel).where(Parcel.order_id == o.id, Parcel.status != "cancelled")))
    all_shipped = all(it.shipped_qty == it.quantity for it in o.items)
    any_shipped = any(it.shipped_qty > 0 for it in o.items)
    if any(p.status in ("exception", "returned_to_sender") for p in parcels):
        o.fulfillment_status = "exception"
    elif all_shipped and parcels and all(p.status == "delivered" for p in parcels if p.items):
        o.fulfillment_status = "delivered"
    elif all_shipped:
        o.fulfillment_status = "shipped"
    elif any_shipped:
        o.fulfillment_status = "partially_shipped"
    elif parcels:
        o.fulfillment_status = "processing"
    if o.fulfillment_status == "delivered" and o.status == "confirmed":
        notify(db, o.buyer_id, o.locale, "hw_delivered", f"hwdeliv:{o.id}", order=o.number)


def supplier_event(db: Session, o: HardwareOrder, p: Parcel, actor_id: uuid.UUID, status: str, description: str,
                   occurred_at: datetime | None, external_id: str | None) -> bool:
    if status not in STATUS_RANK or status in ("packing", "label_created", "handed_over"):
        raise ApiError("validation_failed", 422)
    if p.status in ("packing",):
        raise ApiError("invalid_state", 409)
    ok = _add_event(db, p, "supplier", external_id or f"sup:{uuid.uuid4().hex}", status, occurred_at or utcnow(),
                    description)
    recompute_fulfillment(db, o)
    audit(db, actor_id, "parcel.status_manual", "hardware_order", o.id, {"parcel": p.number, "status": status})
    return ok


def apply_shipping_event(db: Session, source: str, payload: dict) -> None:
    adapter = get_shipping_adapter({"mock_ship": "mock", "easypost": "easypost"}[source])
    for ev in adapter.normalize(payload):
        p = db.scalar(select(Parcel).where(Parcel.tracking_number == ev.tracking_number).with_for_update())
        if p is None:
            from .billing import RetryLater
            raise RetryLater("unknown tracking number")
        if p.status == "packing":
            from .billing import RetryLater
            raise RetryLater("parcel not yet labelled")
        _add_event(db, p, ev.source, ev.external_id, ev.status, ev.occurred_at, ev.description, ev.location)
        recompute_fulfillment(db, db.get(HardwareOrder, p.order_id))


def simulate_tracking(p: Parcel, status: str, occurred_at: datetime | None = None, event_key: str | None = None) -> dict:
    """Simulator: emits a signed carrier-style webhook (source=simulator) through the normal inbox path."""
    from ..adapters.shipping import MockShippingAdapter
    occurred_at = occurred_at or utcnow()
    eid = "mock_trk_" + hashlib.sha256((event_key or f"{p.id}:{status}:{occurred_at.isoformat()}").encode()).hexdigest()[:20]
    ev = {"id": eid, "type": "tracker.updated", "test_mode": True,
          "data": {"tracking_number": p.tracking_number, "status": status, "occurred_at": occurred_at.isoformat(),
                   "description": f"[SIMULATOR] {status}"}}
    raw = json.dumps(ev).encode()
    return receive_shipping_webhook("mock_ship", raw, {"x-mock-signature": MockShippingAdapter.sign(raw)})


def receive_shipping_webhook(source: str, raw: bytes, headers: dict) -> dict:
    from ..adapters.shipping import ShippingSignatureError
    from ..models import WebhookInbox
    from .billing import process_inbox_item
    adapter = get_shipping_adapter({"mock_ship": "mock", "easypost": "easypost"}[source])
    try:
        event = adapter.verify_webhook(raw, {k.lower(): v for k, v in headers.items()})
    except ShippingSignatureError:
        raise ApiError("invalid_signature", 400)
    with session_scope() as db:
        res = db.execute(insert(WebhookInbox).values(id=uuid.uuid4(), source=source, event_id=adapter.event_id(event),
                                                     event_type=str(event.get("type") or event.get("description")),
                                                     payload=event)
                         .on_conflict_do_nothing(index_elements=["source", "event_id"]).returning(WebhookInbox.id))
        iid = res.scalar()
    if iid is None:
        return {"received": True, "duplicate": True}
    try:
        process_inbox_item(iid)
    except Exception:
        pass
    return {"received": True, "duplicate": False}


# ------------------------------------------------------------------ returns & refunds

def request_return(db: Session, o: HardwareOrder, items: list[dict], reason: str) -> ReturnRequest:
    if not reason.strip() or not items:
        raise ApiError("validation_failed", 422)
    pending = {}
    for rr in db.scalars(select(ReturnRequest).where(ReturnRequest.order_id == o.id,
                                                     ReturnRequest.status.notin_(("rejected", "closed")))):
        for row in rr.items:
            pending[row["order_item_id"]] = pending.get(row["order_item_id"], 0) + row["quantity"]
    clean = []
    for row in items:
        it = next((i for i in o.items if str(i.id) == str(row["order_item_id"])), None)
        q = int(row["quantity"])
        if not it or q <= 0 or q > it.shipped_qty - it.returned_qty - pending.get(str(it.id), 0):
            raise ApiError("return_exceeds_shipped", 422)
        clean.append({"order_item_id": str(it.id), "quantity": q})
    r = ReturnRequest(order_id=o.id, items=clean, reason=reason[:2000])
    db.add(r)
    db.flush()
    audit(db, o.buyer_id, "return.requested", "hardware_order", o.id)
    return r


RETURN_FLOW = {"approve": ("requested", "approved"), "reject": ("requested", "rejected"),
               "ship": ("approved", "in_transit"), "receive": ("in_transit", "received"),
               "inspect": ("received", "inspected"), "close": ("inspected", "closed")}


def return_transition(db: Session, r: ReturnRequest, action: str, actor_id: uuid.UUID, data: dict) -> ReturnRequest:
    if action not in RETURN_FLOW or r.status != RETURN_FLOW[action][0]:
        raise ApiError("invalid_state", 409)
    o = db.get(HardwareOrder, r.order_id, with_for_update=True)
    if action in ("approve", "reject"):
        r.decision_note = str(data.get("note", ""))[:2000]
    if action == "ship":
        r.return_tracking = str(data.get("tracking_number", ""))[:64] or None
    if action == "inspect":
        insp = {}
        for row in r.items:
            res = (data.get("results") or {}).get(row["order_item_id"], {})
            accepted = int(res.get("accepted", row["quantity"]))
            restock_qty = int(res.get("restock", accepted))
            if not 0 <= restock_qty <= accepted <= row["quantity"]:
                raise ApiError("validation_failed", 422)
            insp[row["order_item_id"]] = {"accepted": accepted, "restock": restock_qty}
        r.inspection = insp
        for item_id, res in insp.items():  # stock goes up only after physical receipt + inspection
            it = next(i for i in o.items if str(i.id) == item_id)
            it.returned_qty += res["accepted"]
            if res["restock"] and not it.snapshot.get("made_to_order"):
                restock(db, it.sku_id, o.warehouse_id, res["restock"])
        r.restocked = True
    r.status = RETURN_FLOW[action][1]
    r.updated_at = utcnow()
    notify(db, o.buyer_id, o.locale, f"return_{r.status}", f"ret:{r.id}:{r.status}", order=o.number)
    audit(db, actor_id, f"return.{action}", "hardware_order", o.id, {"return": str(r.id)})
    return r


def request_hardware_refund(db: Session, o: HardwareOrder, actor_id: uuid.UUID, goods: int, shipping: int, fees: int,
                            reason: str, idempotency_key: str) -> Refund:
    existing = db.scalar(select(Refund).where(Refund.idempotency_key == idempotency_key))
    if existing:
        return existing
    if o.payment_status not in ("paid", "partially_refunded"):
        raise ApiError("refund_not_allowed", 409)
    amount = goods + shipping + fees
    refunded_lines = {"goods": 0, "shipping": 0, "fees": 0}
    for rf in db.scalars(select(Refund).where(Refund.context == "hardware", Refund.order_id == o.id,
                                              Refund.status.in_(("pending", "succeeded")))):
        for k in refunded_lines:
            refunded_lines[k] += int(rf.lines.get(k, 0))
    if min(goods, shipping, fees) < 0 or amount <= 0 or \
            goods + refunded_lines["goods"] > o.goods_minor or \
            shipping + refunded_lines["shipping"] > (o.shipping_minor or 0) or \
            fees + refunded_lines["fees"] > o.fees_minor:
        raise ApiError("refund_exceeds_payment", 422)
    r = Refund(context="hardware", order_id=o.id, amount_minor=amount, currency=o.currency,
               lines={"goods": goods, "shipping": shipping, "fees": fees}, reason=reason[:1000],
               idempotency_key=idempotency_key, created_by=actor_id)
    db.add(r)
    db.flush()
    enqueue(db, "payment.refund", {"refund_id": str(r.id)}, f"refund:{r.id}")
    audit(db, actor_id, "hw_refund.requested", "hardware_order", o.id, {"amount_minor": amount})
    return r


def apply_hardware_refund(db: Session, r: Refund, provider: str) -> None:
    o = db.get(HardwareOrder, r.order_id, with_for_update=True)
    seller = db.get(Seller, o.seller_id)
    money.post(db, f"hw_refund:{r.id}", "hardware_refund", o.currency, [
        (f"hardware:{seller.id}:goods", r.lines.get("goods", 0)),
        (f"hardware:{seller.id}:shipping", r.lines.get("shipping", 0)),
        (f"hardware:{seller.id}:fees", r.lines.get("fees", 0)), (f"cash:{provider}", -r.amount_minor)])
    o.refunded_minor += r.amount_minor
    o.payment_status = "refunded" if o.refunded_minor >= (o.total_minor or 0) else "partially_refunded"
    notify(db, o.buyer_id, o.locale, "refund_done", f"refund:{r.id}", amount=f"{r.amount_minor / 100:.2f} {r.currency}")


# ------------------------------------------------------------------ documents

def packing_list(db: Session, o: HardwareOrder, parcel: Parcel | None = None) -> dict:
    """Built strictly from the order snapshot. Missing customs data is flagged, never invented."""
    lines = []
    for it in o.items:
        q = it.quantity
        if parcel is not None:
            pi = next((x for x in parcel.items if x.order_item_id == it.id), None)
            if not pi:
                continue
            q = pi.quantity
        s = it.snapshot
        lines.append({"sku": s["code"], "description": s.get("customs_description") or f"{s['brand']} {s['model']}",
                      "quantity": q, "unit_value_minor": it.unit_price_minor, "total_value_minor": it.unit_price_minor * q,
                      "currency": o.currency, "origin_country": s["origin_country"],
                      "hs_code": s.get("hs_code"), "hs_code_status": "confirmed" if s.get("hs_code") else "not_provided",
                      "contains_battery": s.get("contains_battery", "none"),
                      "net_weight_g": s["weight_g"] * q})
    return {"order": o.number, "parcel": parcel.number if parcel else None, "currency": o.currency,
            "ship_to": o.address, "terms": o.terms, "lines": lines,
            "goods_total_minor": sum(line["total_value_minor"] for line in lines),
            "warnings": sorted({"hs_code_missing" for line in lines if not line["hs_code"]} |
                               {"battery_restrictions" for line in lines if line["contains_battery"] != "none"}),
            "generated_from": "order_snapshot", "demo": o.is_demo}


def packing_list_pdf(doc: dict) -> bytes:
    lines = [f"PACKING LIST / COMMERCIAL INVOICE DATA - {doc['order']}" + (f" parcel {doc['parcel']}" if doc['parcel'] else ""),
             "DEMO DOCUMENT" if doc["demo"] else "", f"Terms: {doc['terms'].get('incoterm')} duties payer: "
             f"{doc['terms'].get('duties_payer')}", ""]
    for line in doc["lines"]:
        lines.append(f"{line['sku']} | {line['description']} | qty {line['quantity']} | "
                     f"{line['total_value_minor'] / 100:.2f} {line['currency']} | origin {line['origin_country']} | "
                     f"HS {line['hs_code'] or 'NOT PROVIDED'}")
    lines += ["", f"Total goods: {doc['goods_total_minor'] / 100:.2f} {doc['currency']}"]
    if doc["warnings"]:
        lines.append("Warnings: " + ", ".join(doc["warnings"]))
    return minimal_pdf([x for x in lines if x is not None])
