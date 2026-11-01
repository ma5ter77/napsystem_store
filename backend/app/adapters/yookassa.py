"""NAP-012: ЮKassa (YooKassa) — payments of buyers in Russia, with online receipts under 54-FZ.

API v3 over HTTPS with Basic auth (shop id : secret key) and an `Idempotence-Key` on every write.
Notifications are not signed, so the adapter never trusts the body: it re-reads the payment/refund from the API
by its id and acts on that authoritative object (and optionally checks the sender IP against YooKassa's ranges).
Recurring subscriptions are not part of this adapter yet (stage 2: saved payment methods + scheduled charges).
"""
from __future__ import annotations

import ipaddress
import json
from decimal import Decimal
from typing import Any

import httpx

from ..config import get_settings
from .payments import CheckoutSession, NormalizedEvent, PaymentStatus, ProviderUnavailable, WebhookSignatureError

API = "https://api.yookassa.ru/v3"
# https://yookassa.ru/developers/using-api/webhooks — addresses notifications come from
YOOKASSA_NETS = [ipaddress.ip_network(n) for n in (
    "185.71.76.0/27", "185.71.77.0/27", "77.75.153.0/25", "77.75.156.11/32", "77.75.156.35/32",
    "77.75.154.128/25", "2a02:5180::/32")]
# 54-FZ: VAT code of the seller (1 = без НДС, 4 = 20 %, 11 = 22 % from 2026, …) and payment subject
SUBJECT = {"digital": "service", "goods": "commodity", "shipping": "service"}


def _value(minor: int) -> str:
    return str((Decimal(minor) / 100).quantize(Decimal("0.01")))


def _minor(value: str | None) -> int | None:
    return None if value is None else int((Decimal(value) * 100).to_integral_value())


