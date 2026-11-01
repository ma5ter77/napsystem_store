"""NAP-012: buyer currency. Apps may have one active price per kind *and currency* (e.g. EUR and USD);
the buyer sees and pays in their currency when the author set one, otherwise in the author's currency.

Buyer currency: cookie `nap_currency` (explicit choice) → interface language (en → USD, ru → EUR)."""
from __future__ import annotations

from contextvars import ContextVar

from ..config import get_settings

buyer_currency_var: ContextVar[str | None] = ContextVar("buyer_currency", default=None)
DEFAULT_BY_LOCALE = {"en": "USD", "ru": "EUR"}


def supported() -> list[str]:
    return [c.upper() for c in get_settings().currencies]


def resolve(cookie: str | None, locale: str) -> str:
    c = (cookie or "").upper()
    if c in supported():
        return c
    d = DEFAULT_BY_LOCALE.get(locale, "EUR")
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
