#!/usr/bin/env python3
"""NAP-010: live Stripe test-mode run against a local API (GitHub Actions: .github/workflows/stripe-sandbox.yml).

Real Checkout pages are paid with Stripe test cards in a headless browser; webhooks arrive through
`stripe listen`. Checks: one-time purchase → access; subscription → access after invoice.paid; admin refund →
refund.succeeded; hardware order → paid; billing portal opens. Test mode only: the key must start with sk_test_.
Env: API (http://localhost:8000), STRIPE_SECRET_KEY, TAX=1 to expect tax lines (Stripe Tax registrations needed).
"""
import json
import os
import sys
import time
import uuid

import httpx
from playwright.sync_api import sync_playwright

API = os.environ.get("API", "http://localhost:8000") + "/api/v1"
KEY = os.environ["STRIPE_SECRET_KEY"]
assert KEY.startswith("sk_test_"), "test mode keys only"
US = {"name": "Alex Doe", "line1": "350 Fifth Avenue", "city": "New York", "region": "NY", "postal_code": "10118",
      "phone": "+1 212 555 0100"}
results = []


def step(name):
    def wrap(fn):
        t0 = time.time()
        try:
            fn()
            results.append({"step": name, "ok": True, "seconds": round(time.time() - t0, 1)})
            print(f"PASS {name}")
        except Exception as e:  # keep going: report every step
            results.append({"step": name, "ok": False, "error": str(e)[:400]})
            print(f"FAIL {name}: {e}")
        return fn
    return wrap


class C:
    def __init__(self):
        self.h = httpx.Client(timeout=60)
        self.csrf = None

    def _hd(self, extra=None):
        h = {"Accept-Language": "en"}
        if self.csrf:
            h["x-csrf-token"] = self.csrf
        return h | (extra or {})

    def req(self, m, url, body=None, **kw):
        r = self.h.request(m, API + url, json=body, headers=self._hd(kw.pop("headers", None)), **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"{m} {url} -> {r.status_code} {r.text[:300]}")
        d = r.json() if r.content else {}
        return d.get("data", d) if isinstance(d, dict) else d

    def register(self):
        d = self.req("POST", "/auth/register", {"email": f"stripe-{uuid.uuid4().hex[:8]}@example.com",
                     "password": "long-password-123", "display_name": "Stripe Sandbox", "accept_terms": True,
                     "consent_personal_data": True, "locale": "en"})
        self.csrf = d["csrf_token"]
        return self

    def login(self, email, password="demo-password-1"):
        d = self.req("POST", "/auth/login", {"email": email, "password": password})
        self.csrf = d.get("csrf_token")
        return self


def pay(url, card="4242424242424242"):
    """Fill Stripe Checkout (hosted page) with a test card and submit; waits for the redirect off Stripe."""
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto(url, wait_until="domcontentloaded")
        pg.wait_for_selector("#cardNumber", timeout=60000)
        email = pg.locator("#email")
        if email.count() and email.is_editable():
            email.fill("buyer@example.com")
        pg.fill("#cardNumber", card)
        pg.fill("#cardExpiry", "12 / 34")
        pg.fill("#cardCvc", "123")
        if pg.locator("#billingName").count():
            pg.fill("#billingName", "Alex Doe")
        if pg.locator("#billingCountry").count():
            pg.select_option("#billingCountry", "US")
        for sel, val in (("#billingAddressLine1", US["line1"]), ("#billingLocality", US["city"]),
                         ("#billingPostalCode", US["postal_code"])):
            if pg.locator(sel).count() and pg.locator(sel).is_visible():
                pg.fill(sel, val)
        if pg.locator("#billingAdministrativeArea").count() and pg.locator("#billingAdministrativeArea").is_visible():
            pg.select_option("#billingAdministrativeArea", "NY")
        pg.click("button.SubmitButton, button[type=submit]")
        pg.wait_for_url(lambda u: "checkout.stripe.com" not in u, timeout=90000, wait_until="commit")
        b.close()


def until(fn, what, seconds=90):
    end = time.time() + seconds
    last = None
    while time.time() < end:
        try:
            v = fn()
            if v:
                return v
        except Exception as e:
            last = e
        time.sleep(2)
    raise RuntimeError(f"timeout waiting for {what} ({last})")


