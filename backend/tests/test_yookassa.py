"""NAP-012: YooKassa adapter (buyers in Russia, 54-FZ receipts) — offline with httpx.MockTransport; RUB routing."""
import base64
import json
import uuid

import httpx
import pytest
from sqlalchemy import select

from app.adapters.payments import ProviderUnavailable, WebhookSignatureError, provider_for_currency
from app.adapters.yookassa import YooKassaProvider
from app.config import get_settings
from app.db import session_scope
from app.models import DigitalOrder

from .conftest import Client, ok
from .helpers import make_published_app, mock_pay


class Api:
    def __init__(self, payment_status="succeeded"):
        self.requests = []
        self.payment_status = payment_status

    def __call__(self, req: httpx.Request):
        self.requests.append(req)
        if req.method == "POST" and req.url.path == "/v3/payments":
            body = json.loads(req.content)
            return httpx.Response(200, json={"id": "2f0e-pay", "status": "pending", "paid": False, "amount": body["amount"],
                                             "confirmation": {"type": "redirect",
                                                              "confirmation_url": "https://yoomoney.ru/checkout/payments/v2/contract?orderId=2f0e"}})
        if req.method == "POST" and req.url.path == "/v3/refunds":
            return httpx.Response(200, json={"id": "rf-1", "status": "succeeded"})
        if req.method == "GET" and req.url.path.startswith("/v3/payments/"):
            return httpx.Response(200, json={"id": req.url.path.rsplit("/", 1)[1], "status": self.payment_status,
                                             "paid": self.payment_status == "succeeded",
                                             "amount": {"value": "499.00", "currency": "RUB"}, "metadata": {}})
        if req.method == "GET" and req.url.path.startswith("/v3/refunds/"):
            return httpx.Response(200, json={"id": "rf-1", "status": "succeeded", "amount": {"value": "100.00", "currency": "RUB"},
                                             "metadata": {"nap_refund_id": "r1"}})
        return httpx.Response(404)


@pytest.fixture
def yk(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "yookassa_shop_id", "123456")
    monkeypatch.setattr(s, "yookassa_secret_key", "test_secret")
    monkeypatch.setattr(s, "yookassa_vat_code", 1)
    api = Api()
    return YooKassaProvider(transport=httpx.MockTransport(api)), api


def _co(p, **kw):
    base = dict(context="digital", order_id="o1", amount_minor=49900, currency="RUB", description="Медитация",
                mode="payment", interval=None, subscription_id=None, success_url="https://s/ok", cancel_url="https://s/c",
                idempotency_key="digital:o1", customer_email="buyer@example.ru")
    base.update(kw)
    return p.create_checkout(**base)


def test_payment_with_receipt_auth_and_idempotence(yk):
    p, api = yk
    cs = _co(p)
    assert cs.provider_ref == "2f0e-pay" and cs.url.startswith("https://yoomoney.ru/") and cs.test_mode
    req = api.requests[-1]
    assert req.headers["Idempotence-Key"] == "digital:o1"
    assert req.headers["Authorization"] == "Basic " + base64.b64encode(b"123456:test_secret").decode()
    body = json.loads(req.content)
    assert body["amount"] == {"value": "499.00", "currency": "RUB"} and body["capture"] is True
    assert body["confirmation"] == {"type": "redirect", "return_url": "https://s/ok"}
    item = body["receipt"]["items"][0]
    assert body["receipt"]["customer"] == {"email": "buyer@example.ru"}
    assert item["amount"] == {"value": "499.00", "currency": "RUB"} and item["vat_code"] == 1
    assert item["payment_subject"] == "service" and item["payment_mode"] == "full_payment"


def test_hardware_receipt_lines(yk):
    p, api = yk
    _co(p, context="hardware", amount_minor=150000, lines=[{"name": "Кресло", "amount_minor": 140000, "kind": "goods"},
                                                           {"name": "Доставка", "amount_minor": 10000, "kind": "shipping"}])
    items = json.loads(api.requests[-1].content)["receipt"]["items"]
    assert [(i["payment_subject"], i["amount"]["value"]) for i in items] == [("commodity", "1400.00"), ("service", "100.00")]


