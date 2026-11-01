"""NAP-012: buyer currency. Apps may have one active price per kind *and currency* (e.g. EUR and USD);
the buyer sees and pays in their currency when the author set one, otherwise in the author's currency.

Buyer currency: cookie `nap_currency` (explicit choice) → the browser's region (Accept-Language, e.g. en-US →
USD) → EUR. The interface language never changes prices: switching RU/EN keeps the same currency."""
from __future__ import annotations

from contextvars import ContextVar

from ..config import get_settings

buyer_currency_var: ContextVar[str | None] = ContextVar("buyer_currency", default=None)
USD_REGIONS = {"US", "PR", "EC", "SV", "PA", "TL", "FM", "MH", "PW", "ZW"}  # where the US dollar is the currency


def supported() -> list[str]:
    return [c.upper() for c in get_settings().currencies]


def resolve(cookie: str | None, accept_language: str | None) -> str:
    c = (cookie or "").upper()
    if c in supported():
        return c
    first = (accept_language or "").split(",")[0].split(";")[0].strip()
    region = first.split("-")[1].upper() if "-" in first else ""
    d = "USD" if region in USD_REGIONS else "EUR"
    return d if d in supported() else supported()[0]


def current() -> str:
    return buyer_currency_var.get() or supported()[0]


def choose(prices: list, currency: str | None = None) -> list:
    """One price per kind: the buyer's currency if the author set it, else the oldest active price."""
    currency = currency or current()
    by_kind: dict = {}
    for p in sorted(prices, key=lambda p: (p.created_at is None, p.created_at)):
        cur = by_kind.get(p.kind)
        if cur is None or (p.currency == currency and cur.currency != currency):
            by_kind[p.kind] = p
    order = {"free": 0, "one_time": 1, "subscription": 2}
    return sorted(by_kind.values(), key=lambda p: order.get(p.kind, 9))
