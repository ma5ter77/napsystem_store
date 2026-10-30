"""Criteria 10, 11, 12, 23 (+ payouts / negative balances). Money is integer minor units."""
import json
import threading
import time
import uuid

import pytest
from sqlalchemy import func, select

from app.adapters.payments import MockPaymentProvider, StripeProvider, WebhookSignatureError, mock_sign
from app.config import get_settings
from app.db import session_scope
from app.models import DigitalOrder, Entitlement, LedgerEntry, PaymentAttempt, RevenueShare, Subscription
from app.services import billing, money

from .conftest import Client, ok
from .helpers import make_published_app, mock_pay


def test_split_examples_exact():
    assert money.split(10000, 3000) == (3000, 7000)
    assert money.split(6000, 3000) == (1800, 4200)
    assert money.split(1, 3000) == (0, 1)
    assert money.split(5, 3000) == (2, 3)  # 1.5 -> half-up 2
    # many small refunds never accumulate rounding drift: shares always equal the split of the remaining net
    gross, refunded = 9999, 0
    p_prev, d_prev = money.split(gross, 3000)
    total_p = total_d = 0
    while refunded < gross:
        r = min(7, gross - refunded)
        refunded += r
        p_new, d_new = money.split(gross - refunded, 3000)
        total_p += p_prev - p_new
        total_d += d_prev - d_new
        assert (p_prev - p_new) + (d_prev - d_new) == r
        p_prev, d_prev = p_new, d_new
    assert (total_p, total_d) == money.split(gross, 3000)


def _paid_order(amount=10000):
    dev = Client().register(developer=True)
    mod = Client().login("moderator@nap.demo")
    pub = make_published_app(dev, mod, amount=amount)
    buyer = Client().register()
    co = ok(buyer.post("/checkout/digital", {"app_id": pub["app_id"], "kind": "one_time"}), 201)
    return dev, buyer, pub, co


def _share(order_id):
    with session_scope() as db:
        s = db.scalar(select(RevenueShare).where(RevenueShare.order_id == uuid.UUID(order_id)))
        return (s.platform_minor, s.developer_minor) if s else None


def _refund(admin, order_id, amount):
    ok(admin.post("/admin/refunds/digital", {"order_id": order_id, "amount_minor": amount},
                  headers={"Idempotency-Key": uuid.uuid4().hex}), 201)


def test_30_70_and_refund_4000_example():
    dev, buyer, pub, co = _paid_order(10000)
    mock_pay(buyer, co["checkout_url"])
    assert _share(co["order_id"]) == (3000, 7000)
    admin = Client().login("admin@nap.demo")
    _refund(admin, co["order_id"], 4000)
    assert _share(co["order_id"]) == (1800, 4200)
    fin = ok(dev.get("/developer/finance"))["summary"][0]
    assert fin["accrued_minor"] == 4200 and fin["commission_minor"] == 1800 and fin["refunds_minor"] == 4000
    # partial refund keeps access (MVP)
    assert ok(buyer.get(f"/catalog/apps/{pub['slug']}"))["access"]["has_access"]
    # a refund cannot exceed the confirmed payment
    r = admin.post("/admin/refunds/digital", {"order_id": co["order_id"], "amount_minor": 6001},
                   headers={"Idempotency-Key": uuid.uuid4().hex})
    assert r.json()["error"]["code"] == "refund_exceeds_payment"
    # full refund of the remainder revokes the one-time right
    _refund(admin, co["order_id"], 6000)
    assert _share(co["order_id"]) == (0, 0)
    assert not ok(buyer.get(f"/catalog/apps/{pub['slug']}"))["access"]["has_access"]
    # journal is balanced and immutable
    with session_scope() as db:
        total = db.scalar(select(func.sum(LedgerEntry.amount_minor)))
        assert total == 0
    with pytest.raises(Exception):
        with session_scope() as db:
            e = db.scalar(select(LedgerEntry).limit(1))
            e.amount_minor = 1


