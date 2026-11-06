"""NAP-064: subscription tiers — several monthly plans, each with its own price and set of program sessions."""
import base64
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .conftest import Client, ok
from .helpers import convert, mock_pay, upload, wav_bytes


def _app_with_two_sessions(dev, mod):
    slug = "t-" + uuid.uuid4().hex[:10]
    a = ok(dev.post("/developer/apps", {"slug": slug, "category": "meditation", "author_name": "Test Author",
                                        "metadata": {"ru": {"title": "Тест"}, "en": {"title": "Test"}},
                                        "content_language": "ru"}), 201)
    job = convert(dev, a["id"], upload(dev, a["id"], wav_bytes(3.0))["ticket"]["asset_id"])
    v = a["version_id"]
    ok(dev.patch(f"/developer/apps/{a['id']}/versions/{v}", {"confirm_rights": True}))
    ok(dev.put(f"/developer/apps/{a['id']}/versions/{v}/program", {"sessions": [
        {"key": "s1", "titles": {"ru": "Первый", "en": "First"}, "conversion_job_id": job["id"]},
        {"key": "s2", "titles": {"ru": "Второй", "en": "Second"}, "conversion_job_id": job["id"]}]}))
    return a, v, slug


def _price(dev, app_id, **kw):
    body = {"kind": "subscription", "amount_minor": 500, "currency": "EUR", "interval": "month", **kw}
    return dev.put(f"/developer/apps/{app_id}/price", body)


def test_tiers_open_only_their_sessions():
    dev = Client().register(developer=True)
    mod = Client().login("moderator@nap.demo")
    a, v, slug = _app_with_two_sessions(dev, mod)
    aid = a["id"]
    for bad, field in (({"tier": "Basic!", "tier_names": {"ru": "Б"}}, "tier"),
                       ({"tier": "basic"}, "tier_names"),
                       ({"tier": "basic", "tier_names": {"ru": "Б"}, "sessions": ["nope"]}, "sessions"),
                       ({"tier": "basic", "tier_names": {"ru": "Б"}, "sessions": []}, "sessions")):
        r = _price(dev, aid, **bad)
        assert r.status_code == 422 and r.json()["error"]["details"]["field"] == field, (bad, r.text)
    r = dev.put(f"/developer/apps/{aid}/price", {"kind": "one_time", "amount_minor": 500, "currency": "EUR", "tier": "x"})
    assert r.status_code == 422 and r.json()["error"]["details"]["field"] == "tier"

    ok(_price(dev, aid, tier="basic", tier_names={"ru": "Базовый", "en": "Basic"}, sessions=["s1"]))
    ok(_price(dev, aid, amount_minor=900, tier="full", tier_names={"ru": "Полный", "en": "Full"}))
    ok(_price(dev, aid, amount_minor=600, currency="USD", tier="basic", tier_names={"ru": "Базовый", "en": "Basic"},
              sessions=["s1"]))
    # the second basic price did not replace "full"; a tier is the same set in every currency
    mine = ok(dev.get(f"/developer/apps/{aid}"))["prices"]
    assert sorted((p["tier"], p["currency"]) for p in mine) == [("basic", "EUR"), ("basic", "USD"), ("full", "EUR")]
    ok(dev.post(f"/developer/apps/{aid}/versions/{v}/submit"))
    assert ok(mod.post(f"/moderation/versions/{v}/decision", {"decision": "approve", "comment": "ok"}))["state"] == "published"

    card = ok(Client().get(f"/catalog/apps/{slug}"))
    tiers = {p["tier"]: p for p in card["prices"]}
    assert tiers["basic"]["sessions"] == ["s1"] and tiers["full"]["sessions"] is None
    assert tiers["basic"]["tier_names"]["en"] == "Basic" and tiers["full"]["amount_minor"] == 900

    buyer = Client().register()
    assert buyer.post("/checkout/digital", {"app_id": aid, "kind": "subscription", "tier": "gold"}).json()["error"]["code"] == "not_for_sale"
    mock_pay(buyer, ok(buyer.post("/checkout/digital", {"app_id": aid, "kind": "subscription", "tier": "basic"}), 201)["checkout_url"])
    acc = ok(buyer.get(f"/entitlements/{aid}"))
    assert acc["has_access"] and acc["tier"] == "basic" and acc["sessions"] == ["s1"]
    assert ok(buyer.get("/subscriptions"))["items"][0]["access"]["tier"] == "basic"

    web = ok(buyer.post("/devices", {"adapter": "web_headphones"}), 201)
    ok(buyer.post("/sessions", {"app_id": aid, "session_key": "s1", "device_id": web["id"]}), 201)
    r = buyer.post("/sessions", {"app_id": aid, "session_key": "s2", "device_id": web["id"]})
    assert r.status_code == 403 and r.json()["error"]["code"] == "tier_required"

    # Secure Player: no offline lease for a session outside the tier
    spki = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    ld = ok(buyer.post("/me/license-devices", {"public_key": base64.b64encode(spki).decode(), "name": "L"}), 201)
    r = buyer.post("/me/leases", {"device_id": ld["id"], "app_id": aid, "session_key": "s2"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "tier_required"

    # the author stops selling "basic": it leaves the store, the subscriber keeps the tier they paid for
    assert ok(dev.delete(f"/developer/apps/{aid}/price/tiers/basic"))["removed"] == 2
    assert [p["tier"] for p in ok(Client().get(f"/catalog/apps/{slug}"))["prices"]] == ["full"]
    assert ok(buyer.get(f"/entitlements/{aid}"))["sessions"] == ["s1"]
    assert dev.delete(f"/developer/apps/{aid}/price/tiers/basic").status_code == 404

    # a buyer of the full tier hears everything
    full = Client().register()
    mock_pay(full, ok(full.post("/checkout/digital", {"app_id": aid, "kind": "subscription", "tier": "full"}), 201)["checkout_url"])
    assert ok(full.get(f"/entitlements/{aid}"))["sessions"] is None
    w2 = ok(full.post("/devices", {"adapter": "web_headphones"}), 201)
    ok(full.post("/sessions", {"app_id": aid, "session_key": "s2", "device_id": w2["id"]}), 201)
