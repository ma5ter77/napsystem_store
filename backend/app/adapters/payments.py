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
                        cancel_url: str, idempotency_key: str, customer_email: str | None,
                        lines: list[dict] | None = None, customer_ref: str | None = None,
                        trial_days: int = 0) -> CheckoutSession: ...

    def retrieve(self, provider_ref: str) -> PaymentStatus: ...

    def cancel_subscription(self, provider_subscription_ref: str | None, subscription_id: str) -> None: ...

    def refund(self, *, payment_ref: str, amount_minor: int, currency: str, refund_id: str,
               idempotency_key: str) -> str: ...

    def payout(self, *, payout_id: str, developer_account: str | None, amount_minor: int, currency: str,
               idempotency_key: str) -> str: ...

    def ensure_customer(self, *, user_id: str, email: str, name: str | None, existing: str | None,
                        shipping: dict | None = None) -> str | None: ...

    def portal_url(self, customer_ref: str, return_url: str) -> str: ...

    def connect_onboarding(self, *, user_id: str, email: str, country: str | None, existing: str | None,
                           refresh_url: str, return_url: str) -> tuple[str, str]: ...

    def connect_status(self, account_ref: str) -> dict: ...

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
                        subscription_id, success_url, cancel_url, idempotency_key, customer_email, lines=None,
                        customer_ref=None, trial_days=0):
        ref = "mock_cs_" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:24]
        return CheckoutSession(ref, f"/mock-checkout/{ref}", True)

    def retrieve(self, provider_ref):
        return PaymentStatus("created")

    def ensure_customer(self, *, user_id, email, name, existing, shipping=None):
        return existing  # the mock keeps no customer records

    def portal_url(self, customer_ref, return_url):
        raise ProviderUnavailable("portal_not_available_in_mock")

    def connect_onboarding(self, *, user_id, email, country, existing, refresh_url, return_url):
        # test mode: the account is "connected" at once and the author comes straight back
        return existing or ("mock_acct_" + hashlib.sha256(user_id.encode()).hexdigest()[:16]), return_url

    def connect_status(self, account_ref):
        return {"details_submitted": True, "payouts_enabled": True, "requirements_due": [], "test_mode": True}

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

# Stripe product tax codes: electronically supplied services, general tangible goods, shipping
TAX_CODES = {"digital": "txcd_10000000", "goods": "txcd_99999999", "shipping": "txcd_92010001"}


