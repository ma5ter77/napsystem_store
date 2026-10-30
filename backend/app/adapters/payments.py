"""PaymentProvider contract, a fully working MockPaymentProvider (test mode) and a Stripe reference adapter.

Normalized events (provider-independent):
  checkout.succeeded {attempt_ref, payment_ref}
  checkout.failed    {attempt_ref, reason}
  invoice.paid       {subscription_id, provider_subscription_ref, invoice_ref, payment_ref, period_start, period_end, amount_minor, currency}
  invoice.failed     {subscription_id, provider_subscription_ref, invoice_ref}
  subscription.canceled {subscription_id, provider_subscription_ref}
  refund.succeeded   {refund_id, refund_ref, amount_minor}
  refund.failed      {refund_id, reason}
  payout.paid / payout.failed {payout_id, payout_ref}
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from ..config import get_settings


class WebhookSignatureError(Exception):
    pass


class ProviderUnavailable(Exception):
    pass


@dataclass
class CheckoutSession:
    provider_ref: str
    url: str
    test_mode: bool


@dataclass
class NormalizedEvent:
    type: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class PaymentStatus:
    status: str  # created / succeeded / failed / expired
    payment_ref: str | None = None


class PaymentProvider(Protocol):
    name: str
    test_mode: bool

    def create_checkout(self, *, context: str, order_id: str, amount_minor: int, currency: str, description: str,
                        mode: str, interval: str | None, subscription_id: str | None, success_url: str,
                        cancel_url: str, idempotency_key: str, customer_email: str | None) -> CheckoutSession: ...

    def retrieve(self, provider_ref: str) -> PaymentStatus: ...

    def cancel_subscription(self, provider_subscription_ref: str | None, subscription_id: str) -> None: ...

    def refund(self, *, payment_ref: str, amount_minor: int, currency: str, refund_id: str,
               idempotency_key: str) -> str: ...

    def payout(self, *, payout_id: str, developer_account: str | None, amount_minor: int, currency: str,
               idempotency_key: str) -> str: ...

    def verify_webhook(self, raw_body: bytes, headers: dict[str, str]) -> dict: ...

    def event_id(self, event: dict) -> str: ...

    def normalize(self, event: dict) -> list[NormalizedEvent]: ...


# --------------------------------------------------------------------------- mock

def _mock_secret() -> bytes:
    return hmac.new(get_settings().secret_key.encode(), b"mock-payment-webhook", hashlib.sha256).digest()


def mock_sign(raw: bytes, ts: int | None = None) -> str:
    ts = ts or int(time.time())
    sig = hmac.new(_mock_secret(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


class MockPaymentProvider:
    """Deterministic in-process provider. Outbound effects are emitted as *signed webhooks* that go through
    the same verification/inbox path as a real provider. Never available in production."""
    name = "mock"
    test_mode = True

    def __init__(self, emit=None):
        if get_settings().env == "production":
            raise RuntimeError("MockPaymentProvider is not available in production")
        self.emit = emit  # callable(event_dict) -> delivers a signed webhook

    def create_checkout(self, *, context, order_id, amount_minor, currency, description, mode, interval,
                        subscription_id, success_url, cancel_url, idempotency_key, customer_email):
        ref = "mock_cs_" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:24]
        return CheckoutSession(ref, f"/mock-checkout/{ref}", True)

    def retrieve(self, provider_ref):
        return PaymentStatus("created")

    def cancel_subscription(self, provider_subscription_ref, subscription_id):
        return None  # auto-renew stops; access stays until paid_until

    def refund(self, *, payment_ref, amount_minor, currency, refund_id, idempotency_key):
        ref = "mock_re_" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:20]
        if self.emit:
            self.emit(self.event("refund.succeeded", {"refund_id": refund_id, "refund_ref": ref,
                                                      "amount_minor": amount_minor}, key=f"refund:{refund_id}"))
        return ref

    def payout(self, *, payout_id, developer_account, amount_minor, currency, idempotency_key):
        ref = "mock_po_" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:20]
        if self.emit:
            self.emit(self.event("payout.paid", {"payout_id": payout_id, "payout_ref": ref}, key=f"payout:{payout_id}"))
        return ref

    @staticmethod
    def event(etype: str, data: dict, key: str | None = None) -> dict:
        eid = "mock_evt_" + (hashlib.sha256(key.encode()).hexdigest()[:24] if key else uuid.uuid4().hex)
        return {"id": eid, "type": etype, "created": int(time.time()), "test_mode": True, "data": data}

    def verify_webhook(self, raw_body, headers):
        sig = headers.get("x-mock-signature", "")
        try:
            parts = dict(p.split("=", 1) for p in sig.split(","))
            ts = int(parts["t"])
            expected = hmac.new(_mock_secret(), f"{ts}.".encode() + raw_body, hashlib.sha256).hexdigest()
        except Exception:
            raise WebhookSignatureError("malformed signature")
        if not hmac.compare_digest(expected, parts.get("v1", "")):
            raise WebhookSignatureError("bad signature")
        if abs(time.time() - ts) > 3600 * 24 * 7:
            raise WebhookSignatureError("stale")
        return json.loads(raw_body)

    def event_id(self, event):
        return event["id"]

    def normalize(self, event):
        return [NormalizedEvent(event["type"], event.get("data", {}))]


# --------------------------------------------------------------------------- stripe

def _ts(x: int | None) -> str | None:
    return datetime.fromtimestamp(x, tz=timezone.utc).isoformat() if x else None


class StripeProvider:
    """Reference adapter for Stripe Checkout/Billing (test mode). Live sandbox runs need NAP_STRIPE_* keys."""
    name = "stripe"

    def __init__(self):
        import stripe
        s = get_settings()
        self.stripe = stripe
        self.key = s.stripe_secret_key
        self.webhook_secret = s.stripe_webhook_secret
        self.test_mode = bool(self.key and self.key.startswith("sk_test_")) or not self.key

    def _client(self):
        if not self.key:
            raise ProviderUnavailable("stripe_not_configured")
        return self.stripe.StripeClient(self.key)

    def create_checkout(self, *, context, order_id, amount_minor, currency, description, mode, interval,
                        subscription_id, success_url, cancel_url, idempotency_key, customer_email):
        price_data: dict[str, Any] = {"currency": currency.lower(), "unit_amount": amount_minor,
                                      "product_data": {"name": description[:250]}}
        params: dict[str, Any] = {
            "mode": "subscription" if mode == "subscription" else "payment",
            "success_url": success_url, "cancel_url": cancel_url,
            "client_reference_id": order_id,
            "metadata": {"nap_context": context, "nap_order_id": order_id},
            "line_items": [{"quantity": 1, "price_data": price_data}],
        }
        if customer_email:
            params["customer_email"] = customer_email
        if mode == "subscription":
            price_data["recurring"] = {"interval": interval}
            params["subscription_data"] = {"metadata": {"nap_subscription_id": subscription_id or ""}}
        else:
            params["payment_intent_data"] = {"metadata": {"nap_order_id": order_id, "nap_context": context}}
        cs = self._client().v1.checkout.sessions.create(params=params, options={"idempotency_key": idempotency_key})
        return CheckoutSession(cs.id, cs.url, self.test_mode)

    def retrieve(self, provider_ref):
        cs = self._client().v1.checkout.sessions.retrieve(provider_ref)
        if cs.payment_status == "paid" or cs.status == "complete" and cs.payment_status == "no_payment_required":
            return PaymentStatus("succeeded", cs.payment_intent or cs.invoice)
        if cs.status == "expired":
            return PaymentStatus("expired")
        return PaymentStatus("created")

    def cancel_subscription(self, provider_subscription_ref, subscription_id):
        if provider_subscription_ref:
            self._client().v1.subscriptions.update(provider_subscription_ref, params={"cancel_at_period_end": True})

    def refund(self, *, payment_ref, amount_minor, currency, refund_id, idempotency_key):
        params = {"amount": amount_minor, "metadata": {"nap_refund_id": refund_id}}
        params["payment_intent" if payment_ref.startswith("pi_") else "charge"] = payment_ref
        r = self._client().v1.refunds.create(params=params, options={"idempotency_key": idempotency_key})
        return r.id

    def payout(self, *, payout_id, developer_account, amount_minor, currency, idempotency_key):
        if not developer_account:
            raise ProviderUnavailable("no_connected_account")
        tr = self._client().v1.transfers.create(params={
            "amount": amount_minor, "currency": currency.lower(), "destination": developer_account,
            "metadata": {"nap_payout_id": payout_id}}, options={"idempotency_key": idempotency_key})
        return tr.id

    def verify_webhook(self, raw_body, headers):
        if not self.webhook_secret:
            raise WebhookSignatureError("webhook secret not configured")
        sig = headers.get("stripe-signature", "")
        try:
            self.stripe.WebhookSignature.verify_header(raw_body.decode(), sig, self.webhook_secret, tolerance=300)
        except Exception as e:
            raise WebhookSignatureError(str(e))
        return json.loads(raw_body)

    def event_id(self, event):
        return event["id"]

    def normalize(self, event):
        t = event.get("type")
        o = event.get("data", {}).get("object", {})
        md = o.get("metadata") or {}
        if t in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
            if o.get("mode") == "subscription":
                return [NormalizedEvent("checkout.succeeded", {"attempt_ref": o["id"], "payment_ref": o.get("invoice")})]
            if o.get("payment_status") == "paid":
                return [NormalizedEvent("checkout.succeeded", {"attempt_ref": o["id"],
                                                               "payment_ref": o.get("payment_intent")})]
            return []
        if t in ("checkout.session.async_payment_failed", "checkout.session.expired"):
            return [NormalizedEvent("checkout.failed", {"attempt_ref": o["id"], "reason": t})]
        if t in ("invoice.paid", "invoice.payment_failed"):
            sub_ref = o.get("subscription") or (o.get("parent") or {}).get("subscription_details", {}).get("subscription")
            sd = (o.get("subscription_details") or (o.get("parent") or {}).get("subscription_details") or {})
            nap_sub = (sd.get("metadata") or {}).get("nap_subscription_id")
            line = ((o.get("lines") or {}).get("data") or [{}])[0]
            period = line.get("period") or {}
            if t == "invoice.paid":
                return [NormalizedEvent("invoice.paid", {
                    "subscription_id": nap_sub, "provider_subscription_ref": sub_ref, "invoice_ref": o["id"],
                    "payment_ref": o.get("payment_intent") or o.get("charge") or o["id"],
                    "period_start": _ts(period.get("start")), "period_end": _ts(period.get("end")),
                    "amount_minor": o.get("amount_paid"), "currency": (o.get("currency") or "").upper()})]
            return [NormalizedEvent("invoice.failed", {"subscription_id": nap_sub,
                                                       "provider_subscription_ref": sub_ref, "invoice_ref": o["id"]})]
        if t == "customer.subscription.deleted":
            return [NormalizedEvent("subscription.canceled", {
                "subscription_id": md.get("nap_subscription_id"), "provider_subscription_ref": o.get("id")})]
        if t in ("refund.created", "refund.updated", "charge.refund.updated"):
            if o.get("status") == "succeeded":
                return [NormalizedEvent("refund.succeeded", {"refund_id": md.get("nap_refund_id"), "refund_ref": o["id"],
                                                             "amount_minor": o.get("amount")})]
            if o.get("status") == "failed":
                return [NormalizedEvent("refund.failed", {"refund_id": md.get("nap_refund_id"), "reason": "failed"})]
            return []
        return []


_provider: PaymentProvider | None = None


def get_payment_provider() -> PaymentProvider:
    global _provider
    if _provider is None:
        s = get_settings()
        if s.payment_provider == "stripe":
            _provider = StripeProvider()
        else:
            from ..services.billing import deliver_mock_webhook
            _provider = MockPaymentProvider(emit=deliver_mock_webhook)
    return _provider


def provider_by_name(name: str) -> PaymentProvider:
    p = get_payment_provider()
    if p.name == name:
        return p
    if name == "mock" and get_settings().env != "production":
        from ..services.billing import deliver_mock_webhook
        return MockPaymentProvider(emit=deliver_mock_webhook)
    if name == "stripe":
        return StripeProvider()
    raise ValueError(name)


def reset_provider() -> None:
    global _provider
    _provider = None
