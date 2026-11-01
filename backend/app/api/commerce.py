from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import ratelimit
from ..config import get_settings
from ..errors import ApiError
from ..models import (App, DigitalOrder, Entitlement, HardwareOrder, PaymentAttempt, Price, Subscription)
from ..services import billing
from ..services.hardware_orders import receive_shipping_webhook
from .catalog import app_card
from .deps import DB, P, as_uuid, idem, locale_of

router = APIRouter(tags=["purchases/subscriptions/entitlements"])


class CheckoutIn(BaseModel):
    app_id: str
    kind: str  # one_time / subscription / free  (price itself is defined by the server)


@router.post("/checkout/digital", status_code=201)
def checkout(body: CheckoutIn, request: Request, p=P, db=DB, key=Depends(idem)):
    ratelimit.check(request, "checkout", p.id)
    u = p.require_user()
    if body.kind not in ("one_time", "subscription", "free"):
        raise ApiError("validation_failed", 422, details={"field": "kind"})
    return billing.start_checkout(db, db.merge(u), as_uuid(body.app_id, "app"), body.kind, locale_of(request), key)


class PortalIn(BaseModel):
    return_path: str = "/account"


@router.post("/billing/portal")
def billing_portal(body: PortalIn, request: Request, p=P, db=DB):
    """NAP-012: provider's billing portal — saved cards, invoices, subscription renewal and cancellation."""
    u = p.require_user()
    path = body.return_path if body.return_path.startswith("/") and not body.return_path.startswith("//") else "/account"
    url = billing.portal_url(db, db.merge(u), f"{get_settings().public_base_url}/{locale_of(request)}{path}")
    return {"url": url}


@router.get("/mock-checkout/{ref}")
def mock_checkout(ref: str, request: Request, p=P, db=DB):
    if get_settings().env == "production":
        raise ApiError("not_found", 404, entity="checkout")
    u = p.require_user()
    att = db.scalar(select(PaymentAttempt).where(PaymentAttempt.provider_ref == ref))
    if not att or att.provider != "mock":
        raise ApiError("not_found", 404, entity="checkout")
    billing._check_attempt_owner(db, att, u)
    desc = {}
    if att.context == "digital":
        o = db.get(DigitalOrder, att.order_id)
        desc = {"kind": o.kind, "app": app_card(db, db.get(App, o.app_id), locale_of(request)),
                "interval": db.get(Price, o.price_id).interval}
    else:
        o = db.get(HardwareOrder, att.order_id)
        desc = {"kind": "hardware", "order": o.number, "order_id": str(o.id)}
    return {"ref": ref, "context": att.context, "status": att.status, "amount_minor": att.amount_minor,
            "currency": att.currency, "test_mode": True, **desc}


class MockOutcome(BaseModel):
    outcome: str  # success / fail


@router.post("/mock-checkout/{ref}/complete")
def mock_complete(ref: str, body: MockOutcome, request: Request, p=P, db=DB):
    """TEST MODE ONLY. The redirect does not confirm payment: this emits signed provider webhooks that go through
    the same verification/inbox path as a real provider."""
    ratelimit.check(request, "checkout", p.id)
    if get_settings().env == "production":
        raise ApiError("not_found", 404, entity="checkout")
    u = p.require_user()
    if body.outcome not in ("success", "fail"):
        raise ApiError("validation_failed", 422)
    return billing.mock_complete_checkout(db, ref, body.outcome, u)


@router.get("/orders/digital/{order_id}")
def digital_order(order_id: str, p=P, db=DB):
    u = p.require_user()
    o = db.get(DigitalOrder, as_uuid(order_id, "order"))
    if not o or o.buyer_id != u.id:
        raise ApiError("not_found", 404, entity="order")
    return {"id": str(o.id), "status": o.status, "kind": o.kind, "amount_minor": o.amount_minor,
            "currency": o.currency, "app_id": str(o.app_id), "access": billing.access_for(db, u.id, o.app_id)}


@router.get("/library")
def library(request: Request, p=P, db=DB):
    u = p.require_user()
    loc = locale_of(request)
    app_ids = set(db.scalars(select(Entitlement.app_id).where(Entitlement.user_id == u.id)))
    items = []
    for aid in app_ids:
        a = db.get(App, aid)
        acc = billing.access_for(db, u.id, aid)
        if not a.published_version_id:
            continue
        items.append(app_card(db, a, loc, detail=True) | {"access": acc})
    return {"items": sorted(items, key=lambda x: not x["access"]["has_access"])}