def _invoice_tax(o: dict) -> int:
    """Tax on a paid invoice across API versions (legacy `tax`, newer `total_taxes[]`)."""
    if o.get("tax") is not None:
        return int(o["tax"])
    return sum(int(t.get("amount") or 0) for t in (o.get("total_taxes") or []))


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
        self.automatic_tax = s.stripe_automatic_tax
        self.tax_id_collection = s.stripe_tax_id_collection
        self.api_base = s.stripe_api_base if s.env != "production" else None

    def _client(self):
        if not self.key:
            raise ProviderUnavailable("stripe_not_configured")
        if self.api_base:  # stripe-mock in tests
            return self.stripe.StripeClient(self.key, base_addresses={"api": self.api_base})
        return self.stripe.StripeClient(self.key)

    def ensure_customer(self, *, user_id, email, name, existing, shipping=None):
        """One Stripe customer per user: subscriptions, saved cards, the billing portal and tax addresses live there."""
        params: dict[str, Any] = {"email": email, "metadata": {"nap_user_id": user_id}}
        if name:
            params["name"] = name[:250]
        if shipping:  # hardware: Stripe Tax uses the delivery address
            addr = {k: shipping.get(k) for k in ("line1", "line2", "city", "state", "postal_code", "country") if shipping.get(k)}
            params["shipping"] = {"name": (shipping.get("name") or name or email)[:250], "address": addr}
            params["address"] = addr
        c = self._client()
        if existing:
            c.v1.customers.update(existing, params=params)
            return existing
        return c.v1.customers.create(params=params, options={"idempotency_key": f"customer:{user_id}"}).id

    def portal_url(self, customer_ref, return_url):
        ps = self._client().v1.billing_portal.sessions.create(params={"customer": customer_ref, "return_url": return_url})
        return ps.url

    def connect_onboarding(self, *, user_id, email, country, existing, refresh_url, return_url):
        """NAP-011: Stripe Connect Express — Stripe collects identity, tax and bank details from the author."""
        c = self._client()
        acct = existing
        if not acct:
            params: dict[str, Any] = {"type": "express", "email": email, "metadata": {"nap_user_id": user_id},
                                      "capabilities": {"transfers": {"requested": True}}}
            if country:
                params["country"] = country.upper()
            acct = c.v1.accounts.create(params=params, options={"idempotency_key": f"connect:{user_id}"}).id
        link = c.v1.account_links.create(params={"account": acct, "refresh_url": refresh_url, "return_url": return_url,
                                                 "type": "account_onboarding"})
        return acct, link.url

    def connect_status(self, account_ref):
        a = self._client().v1.accounts.retrieve(account_ref)
        g = lambda o, k: (o.get(k) if isinstance(o, dict) else getattr(o, k, None))  # noqa: E731
        req = g(a, "requirements") or {}
        return {"details_submitted": bool(g(a, "details_submitted")), "payouts_enabled": bool(g(a, "payouts_enabled")),
                "requirements_due": list(g(req, "currently_due") or []), "test_mode": self.test_mode}

    def create_checkout(self, *, context, order_id, amount_minor, currency, description, mode, interval,
                        subscription_id, success_url, cancel_url, idempotency_key, customer_email, lines=None,
                        customer_ref=None, trial_days=0):
        # lines: [{name, amount_minor, kind: digital|goods|shipping}] — summing to amount_minor; default one line
        lines = lines or [{"name": description, "amount_minor": amount_minor,
                           "kind": "goods" if context == "hardware" else "digital"}]
        if sum(int(x["amount_minor"]) for x in lines) != amount_minor:
            raise ValueError("checkout lines do not add up to the amount")
        items = []
        for ln in lines:
            pd: dict[str, Any] = {"currency": currency.lower(), "unit_amount": int(ln["amount_minor"]),
                                  "product_data": {"name": str(ln["name"])[:250]}}
            if self.automatic_tax:
                pd["tax_behavior"] = "exclusive"  # prices are shown without tax; tax is added at checkout
                pd["product_data"]["tax_code"] = TAX_CODES[ln.get("kind", "digital")]
            if mode == "subscription":
                pd["recurring"] = {"interval": interval}
            items.append({"quantity": 1, "price_data": pd})
        params: dict[str, Any] = {
            "mode": "subscription" if mode == "subscription" else "payment",
            "success_url": success_url, "cancel_url": cancel_url,
            "client_reference_id": order_id,
            "metadata": {"nap_context": context, "nap_order_id": order_id},
            "line_items": items,
        }
        if customer_ref:
            params["customer"] = customer_ref
        elif customer_email:
            params["customer_email"] = customer_email
        if self.automatic_tax:
            params["automatic_tax"] = {"enabled": True}
            if context != "hardware":  # digital tax follows the buyer's billing address
                params["billing_address_collection"] = "required"
            if customer_ref:
                params["customer_update"] = {"address": "auto", "name": "auto"} if context != "hardware" else {"name": "auto"}
        if self.tax_id_collection:
            params["tax_id_collection"] = {"enabled": True}
            if customer_ref and "customer_update" not in params:
                params["customer_update"] = {"name": "auto"}
        if mode == "subscription":
            params["subscription_data"] = {"metadata": {"nap_subscription_id": subscription_id or ""}}
            if trial_days:  # NAP-064: Stripe invoices the trial at 0 and charges the price from its end
                params["subscription_data"]["trial_period_days"] = int(trial_days)
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
            amounts = {"amount_subtotal": o.get("amount_subtotal"), "amount_total": o.get("amount_total"),
                       "amount_tax": (o.get("total_details") or {}).get("amount_tax") or 0,
                       "currency": (o.get("currency") or "").upper() or None, "customer_ref": o.get("customer")}
            if o.get("mode") == "subscription":
                return [NormalizedEvent("checkout.succeeded", {"attempt_ref": o["id"], "payment_ref": o.get("invoice"),
                                                               **amounts})]
            if o.get("payment_status") == "paid":
                return [NormalizedEvent("checkout.succeeded", {"attempt_ref": o["id"],
                                                               "payment_ref": o.get("payment_intent"), **amounts})]
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
                    "amount_minor": o.get("amount_paid"), "tax_minor": _invoice_tax(o),
                    "currency": (o.get("currency") or "").upper()})]
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


def provider_for_currency(currency: str) -> PaymentProvider:
    """NAP-012: roubles go to YooKassa (buyers in Russia), every other currency to the main provider (Stripe)."""
    s = get_settings()
    if currency.upper() == "RUB":
        if s.yookassa_shop_id and s.yookassa_secret_key:
            from .yookassa import YooKassaProvider
            return YooKassaProvider()
        if s.env != "production":
            return get_payment_provider() if get_payment_provider().name == "mock" else provider_by_name("mock")
        raise ProviderUnavailable("yookassa_not_configured")
    return get_payment_provider()


def provider_by_name(name: str) -> PaymentProvider:
    p = get_payment_provider()
    if p.name == name:
        return p
    if name == "yookassa":
        from .yookassa import YooKassaProvider
        return YooKassaProvider()
    if name == "mock" and get_settings().env != "production":
        from ..services.billing import deliver_mock_webhook
        return MockPaymentProvider(emit=deliver_mock_webhook)
    if name == "stripe":
        return StripeProvider()
    raise ValueError(name)


def reset_provider() -> None:
    global _provider
    _provider = None