def test_hold_ledger_cannot_be_unbalanced():
    with pytest.raises(ValueError):
        with session_scope() as db:
            money.post(db, "bad:" + uuid.uuid4().hex, "x", "EUR", [("a", 10), ("b", -9)])
    # even bypassing the python check the DB constraint trigger refuses at commit
    from sqlalchemy import text
    with pytest.raises(Exception):
        with session_scope() as db:
            tx = uuid.uuid4()
            db.execute(text("INSERT INTO ledger_transactions(id,business_ref,kind,currency,memo,created_at) "
                            "VALUES (:i,:r,'x','EUR','',now())"), {"i": tx, "r": "raw:" + tx.hex})
            db.execute(text("INSERT INTO ledger_entries(id,transaction_id,account,amount_minor,currency) "
                            "VALUES (gen_random_uuid(),:i,'a',5,'EUR')"), {"i": tx})


def test_webhook_duplicates_reorder_concurrency_and_bad_signature():
    dev, buyer, pub, co = _paid_order(2500)
    ref = co["checkout_url"].rsplit("/", 1)[-1]
    ev = MockPaymentProvider.event("checkout.succeeded", {"attempt_ref": ref, "payment_ref": "pi_x"}, key=f"dup:{ref}")
    raw = json.dumps(ev).encode()
    c = Client().raw()
    # invalid signature rejected, nothing stored
    r = c.post("/api/v1/webhooks/payments/mock", content=raw, headers={"x-mock-signature": "t=1,v1=deadbeef"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_signature"
    # body modified after signing
    r = c.post("/api/v1/webhooks/payments/mock", content=raw.replace(b"pi_x", b"pi_y"),
               headers={"x-mock-signature": mock_sign(raw)})
    assert r.status_code == 400
    # concurrent duplicate deliveries of the same event
    results = []

    def send():
        rr = Client().raw().post("/api/v1/webhooks/payments/mock", content=raw, headers={"x-mock-signature": mock_sign(raw)})
        results.append(rr.status_code)
    ts = [threading.Thread(target=send) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert all(x == 200 for x in results)
    # a second, *different* success event for the same attempt (provider retry with new id) is also harmless
    ev2 = MockPaymentProvider.event("checkout.succeeded", {"attempt_ref": ref, "payment_ref": "pi_x"}, key=f"dup2:{ref}")
    billing.deliver_mock_webhook(ev2)
    with session_scope() as db:
        oid = uuid.UUID(co["order_id"])
        assert db.scalar(select(func.count()).select_from(Entitlement).where(Entitlement.order_id == oid)) == 1
        assert db.scalar(select(func.count()).select_from(RevenueShare).where(RevenueShare.order_id == oid)) == 1
        assert db.scalar(select(func.count()).select_from(LedgerEntry).where(
            LedgerEntry.account == money.dev_payable(db.get(DigitalOrder, oid).developer_id))) == 1


def test_out_of_order_refund_before_payment_is_retried():
    dev, buyer, pub, co = _paid_order(3000)
    ref = co["checkout_url"].rsplit("/", 1)[-1]
    # refund event for an unknown refund arrives first: stored, not applied, retried later
    rid = str(uuid.uuid4())
    early = MockPaymentProvider.event("refund.succeeded", {"refund_id": rid, "refund_ref": "re_early",
                                                           "amount_minor": 100}, key=f"early:{rid}")
    billing.deliver_mock_webhook(early)
    from app.models import WebhookInbox
    with session_scope() as db:
        it = db.scalar(select(WebhookInbox).where(WebhookInbox.event_id == early["id"]))
        assert it.processed_at is None and "retry_later" in (it.last_error or "")
    mock_pay(buyer, co["checkout_url"])
    assert ok(buyer.get(f"/catalog/apps/{pub['slug']}"))["access"]["has_access"]


def _subscribe():
    dev = Client().register(developer=True)
    mod = Client().login("moderator@nap.demo")
    pub = make_published_app(dev, mod, kind="subscription", amount=999, interval="month")
    buyer = Client().register()
    co = ok(buyer.post("/checkout/digital", {"app_id": pub["app_id"], "kind": "subscription"}), 201)
    # creating the subscription is not payment
    assert not ok(buyer.get(f"/catalog/apps/{pub['slug']}"))["access"]["has_access"]
    mock_pay(buyer, co["checkout_url"])
    sub = ok(buyer.get("/subscriptions"))["items"][0]
    return dev, buyer, pub, sub


def test_subscription_renew_fail_cancel_and_period_refund():
    dev, buyer, pub, sub = _subscribe()
    assert sub["access"]["has_access"] and len(sub["periods"]) == 1
    first_until = sub["paid_until"]
    # renewal adds a paid interval
    ok(buyer.post(f"/test/subscriptions/{sub['id']}/renew", {"outcome": "success"}))
    sub = ok(buyer.get("/subscriptions"))["items"][0]
    assert len(sub["periods"]) == 2 and sub["paid_until"] > first_until
    # a failed renewal does not cancel the already paid period
    ok(buyer.post(f"/test/subscriptions/{sub['id']}/renew", {"outcome": "fail"}))
    sub = ok(buyer.get("/subscriptions"))["items"][0]
    assert sub["status"] == "past_due" and sub["access"]["has_access"]
    # cancel auto-renew keeps access until paid_until
    ok(buyer.post(f"/subscriptions/{sub['id']}/cancel"))
    sub = ok(buyer.get("/subscriptions"))["items"][0]
    assert sub["cancel_at_period_end"] and sub["access"]["has_access"]
    assert buyer.post(f"/test/subscriptions/{sub['id']}/renew", {}).status_code == 409
    # refund the *second* period only: the first one stays paid
    with session_scope() as db:
        s = db.get(Subscription, uuid.UUID(sub["id"]))
        second_order = str(s.periods[1].order_id)
        first_end = s.periods[0].period_end
    admin = Client().login("admin@nap.demo")
    _refund(admin, second_order, 999)
    sub = ok(buyer.get("/subscriptions"))["items"][0]
    assert [p["status"] for p in sub["periods"]] == ["paid", "refunded"]
    assert sub["paid_until"] == first_end.isoformat()
    assert sub["access"]["has_access"]  # current interval still paid
    # 30/70 on each paid period: 999 -> 300/699
    with session_scope() as db:
        shares = db.scalars(select(RevenueShare).join(DigitalOrder, DigitalOrder.id == RevenueShare.order_id)
                            .where(DigitalOrder.subscription_id == uuid.UUID(sub["id"]))).all()
        assert sorted((x.platform_minor, x.developer_minor) for x in shares) == [(0, 0), (300, 699)]


def test_payout_and_refund_after_payout_creates_debt():
    dev, buyer, pub, co = _paid_order(10000)
    mock_pay(buyer, co["checkout_url"])
    admin = Client().login("admin@nap.demo")
    po = ok(admin.post("/admin/payouts", {"developer_id": dev.user_id, "currency": "EUR"}), 201)
    assert po["status"] == "paid" and po["mock"] and po["amount_minor"] == 7000
    _refund(admin, co["order_id"], 10000)
    fin = ok(dev.get("/developer/finance"))["summary"][0]
    assert fin["paid_minor"] == 7000 and fin["balance_minor"] == -7000 and fin["negative"]
    assert admin.post("/admin/payouts", {"developer_id": dev.user_id, "currency": "EUR"}).status_code == 409


def test_hardware_sale_creates_no_developer_share():
    """Criterion 32 (money part): the demo multi-parcel hardware order posted no developer/platform-revenue lines."""
    with session_scope() as db:
        from app.models import LedgerTransaction
        hw = db.scalars(select(LedgerTransaction).where(LedgerTransaction.kind == "hardware_sale")).all()
        assert hw
        for tx in hw:
            accts = {e.account for e in tx.entries}
            assert not any(a.startswith("developer:") or a == "platform:revenue" for a in accts)


# ------------------------------------------------------------------ PaymentProvider contract


def _contract(provider, sign):
    ev_raw = json.dumps({"id": "evt_1", "type": "x", "data": {}}).encode()
    with pytest.raises(WebhookSignatureError):
        provider.verify_webhook(ev_raw, {})
    with pytest.raises(WebhookSignatureError):
        provider.verify_webhook(ev_raw, sign(b"other"))
    assert provider.event_id(provider.verify_webhook(ev_raw, sign(ev_raw))) == "evt_1"


def test_contract_mock_provider():
    p = MockPaymentProvider()
    _contract(p, lambda raw: {"x-mock-signature": mock_sign(raw)})
    cs1 = p.create_checkout(context="digital", order_id="o", amount_minor=1, currency="EUR", description="d",
                            mode="payment", interval=None, subscription_id=None, success_url="s", cancel_url="c",
                            idempotency_key="k1", customer_email=None)
    cs2 = p.create_checkout(context="digital", order_id="o", amount_minor=1, currency="EUR", description="d",
                            mode="payment", interval=None, subscription_id=None, success_url="s", cancel_url="c",
                            idempotency_key="k1", customer_email=None)
    assert cs1.provider_ref == cs2.provider_ref and cs1.test_mode


def test_contract_stripe_adapter_offline(monkeypatch):
    """Signature verification + normalization with Stripe-format payloads (no network)."""
    import hashlib
    import hmac
    secret = "whsec_test_contract"
    monkeypatch.setattr(get_settings(), "stripe_webhook_secret", secret)
    p = StripeProvider()

    def sign(raw):
        ts = int(time.time())
        sig = hmac.new(secret.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
        return {"stripe-signature": f"t={ts},v1={sig}"}
    _contract(p, sign)
    ev = {"id": "evt_2", "type": "checkout.session.completed",
          "data": {"object": {"id": "cs_test_1", "mode": "payment", "payment_status": "paid", "payment_intent": "pi_1"}}}
    assert p.normalize(ev)[0].type == "checkout.succeeded"
    inv = {"id": "evt_3", "type": "invoice.paid", "data": {"object": {
        "id": "in_1", "subscription": "sub_1", "amount_paid": 999, "currency": "eur", "payment_intent": "pi_2",
        "subscription_details": {"metadata": {"nap_subscription_id": "abc"}},
        "lines": {"data": [{"period": {"start": 1700000000, "end": 1702592000}}]}}}}
    n = p.normalize(inv)[0]
    assert n.type == "invoice.paid" and n.data["amount_minor"] == 999 and n.data["currency"] == "EUR"
    assert n.data["subscription_id"] == "abc" and n.data["period_end"] > n.data["period_start"]
    ref = {"id": "evt_4", "type": "refund.updated", "data": {"object": {"id": "re_1", "status": "succeeded",
                                                                       "amount": 100, "metadata": {"nap_refund_id": "r"}}}}
    assert p.normalize(ref)[0].type == "refund.succeeded"
    assert p.normalize({"id": "e", "type": "customer.created", "data": {"object": {}}}) == []


@pytest.mark.skipif(not get_settings().stripe_secret_key, reason="not run: NAP_STRIPE_SECRET_KEY not configured")
def test_stripe_live_sandbox_checkout():
    p = StripeProvider()
    cs = p.create_checkout(context="digital", order_id=str(uuid.uuid4()), amount_minor=500, currency="EUR",
                           description="NAP sandbox test", mode="payment", interval=None, subscription_id=None,
                           success_url="https://example.com/s", cancel_url="https://example.com/c",
                           idempotency_key=uuid.uuid4().hex, customer_email=None)
    assert cs.url.startswith("https://checkout.stripe.com")
