"""NAP-012: prices in several currencies; buyers see and pay in theirs (cookie, else interface language)."""
import uuid

from sqlalchemy import select

from app.db import session_scope
from app.models import DigitalOrder, Price, RevenueShare

from .conftest import Client, ok
from .helpers import make_published_app, mock_pay


def _app_with_usd():
    dev = Client().register(developer=True)
    mod = Client().login("moderator@nap.demo")
    pub = make_published_app(dev, mod, amount=1000)  # EUR 10.00
    ok(dev.put(f"/developer/apps/{pub['app_id']}/price", {"kind": "one_time", "amount_minor": 1200, "currency": "USD"}))
    return dev, pub


def _card_price(c, slug):
    return ok(c.get(f"/catalog/apps/{slug}"))["prices"][0]


def test_usd_price_does_not_replace_eur_and_buyer_sees_own_currency():
    dev, pub = _app_with_usd()
    with session_scope() as db:
        active = {p.currency: p.amount_minor for p in db.scalars(select(Price).where(
            Price.app_id == uuid.UUID(pub["app_id"]), Price.active.is_(True)))}
    assert active == {"EUR": 1000, "USD": 1200}
    ru, en = Client("ru"), Client("en")
    assert _card_price(ru, pub["slug"])["currency"] == _card_price(en, pub["slug"])["currency"] == "EUR"  # UI language ≠ currency
    us = Client("en")
    us.headers["accept-language"] = "en-US,en;q=0.9"            # browser region US → USD
    assert _card_price(us, pub["slug"])["currency"] == "USD"
    us.headers["accept-language"] = "de-DE"
    assert _card_price(us, pub["slug"])["currency"] == "EUR"
    ru.c.cookies.set("nap_currency", "USD")                    # explicit choice wins
    p = _card_price(ru, pub["slug"])
    assert (p["currency"], p["amount_minor"]) == ("USD", 1200)
    assert ok(ru.get(f"/catalog/apps/{pub['slug']}"))["currencies"] == ["EUR", "USD"]
    ru.c.cookies.set("nap_currency", "XYZ")                    # unknown → language default
    assert _card_price(ru, pub["slug"])["currency"] == "EUR"


def test_missing_currency_falls_back_to_the_authors_price():
    dev = Client().register(developer=True)
    mod = Client().login("moderator@nap.demo")
    pub = make_published_app(dev, mod, amount=700)  # EUR only
    c = Client("en")
    c.c.cookies.set("nap_currency", "USD")
    assert _card_price(c, pub["slug"])["currency"] == "EUR"


def test_checkout_charges_in_the_buyers_currency():
    _, pub = _app_with_usd()
    buyer = Client("en").register()
    buyer.c.cookies.set("nap_currency", "USD")
    co = ok(buyer.post("/checkout/digital", {"app_id": pub["app_id"], "kind": "one_time"}), 201)
    mock_pay(buyer, co["checkout_url"])
    with session_scope() as db:
        o = db.get(DigitalOrder, uuid.UUID(co["order_id"]))
        assert (o.currency, o.amount_minor, o.status) == ("USD", 1200, "paid")
        s = db.scalar(select(RevenueShare).where(RevenueShare.order_id == o.id))
        assert (s.currency, s.platform_minor, s.developer_minor) == ("USD", 360, 840)


def test_subscription_interval_switch_applies_to_every_currency():
    dev = Client().register(developer=True)
    mod = Client().login("moderator@nap.demo")
    pub = make_published_app(dev, mod, kind="subscription", amount=500, interval="month")
    ok(dev.put(f"/developer/apps/{pub['app_id']}/price", {"kind": "subscription", "amount_minor": 600,
                                                           "currency": "USD", "interval": "month"}))
    ok(dev.put(f"/developer/apps/{pub['app_id']}/price", {"kind": "subscription", "amount_minor": 5000,
                                                           "currency": "EUR", "interval": "year"}))
    with session_scope() as db:
        active = [(p.currency, p.interval) for p in db.scalars(select(Price).where(
            Price.app_id == uuid.UUID(pub["app_id"]), Price.active.is_(True)))]
    assert active == [("EUR", "year")]


def test_unsupported_currency_rejected():
    dev, pub = _app_with_usd()
    r = dev.put(f"/developer/apps/{pub['app_id']}/price", {"kind": "one_time", "amount_minor": 1000, "currency": "JPY"})
    assert r.status_code == 422


def test_demo_apps_are_priced_in_usd_too():
    c = Client("ru")
    assert _card_price(c, "quiet-harbor")["currency"] == "EUR"
    c.c.cookies.set("nap_currency", "USD")
    assert _card_price(c, "quiet-harbor")["currency"] == "USD"