@router.get("/purchases")
def purchases(p=P, db=DB):
    u = p.require_user()
    rows = db.scalars(select(DigitalOrder).where(DigitalOrder.buyer_id == u.id).order_by(DigitalOrder.created_at.desc()))
    out = []
    for o in rows:
        req = billing.latest_refund_request(db, o.id)  # NAP-024
        out.append({"id": str(o.id), "app_id": str(o.app_id), "app_slug": db.get(App, o.app_id).slug,
                    "kind": o.kind, "amount_minor": o.amount_minor, "refunded_minor": o.refunded_minor,
                    "currency": o.currency, "status": o.status, "created_at": o.created_at.isoformat(),
                    "refund_request": billing.refund_request_view(req),
                    "can_request_refund": o.status in ("paid", "partially_refunded")
                    and not (req and req.status in ("requested", "pending"))
                    and billing._refundable_left(db, o) > 0})
    return {"items": out}


class RefundRequestIn(BaseModel):
    reason: str = Field("", max_length=1000)
    amount_minor: int | None = None
    withdrawal: bool = False  # NAP-062: "withdrawal from the contract" — no reason required


@router.post("/purchases/{order_id}/refund-request", status_code=201)
def request_refund(order_id: str, body: RefundRequestIn, p=P, db=DB):
    """NAP-024: the buyer asks for a refund; an administrator decides, the money goes back through the provider."""
    u = p.require_user()
    r = billing.buyer_request_refund(db, u.id, as_uuid(order_id, "order"), body.reason, body.amount_minor,
                                     body.withdrawal)
    db.commit()
    return billing.refund_request_view(r)


@router.get("/subscriptions")
def subscriptions(p=P, db=DB):
    u = p.require_user()
    rows = db.scalars(select(Subscription).where(Subscription.user_id == u.id).order_by(Subscription.created_at.desc()))
    out = []
    for s in rows:
        price = db.get(Price, s.price_id)
        out.append({"id": str(s.id), "app_slug": db.get(App, s.app_id).slug, "status": s.status, "provider": s.provider,
                    "cancel_at_period_end": s.cancel_at_period_end,
                    "paid_until": s.paid_until.isoformat() if s.paid_until else None,
                    "price": {"amount_minor": price.amount_minor, "currency": price.currency, "interval": price.interval},
                    "periods": [{"start": x.period_start.isoformat(), "end": x.period_end.isoformat(),
                                 "status": x.status} for x in s.periods],
                    "access": billing.access_for(db, u.id, s.app_id)})
    return {"items": out}


@router.post("/subscriptions/{sub_id}/cancel")
def cancel_sub(sub_id: str, p=P, db=DB):
    u = p.require_user()
    s = billing.cancel_autorenew(db, db.merge(u), as_uuid(sub_id, "subscription"))
    db.commit()
    return {"id": str(s.id), "cancel_at_period_end": s.cancel_at_period_end,
            "paid_until": s.paid_until.isoformat() if s.paid_until else None}


@router.get("/entitlements/{app_id}")
def entitlement(app_id: str, p=P, db=DB):
    u = p.require_user()
    p.require_scope("entitlements:read")
    return billing.access_for(db, u.id, as_uuid(app_id, "app"))


@router.post("/webhooks/payments/{provider}", tags=["webhooks"])
async def payment_webhook(provider: str, request: Request):
    ratelimit.check(request, "webhook", key=ratelimit.who(request))
    if provider not in ("mock", "stripe", "yookassa") or (provider == "mock" and get_settings().env == "production"):
        raise ApiError("not_found", 404, entity="provider")
    raw = await request.body()  # signature is verified on the exact raw body
    return billing.receive_webhook(provider, raw, dict(request.headers))


@router.post("/webhooks/shipping/{source}", tags=["webhooks"])
async def shipping_webhook(source: str, request: Request):
    ratelimit.check(request, "webhook", key=ratelimit.who(request))
    if source not in ("mock_ship", "easypost") or (source == "mock_ship" and get_settings().env == "production"):
        raise ApiError("not_found", 404, entity="provider")
    raw = await request.body()
    return receive_shipping_webhook(source, raw, dict(request.headers))
