"""NAP-010/012/020: Stripe for the US and world markets — tax at checkout, one customer per user, billing portal,
tax booked separately from the 30/70 revenue base. Offline: the Stripe client is replaced by a recorder."""
import uuid

import pytest
from sqlalchemy import select

from app.adapters.payments import NormalizedEvent, StripeProvider, TAX_CODES, _invoice_tax
from app.config import get_settings
from app.db import session_scope
from app.models import DigitalOrder, PaymentAttempt, RevenueShare
from app.services import billing

from .conftest import Client, ok
from .helpers import make_published_app


class _Rec:
    """Minimal stand-in for stripe.StripeClient().v1.<resource>.<method>(...) that records the calls."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return _Res(self, [name])


class _Obj(dict):
    __getattr__ = dict.get


class _Res:
    def __init__(self, rec, path):
        self.rec, self.path = rec, path

    def __getattr__(self, name):
        return _Res(self.rec, self.path + [name])

    def __call__(self, *args, params=None, options=None):
        self.rec.calls.append((".".join(self.path), args, params or {}, options or {}))
        kind = self.path[-2] if len(self.path) > 1 else ""
        if kind == "sessions" and "billing_portal" in self.path:
            return _Obj(url="https://billing.stripe.com/p/session/test_1")
        if kind == "sessions":
            return _Obj(id="cs_test_" + uuid.uuid4().hex[:8], url="https://checkout.stripe.com/c/pay/cs_test_x")
        if kind == "customers":
            return _Obj(id="cus_test_1")
        return _Obj(id="x_1")


@pytest.fixture
def stripe_rec(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "stripe_secret_key", "sk_test_offline")
    monkeypatch.setattr(s, "stripe_automatic_tax", True)
    monkeypatch.setattr(s, "stripe_tax_id_collection", True)
    p = StripeProvider()
    rec = _Rec()
    monkeypatch.setattr(p, "_client", lambda: rec)
    return p, rec


def _co(p, **kw):
    base = dict(context="digital", order_id="o1", amount_minor=999, currency="USD", description="App",
                mode="payment", interval=None, subscription_id=None, success_url="https://s", cancel_url="https://c",
                idempotency_key="k", customer_email="a@b.c")
    base.update(kw)
    return p.create_checkout(**base)


def test_digital_checkout_with_tax_and_customer(stripe_rec):
    p, rec = stripe_rec
    _co(p, customer_ref="cus_1")
    name, _, params, opts = rec.calls[-1]
    assert name == "v1.checkout.sessions.create" and opts["idempotency_key"] == "k"
    item = params["line_items"][0]["price_data"]
    assert item["currency"] == "usd" and item["unit_amount"] == 999 and item["tax_behavior"] == "exclusive"
    assert item["product_data"]["tax_code"] == TAX_CODES["digital"]
    assert params["automatic_tax"] == {"enabled": True}
    assert params["billing_address_collection"] == "required"  # digital tax follows the buyer's address
    assert params["customer"] == "cus_1" and "customer_email" not in params
    assert params["customer_update"] == {"address": "auto", "name": "auto"}
    assert params["tax_id_collection"] == {"enabled": True}


def test_hardware_checkout_lines_goods_and_shipping(stripe_rec):
    p, rec = stripe_rec
    _co(p, context="hardware", amount_minor=15000, customer_ref="cus_1",
        lines=[{"name": "Order", "amount_minor": 13000, "kind": "goods"},
               {"name": "Shipping", "amount_minor": 2000, "kind": "shipping"}])
    params = rec.calls[-1][2]
    codes = [li["price_data"]["product_data"]["tax_code"] for li in params["line_items"]]
    assert codes == [TAX_CODES["goods"], TAX_CODES["shipping"]]
    assert "billing_address_collection" not in params  # hardware tax uses the customer's shipping address
    with pytest.raises(ValueError):
        _co(p, amount_minor=100, lines=[{"name": "x", "amount_minor": 99, "kind": "goods"}])


def test_subscription_lines_are_recurring(stripe_rec):
    p, rec = stripe_rec
    _co(p, mode="subscription", interval="month", subscription_id="s1")
    params = rec.calls[-1][2]
    assert params["mode"] == "subscription"
    assert params["line_items"][0]["price_data"]["recurring"] == {"interval": "month"}
    assert params["subscription_data"]["metadata"]["nap_subscription_id"] == "s1"


def test_tax_off_keeps_the_simple_session(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "stripe_secret_key", "sk_test_offline")
    monkeypatch.setattr(s, "stripe_automatic_tax", False)
    monkeypatch.setattr(s, "stripe_tax_id_collection", False)
    p = StripeProvider()
    rec = _Rec()
    monkeypatch.setattr(p, "_client", lambda: rec)
    _co(p)
    params = rec.calls[-1][2]
    assert "automatic_tax" not in params and "tax_behavior" not in params["line_items"][0]["price_data"]
    assert params["customer_email"] == "a@b.c"


def test_customer_created_once_then_updated_with_shipping(stripe_rec):
    p, rec = stripe_rec
    ref = p.ensure_customer(user_id="u1", email="a@b.c", name="Ann", existing=None)
    assert ref == "cus_test_1" and rec.calls[-1][0] == "v1.customers.create"
    assert rec.calls[-1][3]["idempotency_key"] == "customer:u1"
    p.ensure_customer(user_id="u1", email="a@b.c", name="Ann", existing="cus_test_1",
                      shipping={"name": "Ann", "line1": "1 Main St", "city": "Austin", "state": "TX",
                                "postal_code": "78701", "country": "US"})
    name, args, params, _ = rec.calls[-1]
    assert name == "v1.customers.update" and args == ("cus_test_1",)
    assert params["shipping"]["address"]["state"] == "TX" and params["address"]["country"] == "US"


def test_portal_url(stripe_rec):
    p, rec = stripe_rec
    assert p.portal_url("cus_1", "https://store/en/account").startswith("https://billing.stripe.com")
    assert rec.calls[-1][2] == {"customer": "cus_1", "return_url": "https://store/en/account"}


def test_normalize_carries_tax_amounts():
    p = StripeProvider()
    ev = {"id": "e", "type": "checkout.session.completed", "data": {"object": {
        "id": "cs_1", "mode": "payment", "payment_status": "paid", "payment_intent": "pi_1", "currency": "usd",
        "amount_subtotal": 1000, "amount_total": 1083, "total_details": {"amount_tax": 83}, "customer": "cus_9"}}}
    d = p.normalize(ev)[0].data
    assert (d["amount_subtotal"], d["amount_tax"], d["amount_total"], d["currency"], d["customer_ref"]) == \
        (1000, 83, 1083, "USD", "cus_9")
    assert _invoice_tax({"tax": 50}) == 50
    assert _invoice_tax({"total_taxes": [{"amount": 30}, {"amount": 12}]}) == 42
    assert _invoice_tax({}) == 0


def _buy(amount=1000):
    dev = Client().register(developer=True)
    mod = Client().login("moderator@nap.demo")
    pub = make_published_app(dev, mod, amount=amount)
    buyer = Client().register()
    co = ok(buyer.post("/checkout/digital", {"app_id": pub["app_id"], "kind": "one_time"}), 201)
    return buyer, co


def test_tax_is_booked_apart_from_the_revenue_split():
    """US sales tax of 83 on 1000: cash 1083, tax payable 83, platform/developer split only the 1000."""
    _, co = _buy(1000)
    with session_scope() as db:
        att = db.scalar(select(PaymentAttempt).where(PaymentAttempt.order_id == uuid.UUID(co["order_id"])))
        billing.apply_event(db, "mock", NormalizedEvent("checkout.succeeded", {
            "attempt_ref": att.provider_ref, "payment_ref": "pi_x", "amount_subtotal": 1000, "amount_tax": 83,
            "amount_total": 1083}))
        db.commit()
    with session_scope() as db:
        o = db.get(DigitalOrder, uuid.UUID(co["order_id"]))
        assert o.status == "paid" and o.tax_minor == 83 and o.amount_minor == 1000
        share = db.scalar(select(RevenueShare).where(RevenueShare.order_id == o.id))
        assert (share.platform_minor, share.developer_minor) == (300, 700)
        from app.models import LedgerTransaction
        tx = db.scalar(select(LedgerTransaction).where(LedgerTransaction.business_ref == f"sale:{o.id}"))
        rows = {e.account: e.amount_minor for e in tx.entries}
        assert rows["cash:mock"] == 1083 and rows["tax:payable"] == -83
        assert rows["platform:revenue"] == -300 and sum(rows.values()) == 0


def test_subtotal_mismatch_is_audited_not_hidden():
    _, co = _buy(1000)
    with session_scope() as db:
        att = db.scalar(select(PaymentAttempt).where(PaymentAttempt.order_id == uuid.UUID(co["order_id"])))
        billing.apply_event(db, "mock", NormalizedEvent("checkout.succeeded", {
            "attempt_ref": att.provider_ref, "payment_ref": "pi_y", "amount_subtotal": 900, "amount_tax": 0}))
        db.commit()
        from app.models import AuditLog
        assert db.scalar(select(AuditLog).where(AuditLog.action == "payment.amount_mismatch",
                                                AuditLog.entity_id == str(att.id)))


def test_portal_requires_a_billing_account():
    u = Client().register()
    r = u.post("/billing/portal", {"return_path": "/account"})
    assert r.status_code in (404, 503)
