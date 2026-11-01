"""Digital purchases, subscriptions, entitlements, 30/70 split, refunds, payouts, webhooks (inbox/outbox)."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta

from dateutil.relativedelta import relativedelta
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..adapters.payments import (MockPaymentProvider, NormalizedEvent, ProviderUnavailable, WebhookSignatureError,
                                 get_payment_provider, mock_sign, provider_by_name)
from ..config import get_settings
from ..db import session_scope, utcnow
from ..errors import ApiError
from ..models import (App, AppVersion, DigitalOrder, Entitlement, Outbox, PaymentAttempt, Payout, Price, Refund, RevenueShare,
                      Subscription, SubscriptionPeriod, User, WebhookInbox)
from . import money
from .common import audit, enqueue, notify

log = logging.getLogger("nap.billing")


class RetryLater(Exception):
    """Event arrived out of order; keep it in the inbox and retry."""


# ------------------------------------------------------------------ access

def access_for(db: Session, user_id: uuid.UUID, app_id: uuid.UUID) -> dict:
    now = utcnow()
    grace = timedelta(days=get_settings().subscription_grace_days)
    ents = db.scalars(select(Entitlement).where(Entitlement.user_id == user_id, Entitlement.app_id == app_id,
                                                Entitlement.status == "active")).all()
    for e in ents:
        if e.source in ("one_time", "free", "grant"):
            return {"has_access": True, "source": e.source, "paid_until": None, "entitlement_id": str(e.id)}
    for e in ents:
        if e.source == "subscription" and e.subscription_id:
            sub = db.get(Subscription, e.subscription_id)
            for p in sub.periods:
                if p.status != "refunded" and p.period_start <= now < p.period_end + grace:
                    return {"has_access": True, "source": "subscription", "paid_until": sub.paid_until.isoformat()
                            if sub.paid_until else None, "entitlement_id": str(e.id)}
    return {"has_access": False, "source": None, "paid_until": None, "entitlement_id": None}


def _recompute_paid_until(sub: Subscription) -> None:
    ends = [p.period_end for p in sub.periods if p.status != "refunded"]
    sub.paid_until = max(ends) if ends else None


# ------------------------------------------------------------------ checkout

def start_checkout(db: Session, user: User, app_id: uuid.UUID, kind: str, locale: str,
                   idempotency_key: str | None) -> dict:
    app = db.get(App, app_id)
    if not app or not app.published_version_id or app.sales_status != "active":
        raise ApiError("not_for_sale", 409)
    price = db.scalar(select(Price).where(Price.app_id == app.id, Price.active.is_(True), Price.kind == kind))
    if not price:
        raise ApiError("not_for_sale", 409)
    if access_for(db, user.id, app.id)["has_access"]:
        raise ApiError("already_owned", 409)
    if idempotency_key:
        existing = db.scalar(select(DigitalOrder).where(DigitalOrder.buyer_id == user.id,
                                                        DigitalOrder.idempotency_key == idempotency_key))
        if existing:
            att = db.scalar(select(PaymentAttempt).where(PaymentAttempt.order_id == existing.id,
                                                         PaymentAttempt.context == "digital"))
            return {"order_id": str(existing.id), "status": existing.status,
                    "checkout_url": att.checkout_url if att else None, "test_mode": att.test_mode if att else True}
    if kind == "free":
        db.execute(insert(Entitlement).values(id=uuid.uuid4(), user_id=user.id, app_id=app.id, source="free",
                                              status="active").on_conflict_do_nothing())
        audit(db, user.id, "entitlement.free", "app", app.id)
        db.commit()
        return {"order_id": None, "status": "granted", "checkout_url": None, "test_mode": False}
    s = get_settings()
    sub = None
    if kind == "subscription":
        sub = Subscription(user_id=user.id, app_id=app.id, price_id=price.id, provider=get_payment_provider().name)
        db.add(sub)
        db.flush()
    order = DigitalOrder(buyer_id=user.id, app_id=app.id, developer_id=app.developer_id, price_id=price.id, kind=kind,
                         amount_minor=price.amount_minor, tax_minor=0, currency=price.currency,
                         commission_bps=s.platform_commission_bps, locale=locale, idempotency_key=idempotency_key,
                         subscription_id=sub.id if sub else None)
    db.add(order)
    db.flush()
    db.commit()  # the order exists before the external call (no distributed transaction)
    provider = get_payment_provider()
    title = (db.get(App, app.id).slug)
    try:
        cs = provider.create_checkout(
            context="digital", order_id=str(order.id), amount_minor=order.amount_minor, currency=order.currency,
            description=f"NAP Store: {title}", mode="subscription" if kind == "subscription" else "payment",
            interval=price.interval, subscription_id=str(sub.id) if sub else None,
            success_url=f"{s.public_base_url}/{locale}/library?order={order.id}",
            cancel_url=f"{s.public_base_url}/{locale}/apps/{app.slug}", idempotency_key=f"digital:{order.id}",
            customer_email=user.email)
    except ProviderUnavailable as e:
        raise ApiError("payment_provider_unavailable", 503, reason=str(e))
    att = PaymentAttempt(context="digital", order_id=order.id, provider=provider.name, provider_ref=cs.provider_ref,
                         amount_minor=order.amount_minor, currency=order.currency, checkout_url=cs.url,
                         test_mode=cs.test_mode)
    db.add(att)
    audit(db, user.id, "checkout.started", "digital_order", order.id, {"kind": kind, "provider": provider.name})
    db.commit()
    return {"order_id": str(order.id), "status": order.status, "checkout_url": cs.url, "test_mode": cs.test_mode}


def cancel_autorenew(db: Session, user: User, sub_id: uuid.UUID) -> Subscription:
    sub = db.get(Subscription, sub_id, with_for_update=True)
    if not sub or sub.user_id != user.id:
        raise ApiError("not_found", 404, entity="subscription")
    if sub.cancel_at_period_end or sub.status in ("canceled", "ended"):
        return sub
    provider_by_name(sub.provider).cancel_subscription(sub.provider_ref, str(sub.id))
    sub.cancel_at_period_end = True
    audit(db, user.id, "subscription.autorenew_canceled", "subscription", sub.id)
    notify(db, user.id, user.locale, "subscription_canceled", f"subcancel:{sub.id}",
           until=sub.paid_until.date().isoformat() if sub.paid_until else "—")
    return sub


# ------------------------------------------------------------------ webhooks / inbox

def receive_webhook(source: str, raw: bytes, headers: dict[str, str]) -> dict:
    """Verify the signature on the raw body, durably store the unique event, then acknowledge."""
    provider = provider_by_name(source)
    try:
        event = provider.verify_webhook(raw, {k.lower(): v for k, v in headers.items()})
    except WebhookSignatureError:
        raise ApiError("invalid_signature", 400)
    eid = provider.event_id(event)
    with session_scope() as db:
        res = db.execute(insert(WebhookInbox).values(id=uuid.uuid4(), source=source, event_id=eid,
                                                     event_type=str(event.get("type")), payload=event)
                         .on_conflict_do_nothing(index_elements=["source", "event_id"]).returning(WebhookInbox.id))
        inbox_id = res.scalar()
    if inbox_id is None:
        return {"received": True, "duplicate": True}
    try:
        process_inbox_item(inbox_id)
    except Exception:  # stored; the periodic processor retries
        log.exception("inbox processing deferred")
    return {"received": True, "duplicate": False}


def process_inbox_item(inbox_id: uuid.UUID) -> bool:
    with session_scope() as db:
        item = db.scalar(select(WebhookInbox).where(WebhookInbox.id == inbox_id, WebhookInbox.processed_at.is_(None))
                         .with_for_update(skip_locked=True))
        if item is None:
            return False
        provider = provider_by_name(item.source) if item.source in ("mock", "stripe") else None
        try:
            if provider is None:
                from .hardware_orders import apply_shipping_event
                apply_shipping_event(db, item.source, item.payload)
            else:
                for ev in provider.normalize(item.payload):
                    apply_event(db, provider.name, ev)
            item.processed_at = utcnow()
            item.attempts += 1
            item.last_error = None
            return True
        except RetryLater as e:
            db.rollback()
            err = "retry_later: " + str(e)
        except Exception as e:  # noqa
            db.rollback()
            log.exception("inbox item failed")
            err = repr(e)[:1000]
    with session_scope() as db:
        it = db.get(WebhookInbox, inbox_id)
        it.attempts += 1
        it.last_error = err
    return False


def process_inbox(limit: int = 100) -> int:
    with session_scope() as db:
        ids = list(db.scalars(select(WebhookInbox.id).where(WebhookInbox.processed_at.is_(None),
                                                            WebhookInbox.attempts < 50)
                              .order_by(WebhookInbox.received_at).limit(limit)))
    return sum(1 for i in ids if process_inbox_item(i))


def apply_event(db: Session, provider: str, ev: NormalizedEvent) -> None:
    h = {"checkout.succeeded": _on_checkout_succeeded, "checkout.failed": _on_checkout_failed,
         "invoice.paid": _on_invoice_paid, "invoice.failed": _on_invoice_failed,
         "subscription.canceled": _on_subscription_canceled, "refund.succeeded": _on_refund_succeeded,
         "refund.failed": _on_refund_failed, "payout.paid": _on_payout_paid, "payout.failed": _on_payout_failed}
    fn = h.get(ev.type)
    if fn:
        fn(db, provider, ev.data)


def _attempt(db: Session, ref: str) -> PaymentAttempt:
    att = db.scalar(select(PaymentAttempt).where(PaymentAttempt.provider_ref == ref).with_for_update())
    if att is None:
        raise RetryLater("unknown attempt")
    return att


def _on_checkout_succeeded(db: Session, provider: str, d: dict) -> None:
    att = _attempt(db, d["attempt_ref"])
    if att.status == "succeeded":
        return
    att.status = "succeeded"
    att.payment_ref = d.get("payment_ref")
    att.confirmed_at = utcnow()
    if att.context == "hardware":
        from .hardware_orders import confirm_payment
        confirm_payment(db, att)
        return
    order = db.get(DigitalOrder, att.order_id, with_for_update=True)
    if order.kind == "subscription":
        return  # access is granted per paid period (invoice.paid), not by the checkout itself
    _mark_paid(db, order, provider, att.payment_ref)
    db.execute(insert(Entitlement).values(id=uuid.uuid4(), user_id=order.buyer_id, app_id=order.app_id,
                                          source="one_time", order_id=order.id, status="active")
               .on_conflict_do_nothing(index_elements=["order_id"]))
    app = db.get(App, order.app_id)
    from ..api.about import legal_version  # NAP-062: durable-medium confirmation of the immediate-access consent
    notify(db, order.buyer_id, order.locale, "purchase_paid", f"paid:{order.id}", app=app.slug,
           amount=_fmt(order.amount_minor, order.currency), terms_version=legal_version("terms") or "1.0")


def _mark_paid(db: Session, order: DigitalOrder, provider: str, payment_ref: str | None) -> None:
    if order.status == "paid":
        return
    order.status = "paid"
    order.paid_at = utcnow()
    gross = order.amount_minor
    p, d = money.split(gross, order.commission_bps)
    money.post(db, f"sale:{order.id}", "digital_sale", order.currency, [
        (f"cash:{provider}", gross + order.tax_minor), ("platform:revenue", -p),
        (money.dev_payable(order.developer_id), -d), ("tax:payable", -order.tax_minor)],
        memo=f"order {order.id}")
    db.execute(insert(RevenueShare).values(
        id=uuid.uuid4(), order_id=order.id, developer_id=order.developer_id, currency=order.currency,
        commission_bps=order.commission_bps, gross_minor=gross, refunded_minor=0, platform_minor=p, developer_minor=d,
        available_at=order.paid_at + timedelta(days=get_settings().payout_hold_days))
        .on_conflict_do_nothing(index_elements=["order_id"]))


def _on_checkout_failed(db: Session, provider: str, d: dict) -> None:
    att = _attempt(db, d["attempt_ref"])
    if att.status in ("succeeded", "failed"):
        return
    att.status = "failed"
    if att.context == "hardware":
        from .hardware_orders import payment_failed
        payment_failed(db, att)
        return
    order = db.get(DigitalOrder, att.order_id)
    if order.status == "pending":
        order.status = "failed"


def _sub_from(db: Session, d: dict) -> Subscription:
    sub = None
    if d.get("subscription_id"):
        sub = db.get(Subscription, uuid.UUID(d["subscription_id"]), with_for_update=True)
    if sub is None and d.get("provider_subscription_ref"):
        sub = db.scalar(select(Subscription).where(Subscription.provider_ref == d["provider_subscription_ref"])
                        .with_for_update())
    if sub is None:
        raise RetryLater("unknown subscription")
    if d.get("provider_subscription_ref") and not sub.provider_ref:
        sub.provider_ref = d["provider_subscription_ref"]
    return sub


def _on_invoice_paid(db: Session, provider: str, d: dict) -> None:
    sub = _sub_from(db, d)
    if db.scalar(select(SubscriptionPeriod).where(SubscriptionPeriod.invoice_ref == d["invoice_ref"])):
        return
    price = db.get(Price, sub.price_id)
    start = datetime.fromisoformat(d["period_start"])
    end = datetime.fromisoformat(d["period_end"])
    amount = int(d.get("amount_minor") if d.get("amount_minor") is not None else price.amount_minor)
    currency = (d.get("currency") or price.currency).upper()
    if currency != price.currency:
        raise ValueError("currency mismatch")
    order = db.scalar(select(DigitalOrder).where(DigitalOrder.subscription_id == sub.id,
                                                 DigitalOrder.status == "pending").with_for_update())
    if order is None:
        app = db.get(App, sub.app_id)
        owner = db.get(User, sub.user_id)
        order = DigitalOrder(buyer_id=sub.user_id, app_id=sub.app_id, developer_id=app.developer_id,
                             price_id=price.id, kind="subscription", amount_minor=amount, tax_minor=0,
                             currency=currency, commission_bps=get_settings().platform_commission_bps,
                             locale=owner.locale, subscription_id=sub.id)
        db.add(order)
        db.flush()
    order.amount_minor = amount
    _mark_paid(db, order, provider, d.get("payment_ref"))
    db.add(SubscriptionPeriod(subscription_id=sub.id, order_id=order.id, invoice_ref=d["invoice_ref"],
                              payment_ref=d.get("payment_ref"), period_start=start, period_end=end))
    db.flush()
    db.refresh(sub)
    if sub.status not in ("canceled",):
        sub.status = "active"
    _recompute_paid_until(sub)
    db.execute(insert(Entitlement).values(id=uuid.uuid4(), user_id=sub.user_id, app_id=sub.app_id,
                                          source="subscription", subscription_id=sub.id, status="active")
               .on_conflict_do_nothing(index_elements=["subscription_id"]))
    notify(db, sub.user_id, order.locale, "subscription_paid", f"inv:{d['invoice_ref']}",
           until=end.date().isoformat(), amount=_fmt(amount, currency))


def _on_invoice_failed(db: Session, provider: str, d: dict) -> None:
    sub = _sub_from(db, d)
    if sub.status in ("active", "incomplete"):
        sub.status = "past_due"  # already-paid periods stay valid until paid_until
    owner = db.get(User, sub.user_id)
    notify(db, sub.user_id, owner.locale, "subscription_payment_failed", f"invfail:{d['invoice_ref']}",
           until=sub.paid_until.date().isoformat() if sub.paid_until else "—")


def _on_subscription_canceled(db: Session, provider: str, d: dict) -> None:
    sub = _sub_from(db, d)
    sub.status = "canceled"
    sub.cancel_at_period_end = True


# ------------------------------------------------------------------ refunds

def request_refund(db: Session, actor_id: uuid.UUID, order_id: uuid.UUID, amount_minor: int, reason: str,
                   idempotency_key: str) -> Refund:
    existing = db.scalar(select(Refund).where(Refund.idempotency_key == idempotency_key))
    if existing:
        return existing
    order = db.get(DigitalOrder, order_id, with_for_update=True)
    if not order or order.status not in ("paid", "partially_refunded"):
        raise ApiError("refund_not_allowed", 409)
    pending = db.scalar(select(func.coalesce(func.sum(Refund.amount_minor), 0)).where(
        Refund.context == "digital", Refund.order_id == order.id, Refund.status == "pending")) or 0
    if amount_minor <= 0 or amount_minor > order.amount_minor - order.refunded_minor - pending:
        raise ApiError("refund_exceeds_payment", 422)
    r = Refund(context="digital", order_id=order.id, amount_minor=amount_minor, currency=order.currency,
               reason=reason[:1000], idempotency_key=idempotency_key, created_by=actor_id)
    db.add(r)
    db.flush()
    enqueue(db, "payment.refund", {"refund_id": str(r.id)}, f"refund:{r.id}")
    audit(db, actor_id, "refund.requested", "digital_order", order.id, {"amount_minor": amount_minor})
    return r


# ------------------------------------------------------------------ buyer refund requests (NAP-024)
# A request is a Refund row with status "requested" (created_by = buyer). The admin approves it — it becomes an
# ordinary "pending" refund sent to the payment provider — or rejects it ("rejected") with a reason the buyer sees.

def _refundable_left(db: Session, order: DigitalOrder) -> int:
    pending = db.scalar(select(func.coalesce(func.sum(Refund.amount_minor), 0)).where(
        Refund.context == "digital", Refund.order_id == order.id, Refund.status == "pending")) or 0
    return order.amount_minor - order.refunded_minor - pending


def open_refund_request(db: Session, order_id: uuid.UUID) -> Refund | None:
    return db.scalar(select(Refund).where(Refund.context == "digital", Refund.order_id == order_id,
                                          Refund.status == "requested"))


def latest_refund_request(db: Session, order_id: uuid.UUID) -> Refund | None:
    return db.scalar(select(Refund).where(Refund.context == "digital", Refund.order_id == order_id,
                                          Refund.lines["source"].astext == "buyer_request")
                     .order_by(Refund.created_at.desc()).limit(1))


def buyer_request_refund(db: Session, buyer_id: uuid.UUID, order_id: uuid.UUID, reason: str,
                         amount_minor: int | None = None, withdrawal: bool = False) -> Refund:
    order = db.get(DigitalOrder, order_id, with_for_update=True)
    if not order or order.buyer_id != buyer_id:
        raise ApiError("not_found", 404, entity="order")
    if order.status not in ("paid", "partially_refunded"):
        raise ApiError("refund_not_allowed", 409)
    if open_refund_request(db, order.id):
        raise ApiError("refund_request_exists", 409)
    reason = (reason or "").strip()
    if withdrawal and not reason:  # NAP-062: withdrawal from the contract needs no explanation (EU/Serbia)
        reason = "withdrawal"
    elif len(reason) < 10:
        raise ApiError("validation_failed", 422, details={"fields": ["reason"]})
    left = _refundable_left(db, order)
    amount = left if amount_minor is None else amount_minor
    if amount <= 0 or amount > left:
        raise ApiError("refund_exceeds_payment", 422)
    r = Refund(context="digital", order_id=order.id, amount_minor=amount, currency=order.currency,
               reason=reason[:1000], status="requested", created_by=buyer_id,
               lines={"source": "buyer_request", "requested_minor": amount, "withdrawal": withdrawal})
    db.add(r)
    db.flush()
    audit(db, buyer_id, "refund.request_created", "digital_order", order.id, {"refund_id": str(r.id), "amount_minor": amount})
    return r


def decide_refund_request(db: Session, admin_id: uuid.UUID, refund_id: uuid.UUID, approve: bool, note: str = "",
                          amount_minor: int | None = None) -> Refund:
    r = db.get(Refund, refund_id, with_for_update=True)
    if not r or r.context != "digital" or (r.lines or {}).get("source") != "buyer_request":
        raise ApiError("not_found", 404, entity="refund_request")
    if r.status != "requested":
        raise ApiError("refund_request_decided", 409)
    order = db.get(DigitalOrder, r.order_id, with_for_update=True)
    note = (note or "").strip()[:1000]
    lines = dict(r.lines or {}) | {"decided_by": str(admin_id), "decided_at": utcnow().isoformat(), "note": note}
    a = db.get(App, order.app_id)
    title = a.slug
    if a.published_version_id:
        from .apps import app_title
        title = app_title(a, db.get(AppVersion, a.published_version_id), order.locale)
    if not approve:
        if len(note) < 3:
            raise ApiError("validation_failed", 422, details={"fields": ["note"]})
        r.status, r.lines = "rejected", lines
        audit(db, admin_id, "refund.request_rejected", "digital_order", order.id, {"refund_id": str(r.id), "note": note})
        notify(db, order.buyer_id, order.locale, "refund_request_rejected", f"refund-rejected:{r.id}",
               app=title, note=note)
        return r
    if order.status not in ("paid", "partially_refunded"):
        raise ApiError("refund_not_allowed", 409)
    amount = r.amount_minor if amount_minor is None else amount_minor
    if amount <= 0 or amount > min(r.amount_minor, _refundable_left(db, order)):
        raise ApiError("refund_exceeds_payment", 422)
    r.amount_minor, r.status, r.lines = amount, "pending", lines
    r.idempotency_key = f"request:{r.id}"
    db.flush()
    enqueue(db, "payment.refund", {"refund_id": str(r.id)}, f"refund:{r.id}")
    audit(db, admin_id, "refund.request_approved", "digital_order", order.id,
          {"refund_id": str(r.id), "amount_minor": amount})
    notify(db, order.buyer_id, order.locale, "refund_request_approved", f"refund-approved:{r.id}",
           app=title, amount=_fmt(amount, r.currency))
    return r


def refund_request_view(r: Refund | None) -> dict | None:
    if r is None:
        return None
    lines = r.lines or {}
    return {"id": str(r.id), "status": r.status, "amount_minor": r.amount_minor, "currency": r.currency,
            "requested_minor": lines.get("requested_minor", r.amount_minor), "reason": r.reason,
            "note": lines.get("note") or None, "withdrawal": bool(lines.get("withdrawal")),
            "created_at": r.created_at.isoformat(),
            "decided_at": lines.get("decided_at")}


def _payment_ref_for(db: Session, r: Refund) -> tuple[str, str]:
    if r.context == "digital":
        order = db.get(DigitalOrder, r.order_id)
        period = db.scalar(select(SubscriptionPeriod).where(SubscriptionPeriod.order_id == order.id))
        if period:
            return db.get(Subscription, period.subscription_id).provider, period.payment_ref or period.invoice_ref
        att = db.scalar(select(PaymentAttempt).where(PaymentAttempt.order_id == order.id,
                                                     PaymentAttempt.status == "succeeded"))
        return att.provider, att.payment_ref or att.provider_ref
    att = db.scalar(select(PaymentAttempt).where(PaymentAttempt.order_id == r.order_id,
                                                 PaymentAttempt.context == "hardware",
                                                 PaymentAttempt.status == "succeeded"))
    return att.provider, att.payment_ref or att.provider_ref


def _on_refund_succeeded(db: Session, provider: str, d: dict) -> None:
    r = None
    if d.get("refund_id"):
        r = db.get(Refund, uuid.UUID(d["refund_id"]), with_for_update=True)
    if r is None and d.get("refund_ref"):
        r = db.scalar(select(Refund).where(Refund.provider_ref == d["refund_ref"]).with_for_update())
    if r is None:
        raise RetryLater("unknown refund")
    if r.status == "succeeded":
        return
    r.status = "succeeded"
    r.provider_ref = r.provider_ref or d.get("refund_ref")
    if r.context == "hardware":
        from .hardware_orders import apply_hardware_refund
        apply_hardware_refund(db, r, provider)
        return
    order = db.get(DigitalOrder, r.order_id, with_for_update=True)
    share = db.scalar(select(RevenueShare).where(RevenueShare.order_id == order.id).with_for_update())
    order.refunded_minor += r.amount_minor
    full = order.refunded_minor == order.amount_minor
    order.status = "refunded" if full else "partially_refunded"
    # recompute target split from the cumulative net to avoid rounding drift
    net = share.gross_minor - order.refunded_minor
    new_p, new_d = money.split(net, share.commission_bps)
    dp, dd = share.platform_minor - new_p, share.developer_minor - new_d
    money.post(db, f"refund:{r.id}", "digital_refund", order.currency, [
        ("platform:revenue", dp), (money.dev_payable(order.developer_id), dd), (f"cash:{provider}", -r.amount_minor)],
        memo=f"refund of order {order.id}")
    share.refunded_minor = order.refunded_minor
    share.platform_minor, share.developer_minor = new_p, new_d
    period = db.scalar(select(SubscriptionPeriod).where(SubscriptionPeriod.order_id == order.id))
    if period is not None:  # subscription refunds touch only their own period
        period.status = "refunded" if full else "partially_refunded"
        sub = db.get(Subscription, period.subscription_id)
        db.flush()
        db.refresh(sub)
        _recompute_paid_until(sub)
    elif full:  # full one-time refund revokes that right; partial refunds keep it (MVP)
        ent = db.scalar(select(Entitlement).where(Entitlement.order_id == order.id))
        if ent and ent.status == "active":
            ent.status, ent.revoked_reason, ent.revoked_at = "revoked", "refund", utcnow()
    notify(db, order.buyer_id, order.locale, "refund_done", f"refund:{r.id}", amount=_fmt(r.amount_minor, r.currency))


def _on_refund_failed(db: Session, provider: str, d: dict) -> None:
    if d.get("refund_id"):
        r = db.get(Refund, uuid.UUID(d["refund_id"]), with_for_update=True)
        if r and r.status == "pending":
            r.status = "failed"


# ------------------------------------------------------------------ payouts

def finance_summary(db: Session, dev_id: uuid.UUID) -> list[dict]:
    now = utcnow()
    out = []
    currencies = set(db.scalars(select(RevenueShare.currency).where(RevenueShare.developer_id == dev_id).distinct()))
    currencies |= set(db.scalars(select(Payout.currency).where(Payout.developer_id == dev_id).distinct()))
    for cur in sorted(currencies):
        q = select(func.coalesce(func.sum(RevenueShare.gross_minor), 0), func.coalesce(func.sum(RevenueShare.refunded_minor), 0),
                   func.coalesce(func.sum(RevenueShare.platform_minor), 0), func.coalesce(func.sum(RevenueShare.developer_minor), 0),
                   func.count()).where(RevenueShare.developer_id == dev_id, RevenueShare.currency == cur)
        gross, refunded, platform, dev, n = db.execute(q).one()
        pending = db.scalar(select(func.coalesce(func.sum(RevenueShare.developer_minor), 0)).where(
            RevenueShare.developer_id == dev_id, RevenueShare.currency == cur, RevenueShare.available_at > now)) or 0
        paid = db.scalar(select(func.coalesce(func.sum(Payout.amount_minor), 0)).where(
            Payout.developer_id == dev_id, Payout.currency == cur, Payout.status == "paid")) or 0
        transit = db.scalar(select(func.coalesce(func.sum(Payout.amount_minor), 0)).where(
            Payout.developer_id == dev_id, Payout.currency == cur, Payout.status == "pending")) or 0
        balance = -money.balance(db, money.dev_payable(dev_id), cur)
        one_time = db.scalar(select(func.count()).select_from(DigitalOrder).where(
            DigitalOrder.developer_id == dev_id, DigitalOrder.currency == cur, DigitalOrder.kind == "one_time",
            DigitalOrder.status.in_(("paid", "partially_refunded", "refunded")))) or 0
        subs = db.scalar(select(func.count()).select_from(DigitalOrder).where(
            DigitalOrder.developer_id == dev_id, DigitalOrder.currency == cur, DigitalOrder.kind == "subscription",
            DigitalOrder.status.in_(("paid", "partially_refunded", "refunded")))) or 0
        available = balance - int(pending)
        out.append({"currency": cur, "sales": int(one_time), "subscription_periods": int(subs),
                    "gross_minor": int(gross), "refunds_minor": int(refunded), "commission_minor": int(platform),
                    "accrued_minor": int(dev), "pending_minor": int(pending), "available_minor": int(available),
                    "in_transit_minor": int(transit), "paid_minor": int(paid), "balance_minor": int(balance),
                    "negative": available < 0, "blocked": available < 0})
    return out


def create_payout(db: Session, actor_id: uuid.UUID, dev_id: uuid.UUID, currency: str) -> Payout:
    summary = {s["currency"]: s for s in finance_summary(db, dev_id)}.get(currency)
    if not summary or summary["available_minor"] <= 0:
        raise ApiError("nothing_to_pay", 409)
    provider = get_payment_provider()
    p = Payout(developer_id=dev_id, amount_minor=summary["available_minor"], currency=currency, provider=provider.name,
               mock=provider.name == "mock")
    db.add(p)
    db.flush()
    money.post(db, f"payout:{p.id}:init", "payout_init", currency, [
        (money.dev_payable(dev_id), p.amount_minor), (money.dev_in_transit(dev_id), -p.amount_minor)])
    enqueue(db, "payment.payout", {"payout_id": str(p.id)}, f"payout:{p.id}")
    audit(db, actor_id, "payout.created", "payout", p.id, {"amount_minor": p.amount_minor, "currency": currency})
    return p


def _on_payout_paid(db: Session, provider: str, d: dict) -> None:
    p = db.get(Payout, uuid.UUID(d["payout_id"]), with_for_update=True)
    if p is None:
        raise RetryLater("unknown payout")
    if p.status == "paid":
        return
    p.status, p.paid_at, p.provider_ref = "paid", utcnow(), p.provider_ref or d.get("payout_ref")
    money.post(db, f"payout:{p.id}:paid", "payout_paid", p.currency, [
        (money.dev_in_transit(p.developer_id), p.amount_minor), (f"cash:{provider}", -p.amount_minor)])


def _on_payout_failed(db: Session, provider: str, d: dict) -> None:
    p = db.get(Payout, uuid.UUID(d["payout_id"]), with_for_update=True)
    if p is None or p.status != "pending":
        return
    p.status = "failed"
    money.post(db, f"payout:{p.id}:reversal", "payout_reversal", p.currency, [
        (money.dev_in_transit(p.developer_id), p.amount_minor), (money.dev_payable(p.developer_id), -p.amount_minor)])


# ------------------------------------------------------------------ outbox dispatcher

def dispatch_outbox(limit: int = 50) -> int:
    done = 0
    with session_scope() as db:
        now = utcnow()
        ids = list(db.scalars(select(Outbox.id).where(Outbox.sent_at.is_(None), Outbox.attempts < 20,
                                                      (Outbox.next_attempt_at.is_(None)) | (Outbox.next_attempt_at <= now))
                              .order_by(Outbox.created_at).limit(limit)))
    for oid in ids:
        with session_scope() as db:
            item = db.scalar(select(Outbox).where(Outbox.id == oid, Outbox.sent_at.is_(None))
                             .with_for_update(skip_locked=True))
            if item is None:
                continue
            topic, payload = item.topic, dict(item.payload)
            item.attempts += 1
        try:
            _dispatch(topic, payload)
            with session_scope() as db:
                db.get(Outbox, oid).sent_at = utcnow()
            done += 1
        except Exception as e:  # retried later
            log.warning("outbox %s failed: %r", topic, e)
            with session_scope() as db:
                it = db.get(Outbox, oid)
                it.last_error = repr(e)[:1000]
                # exponential backoff: 30 s, 1 min, 2 min ... capped at 1 h; 20 attempts span about a day
                it.next_attempt_at = utcnow() + timedelta(seconds=min(3600, 30 * 2 ** max(0, it.attempts - 1)))
    return done


def _dispatch(topic: str, payload: dict) -> None:
    if topic == "payment.refund":
        with session_scope() as db:
            r = db.get(Refund, uuid.UUID(payload["refund_id"]))
            if r.status != "pending":
                return
            provider_name, payment_ref = _payment_ref_for(db, r)
            args = dict(payment_ref=payment_ref, amount_minor=r.amount_minor, currency=r.currency,
                        refund_id=str(r.id), idempotency_key=f"refund:{r.id}")
        ref = provider_by_name(provider_name).refund(**args)
        with session_scope() as db:
            r = db.get(Refund, uuid.UUID(payload["refund_id"]))
            r.provider_ref = r.provider_ref or ref
    elif topic == "payment.payout":
        with session_scope() as db:
            p = db.get(Payout, uuid.UUID(payload["payout_id"]))
            if p.status != "pending":
                return
            dev = db.get(User, p.developer_id)
            args = dict(payout_id=str(p.id), developer_account=dev.payout_account, amount_minor=p.amount_minor,
                        currency=p.currency, idempotency_key=f"payout:{p.id}")
        try:
            ref = provider_by_name(p.provider).payout(**args)
        except ProviderUnavailable:
            with session_scope() as db:
                _on_payout_failed(db, p.provider, {"payout_id": str(p.id)})
            return
        with session_scope() as db:
            pp = db.get(Payout, p.id)
            pp.provider_ref = pp.provider_ref or ref
    elif topic == "notification.email":
        _send_notification_email(payload)
    elif topic.startswith("fulfillment."):
        from .hardware_orders import dispatch_fulfillment
        dispatch_fulfillment(topic, payload)


def _send_notification_email(payload: dict) -> None:
    """NAP-007: deliver a rendered notification by e-mail exactly once (dedupe key), retry on transport errors."""
    from ..adapters.email import EmailError, build_message, send
    from ..models import Notification
    key = payload.get("dedupe_key")
    with session_scope() as db:
        n = db.scalar(select(Notification).where(Notification.dedupe_key == key)) if key else None
        if n is None or n.emailed_at is not None:
            return  # old payloads without a key, or already delivered
        u = db.get(User, n.user_id) if n.user_id else None
        if u is None or not u.email or u.deleted_at:
            n.email_error = "no_address"
            return
        msg = build_message(u.email, n.subject, n.body, n.locale, n.dedupe_key)
        nid = n.id
    try:
        send(msg)
    except EmailError as e:
        with session_scope() as db:
            db.get(Notification, nid).email_error = str(e)[:300]
        raise
    with session_scope() as db:
        n = db.get(Notification, nid)
        n.emailed_at, n.email_error = utcnow(), None


# ------------------------------------------------------------------ mock helpers (non-production only)

def deliver_mock_webhook(event: dict) -> dict:
    raw = json.dumps(event, separators=(",", ":")).encode()
    return receive_webhook("mock", raw, {"x-mock-signature": mock_sign(raw)})


def mock_complete_checkout(db: Session, ref: str, outcome: str, user: User) -> dict:
    att = db.scalar(select(PaymentAttempt).where(PaymentAttempt.provider_ref == ref))
    if not att or att.provider != "mock":
        raise ApiError("not_found", 404, entity="checkout")
    _check_attempt_owner(db, att, user)
    if outcome == "fail":
        ev = MockPaymentProvider.event("checkout.failed", {"attempt_ref": ref, "reason": "card_declined_test"},
                                       key=f"fail:{ref}")
        deliver_mock_webhook(ev)
        return {"status": "failed"}
    pay_ref = "mock_pi_" + ref[-20:]
    events = [MockPaymentProvider.event("checkout.succeeded", {"attempt_ref": ref, "payment_ref": pay_ref},
                                        key=f"ok:{ref}")]
    if att.context == "digital":
        order = db.get(DigitalOrder, att.order_id)
        if order.kind == "subscription":
            events.append(mock_invoice_event(db, order.subscription_id, first=True))
    for ev in events:
        deliver_mock_webhook(ev)
    return {"status": "succeeded"}


def _check_attempt_owner(db: Session, att: PaymentAttempt, user: User) -> None:
    if att.context == "digital":
        owner = db.get(DigitalOrder, att.order_id).buyer_id
    else:
        from ..models import HardwareOrder
        owner = db.get(HardwareOrder, att.order_id).buyer_id
    if owner != user.id:
        raise ApiError("not_found", 404, entity="checkout")


def mock_invoice_event(db: Session, sub_id: uuid.UUID, first: bool = False, fail: bool = False) -> dict:
    sub = db.get(Subscription, sub_id)
    price = db.get(Price, sub.price_id)
    start = sub.paid_until if (sub.paid_until and not first) else utcnow()
    end = start + (relativedelta(months=1) if price.interval == "month" else relativedelta(years=1))
    n = len(sub.periods) + 1
    inv = f"mock_in_{str(sub.id)[:8]}_{n}"
    if fail:
        return MockPaymentProvider.event("invoice.failed", {"subscription_id": str(sub.id), "invoice_ref": inv + "_f",
                                                            "provider_subscription_ref": f"mock_sub_{sub.id}"},
                                         key=f"invfail:{inv}")
    return MockPaymentProvider.event("invoice.paid", {
        "subscription_id": str(sub.id), "provider_subscription_ref": f"mock_sub_{sub.id}", "invoice_ref": inv,
        "payment_ref": f"mock_pi_{inv}", "period_start": start.isoformat(), "period_end": end.isoformat(),
        "amount_minor": price.amount_minor, "currency": price.currency}, key=f"inv:{inv}")


def reconcile_attempts(older_than_minutes: int = 15) -> int:
    """Ask the provider about stale 'created' attempts; apply confirmed results through the same handlers."""
    n = 0
    with session_scope() as db:
        rows = db.scalars(select(PaymentAttempt).where(
            PaymentAttempt.status == "created",
            PaymentAttempt.created_at < utcnow() - timedelta(minutes=older_than_minutes))).all()
        work = [(a.provider, a.provider_ref) for a in rows]
    for prov, ref in work:
        try:
            st = provider_by_name(prov).retrieve(ref)
        except Exception:
            continue
        if st.status == "succeeded":
            with session_scope() as db:
                apply_event(db, prov, NormalizedEvent("checkout.succeeded", {"attempt_ref": ref,
                                                                             "payment_ref": st.payment_ref}))
            n += 1
        elif st.status == "expired":
            with session_scope() as db:
                apply_event(db, prov, NormalizedEvent("checkout.failed", {"attempt_ref": ref, "reason": "expired"}))
    return n


def _fmt(amount: int, currency: str) -> str:
    return f"{amount / 100:.2f} {currency}"