def test_subscriptions_and_payouts_not_offered_yet(yk):
    p, _ = yk
    with pytest.raises(ProviderUnavailable):
        _co(p, mode="subscription", interval="month")
    with pytest.raises(ProviderUnavailable):
        p.payout(payout_id="x", developer_account=None, amount_minor=1, currency="RUB", idempotency_key="k")


def test_notification_is_confirmed_with_the_api_and_sender_checked(yk, monkeypatch):
    p, api = yk
    raw = json.dumps({"type": "notification", "event": "payment.succeeded",
                      "object": {"id": "2f0e-pay", "status": "succeeded", "paid": True}}).encode()
    with pytest.raises(WebhookSignatureError):
        p.verify_webhook(raw, {"x-forwarded-for": "203.0.113.9"})        # not YooKassa
    ev = p.verify_webhook(raw, {"x-forwarded-for": "185.71.76.5, 10.0.0.1"})
    assert api.requests[-1].method == "GET" and api.requests[-1].url.path == "/v3/payments/2f0e-pay"
    n = p.normalize(ev)
    assert n[0].type == "checkout.succeeded" and n[0].data["amount_total"] == 49900
    assert p.event_id(ev) == "payment.succeeded:2f0e-pay:succeeded"
    api.payment_status = "pending"                                         # forged "succeeded" body: API disagrees
    assert p.normalize(p.verify_webhook(raw, {"x-forwarded-for": "185.71.76.5"})) == []
    with pytest.raises(WebhookSignatureError):
        p.verify_webhook(b"not json", {"x-forwarded-for": "185.71.76.5"})


def test_refund_request_and_refund_notification(yk):
    p, api = yk
    assert p.refund(payment_ref="2f0e-pay", amount_minor=10000, currency="RUB", refund_id="r1",
                    idempotency_key="refund:r1") == "rf-1"
    body = json.loads(api.requests[-1].content)
    assert body == {"payment_id": "2f0e-pay", "amount": {"value": "100.00", "currency": "RUB"},
                    "metadata": {"nap_refund_id": "r1"}}
    raw = json.dumps({"event": "refund.succeeded", "object": {"id": "rf-1"}}).encode()
    n = p.normalize(p.verify_webhook(raw, {"x-forwarded-for": "77.75.153.10"}))
    assert n[0].type == "refund.succeeded" and n[0].data == {"refund_id": "r1", "refund_ref": "rf-1", "amount_minor": 10000}


def test_roubles_route_to_yookassa_when_configured(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "yookassa_shop_id", None)
    assert provider_for_currency("RUB").name == "mock"        # demo: test checkout
    monkeypatch.setattr(s, "yookassa_shop_id", "1")
    monkeypatch.setattr(s, "yookassa_secret_key", "test_x")
    assert provider_for_currency("RUB").name == "yookassa"
    assert provider_for_currency("EUR").name == "mock"


def test_rub_purchase_end_to_end_in_demo(monkeypatch):
    monkeypatch.setattr(get_settings(), "currencies", ["EUR", "USD", "RUB"])  # RUB is opt-in
    dev = Client().register(developer=True)
    mod = Client().login("moderator@nap.demo")
    pub = make_published_app(dev, mod, amount=1000)
    ok(dev.put(f"/developer/apps/{pub['app_id']}/price", {"kind": "one_time", "amount_minor": 49900, "currency": "RUB"}))
    buyer = Client().register()
    buyer.headers["accept-language"] = "ru-RU,ru;q=0.9"       # region RU → roubles
    assert ok(buyer.get(f"/catalog/apps/{pub['slug']}"))["prices"][0]["currency"] == "RUB"
    co = ok(buyer.post("/checkout/digital", {"app_id": pub["app_id"], "kind": "one_time"}), 201)
    mock_pay(buyer, co["checkout_url"])
    with session_scope() as db:
        o = db.get(DigitalOrder, uuid.UUID(co["order_id"]))
        assert (o.currency, o.amount_minor, o.status) == ("RUB", 49900, "paid")


def test_webhook_route_accepts_yookassa_only_from_its_networks(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "yookassa_shop_id", "1")
    monkeypatch.setattr(s, "yookassa_secret_key", "test_x")
    c = Client()
    r = c.c.post("/api/v1/webhooks/payments/yookassa", content=b'{"event":"payment.succeeded","object":{"id":"x"}}',
                 headers={"x-forwarded-for": "198.51.100.7", "content-type": "application/json"})
    assert r.status_code == 400