buyer = C().register()
state = {}


@step("one-time purchase: Stripe Checkout, webhook grants access")
def _():
    app = buyer.req("GET", "/catalog/apps/quiet-harbor")
    co = buyer.req("POST", "/checkout/digital", {"app_id": app["id"], "kind": "one_time"},
                   headers={"Idempotency-Key": uuid.uuid4().hex})
    assert co["checkout_url"].startswith("https://checkout.stripe.com"), co
    assert co["test_mode"] is True
    pay(co["checkout_url"])
    state["order"] = co["order_id"]
    until(lambda: buyer.req("GET", f"/orders/digital/{co['order_id']}")["status"] == "paid", "order paid")
    until(lambda: buyer.req("GET", f"/orders/digital/{co['order_id']}")["access"]["has_access"], "access")


@step("subscription: access after invoice.paid")
def _():
    app = buyer.req("GET", "/catalog/apps/focus-course")
    co = buyer.req("POST", "/checkout/digital", {"app_id": app["id"], "kind": "subscription"},
                   headers={"Idempotency-Key": uuid.uuid4().hex})
    pay(co["checkout_url"])
    until(lambda: buyer.req("GET", f"/orders/digital/{co['order_id']}")["access"]["has_access"], "subscription access")


@step("declined card leaves the order unpaid")
def _():
    other = C().register()
    app = other.req("GET", "/catalog/apps/quiet-harbor")
    co = other.req("POST", "/checkout/digital", {"app_id": app["id"], "kind": "one_time"},
                   headers={"Idempotency-Key": uuid.uuid4().hex})
    try:
        pay(co["checkout_url"], card="4000000000000002")
    except Exception:
        pass  # Checkout stays on the page with "card declined"
    time.sleep(5)
    assert other.req("GET", f"/orders/digital/{co['order_id']}")["status"] != "paid"


@step("admin refund: Stripe refund, refund.succeeded webhook")
def _():
    admin = C().login("admin@nap.demo")
    o = buyer.req("GET", f"/orders/digital/{state['order']}")
    admin.req("POST", "/admin/refunds/digital", {"order_id": state["order"], "amount_minor": o["amount_minor"],
              "reason": "sandbox"}, headers={"Idempotency-Key": uuid.uuid4().hex})
    until(lambda: buyer.req("GET", f"/orders/digital/{state['order']}")["status"] in ("refunded",), "refunded")


@step("hardware order: US address, Stripe payment, order paid")
def _():
    prod = buyer.req("GET", "/hardware/products/nap-cable-35")
    buyer.req("POST", "/hardware/cart/items", {"sku_id": prod["skus"][0]["id"], "quantity": 1})
    o = buyer.req("POST", "/hardware/checkout")["orders"][0]
    buyer.req("PUT", f"/hardware/orders/{o['id']}/address", {"country": "US", "fields": US})
    rates = buyer.req("POST", f"/hardware/orders/{o['id']}/rates")["items"]
    buyer.req("POST", f"/hardware/orders/{o['id']}/shipping", {"rate_id": rates[0]["id"]})
    p = buyer.req("POST", f"/hardware/orders/{o['id']}/pay")
    pay(p["checkout_url"])
    paid = until(lambda: (lambda x: x if x["payment_status"] == "paid" else None)(
        buyer.req("GET", f"/hardware/orders/{o['id']}")), "hardware paid")
    if os.environ.get("TAX") == "1":
        assert paid["tax_minor"] > 0, "expected US sales tax"


@step("billing portal opens for the buyer")
def _():
    r = buyer.req("POST", "/billing/portal", {"return_path": "/account"})
    assert r["url"].startswith("https://billing.stripe.com"), r


print(json.dumps(results, indent=2))
if os.environ.get("GITHUB_STEP_SUMMARY"):
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
        f.write("## Stripe sandbox\n\n| step | result |\n|---|---|\n")
        for r in results:
            f.write(f"| {r['step']} | {'✅' if r['ok'] else '❌ ' + r.get('error', '')[:200]} |\n")
sys.exit(0 if all(r["ok"] for r in results) else 1)