class YooKassaProvider:
    name = "yookassa"

    def __init__(self, transport: httpx.BaseTransport | None = None):
        s = get_settings()
        self.shop_id = s.yookassa_shop_id
        self.secret = s.yookassa_secret_key
        self.vat_code = s.yookassa_vat_code
        self.check_ip = s.yookassa_check_ip
        self.test_mode = bool(self.secret and self.secret.startswith("test_")) or not self.secret
        self._transport = transport

    def _http(self) -> httpx.Client:
        if not (self.shop_id and self.secret):
            raise ProviderUnavailable("yookassa_not_configured")
        return httpx.Client(base_url=API, auth=(self.shop_id, self.secret), timeout=30, transport=self._transport)

    def _post(self, path: str, body: dict, key: str) -> dict:
        with self._http() as c:
            r = c.post(path, json=body, headers={"Idempotence-Key": key[:64]})
        if r.status_code >= 500:
            raise ProviderUnavailable(f"yookassa_{r.status_code}")
        if r.status_code >= 400:
            raise ValueError(f"yookassa rejected the request: {r.status_code} {r.text[:300]}")
        return r.json()

    def _get(self, path: str) -> dict:
        with self._http() as c:
            r = c.get(path)
        if r.status_code >= 400:
            raise ProviderUnavailable(f"yookassa_{r.status_code}")
        return r.json()

    # ------------------------------------------------------------------ receipts (54-FZ)
    def _receipt(self, email: str | None, lines: list[dict], currency: str) -> dict | None:
        if not email:
            return None
        return {"customer": {"email": email}, "items": [{
            "description": str(ln["name"])[:128], "quantity": "1.00",
            "amount": {"value": _value(int(ln["amount_minor"])), "currency": currency},
            "vat_code": self.vat_code, "payment_mode": "full_payment",
            "payment_subject": SUBJECT.get(ln.get("kind", "digital"), "service")} for ln in lines]}

    # ------------------------------------------------------------------ protocol
    def create_checkout(self, *, context, order_id, amount_minor, currency, description, mode, interval,
                        subscription_id, success_url, cancel_url, idempotency_key, customer_email, lines=None,
                        customer_ref=None):
        if mode == "subscription":
            raise ProviderUnavailable("yookassa_subscriptions_not_supported_yet")
        lines = lines or [{"name": description, "amount_minor": amount_minor,
                           "kind": "goods" if context == "hardware" else "digital"}]
        if sum(int(x["amount_minor"]) for x in lines) != amount_minor:
            raise ValueError("checkout lines do not add up to the amount")
        body: dict[str, Any] = {
            "amount": {"value": _value(amount_minor), "currency": currency.upper()},
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": success_url},
            "description": description[:128],
            "metadata": {"nap_context": context, "nap_order_id": order_id},
        }
        rc = self._receipt(customer_email, lines, currency.upper())
        if rc:
            body["receipt"] = rc
        p = self._post("/payments", body, idempotency_key)
        return CheckoutSession(p["id"], p["confirmation"]["confirmation_url"], self.test_mode)

    def retrieve(self, provider_ref):
        p = self._get(f"/payments/{provider_ref}")
        if p.get("status") == "succeeded" and p.get("paid"):
            return PaymentStatus("succeeded", p["id"])
        if p.get("status") == "canceled":
            return PaymentStatus("expired")
        return PaymentStatus("created")

    def ensure_customer(self, *, user_id, email, name, existing, shipping=None):
        return existing  # YooKassa has no customer objects; the receipt carries the e-mail

    def portal_url(self, customer_ref, return_url):
        raise ProviderUnavailable("portal_not_available")

    def connect_onboarding(self, **kw):
        raise ProviderUnavailable("payouts_not_available")

    def connect_status(self, account_ref):
        raise ProviderUnavailable("payouts_not_available")

    def cancel_subscription(self, provider_subscription_ref, subscription_id):
        return None

    def refund(self, *, payment_ref, amount_minor, currency, refund_id, idempotency_key):
        body: dict[str, Any] = {"payment_id": payment_ref,
                                "amount": {"value": _value(amount_minor), "currency": currency.upper()},
                                "metadata": {"nap_refund_id": refund_id}}
        r = self._post("/refunds", body, idempotency_key)
        return r["id"]

    def payout(self, *, payout_id, developer_account, amount_minor, currency, idempotency_key):
        raise ProviderUnavailable("payouts_not_available")

    def verify_webhook(self, raw_body, headers):
        """No signature exists: check the sender (optional) and replace the object with the API's own copy."""
        if self.check_ip:
            ip = (headers.get("x-real-ip") or headers.get("x-forwarded-for", "").split(",")[0]).strip()
            try:
                if not any(ipaddress.ip_address(ip) in n for n in YOOKASSA_NETS):
                    raise WebhookSignatureError("sender is not YooKassa")
            except ValueError:
                raise WebhookSignatureError("no sender address")
        try:
            ev = json.loads(raw_body)
            obj = ev["object"]
            kind = "refunds" if ev["event"].startswith("refund.") else "payments"
        except (ValueError, KeyError, AttributeError):
            raise WebhookSignatureError("malformed notification")
        try:
            fresh = self._get(f"/{kind}/{obj['id']}")
        except ProviderUnavailable as e:
            raise WebhookSignatureError(f"cannot confirm with the API: {e}")
        return {"event": ev["event"], "object": fresh}

    def event_id(self, event):
        o = event["object"]
        return f"{event['event']}:{o['id']}:{o.get('status')}"

    def normalize(self, event):
        t, o = event.get("event"), event.get("object") or {}
        md = o.get("metadata") or {}
        if t == "payment.succeeded" and o.get("status") == "succeeded" and o.get("paid"):
            amount = _minor((o.get("amount") or {}).get("value"))
            return [NormalizedEvent("checkout.succeeded", {
                "attempt_ref": o["id"], "payment_ref": o["id"], "amount_subtotal": amount, "amount_total": amount,
                "amount_tax": 0, "currency": (o.get("amount") or {}).get("currency")})]
        if t == "payment.canceled" and o.get("status") == "canceled":
            return [NormalizedEvent("checkout.failed", {"attempt_ref": o["id"],
                                                        "reason": (o.get("cancellation_details") or {}).get("reason", t)})]
        if t == "refund.succeeded" and o.get("status") == "succeeded":
            return [NormalizedEvent("refund.succeeded", {"refund_id": md.get("nap_refund_id"), "refund_ref": o["id"],
                                                         "amount_minor": _minor((o.get("amount") or {}).get("value"))})]
        return []
