"""NAP-022: a supplier creates a product with variants and RU/EN texts, sends it to moderation; after approval it is
in the catalog without any administrator action."""
import uuid

from .conftest import Client, ok


def _new_product(seller, **extra):
    tag = uuid.uuid4().hex[:6]
    return ok(seller.post("/seller/products", {"category": "headphones", "brand": "Aurora", "model": f"HX-{tag}",
                                               **extra}), 201)


def test_supplier_product_lifecycle_to_catalog():
    seller = Client().login("seller2@nap.demo")
    other = Client().login("seller@nap.demo")
    mod = Client().login("moderator@nap.demo")
    anon = Client()

    p = _new_product(seller)
    pid = p["id"]
    assert p["status"] == "draft" and p["slug"].startswith("aurora-hx-") and p["editable"]
    assert not p["ready"] and {c["key"] for c in p["checks"] if not c["ok"]} >= {"sku", "texts", "destinations"}
    # nobody else sees or edits it; the catalog does not show drafts
    assert other.get(f"/seller/products/{pid}").status_code == 404
    assert anon.get(f"/hardware/products/{p['slug']}").status_code == 404
    # too early
    r = seller.post(f"/seller/products/{pid}/submit")
    assert r.status_code == 422 and r.json()["error"]["code"] == "product_incomplete"
    assert "sku" in r.json()["error"]["details"]["missing"]

    ok(seller.put(f"/seller/products/{pid}", {"destinations": ["de", "RS", "AM"], "warranty_months": 24,
                                              "specs": {"impedance_ohm": 32, "driver_mm": 40}}))
    r = seller.put(f"/seller/products/{pid}", {"warranty_months": 999})
    assert r.status_code == 422 and "warranty_months" in r.json()["error"]["details"]["fields"]
    p = ok(seller.post(f"/seller/products/{pid}/skus", {"price_minor": 12900, "currency": "EUR", "weight_g": 380,
                                                        "length_mm": 220, "width_mm": 190, "height_mm": 90,
                                                        "origin_country": "de", "variant": {"color": "black"}}), 201)
    sku = p["skus"][0]
    assert sku["code"].startswith(p["slug"].upper()) and sku["origin_country"] == "DE" and sku["on_hand"] == 0
    r = seller.post(f"/seller/products/{pid}/skus", {"code": sku["code"], "price_minor": 1})
    assert r.status_code == 409 and r.json()["error"]["code"] == "sku_taken"
    ok(seller.put(f"/seller/products/{pid}/texts/ru", {
        "title": "Наушники Aurora HX", "summary": "Закрытые, для бинауральных программ",
        "description": "Мягкие амбушюры, кабель 1,5 м.", "in_box": ["Наушники", "Кабель 3,5 мм", "Чехол"]}))
    p = ok(seller.put(f"/seller/products/{pid}/texts/en", {
        "title": "Aurora HX headphones", "description": "Soft ear pads, 1.5 m cable.",
        "in_box": ["Headphones", "3.5 mm cable", "Pouch"]}))
    assert p["ready"] and p["texts"]["ru"]["status"] == "draft"
    assert all(c["ok"] for c in p["checks"] if c["key"] != "photos")  # photos are recommended (NAP-047)

    p = ok(seller.post(f"/seller/products/{pid}/submit"))
    assert p["status"] == "submitted" and not p["editable"]
    r = seller.put(f"/seller/products/{pid}/texts/ru", {"title": "x"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "product_locked"
    queue = ok(mod.get("/moderation/products"))["items"]
    assert any(x["id"] == pid and x["languages"] == ["en", "ru"] for x in queue)
    assert seller.get("/moderation/products").status_code == 403
    m = ok(mod.get(f"/moderation/products/{pid}"))
    assert m["texts"]["en"]["title"] == "Aurora HX headphones" and m["seller"]

    # reject needs a comment, then the supplier fixes and resubmits
    r = mod.post(f"/moderation/products/{pid}/decision", {"decision": "reject", "comment": ""})
    assert r.status_code == 422 and r.json()["error"]["code"] == "comment_required"
    ok(mod.post(f"/moderation/products/{pid}/decision", {"decision": "reject", "comment": "Нужна страна происхождения кабеля"}))
    p = ok(seller.get(f"/seller/products/{pid}"))
    assert p["status"] == "rejected" and p["editable"] and p["history"][0]["comment"].startswith("Нужна")
    notes = ok(seller.get("/me/notifications"))["items"]
    assert any(n["template"] == "product_rejected" for n in notes)
    ok(seller.put(f"/seller/products/{pid}/skus/{sku['id']}", {"customs_description": "Headphones with cable"}))
    ok(seller.post(f"/seller/products/{pid}/submit"))
    assert ok(mod.post(f"/moderation/products/{pid}/decision", {"decision": "approve", "comment": ""}))["status"] == "published"

    # in the catalog, texts published, in both languages, without any administrator
    ru = ok(anon.get(f"/hardware/products/{p['slug']}"))
    assert ru["title"] == "Наушники Aurora HX" and ru["in_box"] == ["Наушники", "Кабель 3,5 мм", "Чехол"]
    en = ok(Client("en").get(f"/hardware/products/{p['slug']}"))
    assert en["title"] == "Aurora HX headphones" and not en["fallback"]
    assert any(x["slug"] == p["slug"] for x in ok(anon.get("/hardware/products?category=headphones"))["items"])
    assert any(n["template"] == "product_published" for n in ok(seller.get("/me/notifications"))["items"])

    # published: price and variant switch change right away, the card itself is locked
    p = ok(seller.put(f"/seller/products/{pid}/skus/{sku['id']}", {"price_minor": 11900}))
    assert p["skus"][0]["price_minor"] == 11900
    assert ok(anon.get(f"/hardware/products/{p['slug']}"))["skus"][0]["price_minor"] == 11900
    r = seller.put(f"/seller/products/{pid}/skus/{sku['id']}", {"weight_g": 1})
    assert r.status_code == 409 and r.json()["error"]["code"] == "product_locked"
    assert seller.put(f"/seller/products/{pid}", {"model": "X"}).status_code == 409
    # stock: the new SKU is in the supplier's inventory list
    inv = ok(seller.get("/seller/inventory"))["items"]
    assert any(i["code"] == sku["code"] for i in inv)

    # withdraw: hidden again, texts back to draft, editable
    p = ok(seller.post(f"/seller/products/{pid}/withdraw"))
    assert p["status"] == "draft" and p["texts"]["ru"]["status"] == "draft"
    assert anon.get(f"/hardware/products/{p['slug']}").status_code == 404
    listing = ok(seller.get("/seller/products"))
    assert any(x["id"] == pid and x["status"] == "draft" for x in listing["items"]) and listing["warehouses"]


def test_supplier_validation_and_access():
    seller = Client().login("seller2@nap.demo")
    r = seller.post("/seller/products", {"category": "nope", "brand": "A", "model": "B1"})
    assert r.status_code == 422
    p = _new_product(seller)
    r = seller.post("/seller/products", {"category": "headphones", "brand": "A", "model": "B", "slug": p["slug"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "slug_taken"
    r = seller.post(f"/seller/products/{p['id']}/skus", {"price_minor": -5, "weight_g": 0})
    assert r.status_code == 422 and {"price_minor", "weight_g"} <= set(r.json()["error"]["details"]["fields"])
    # an empty text form removes that language
    ok(seller.put(f"/seller/products/{p['id']}/texts/en", {"title": "T", "description": "D"}))
    assert "en" not in ok(seller.put(f"/seller/products/{p['id']}/texts/en", {}))["texts"]
    # buyers, developers and fulfillment cannot use the cabinet
    assert Client().login("user@nap.demo").get("/seller/products").status_code == 403
    assert Client().login("fulfillment@nap.demo").get("/seller/products").status_code == 403
    # a moderator cannot decide twice
    assert Client().login("moderator@nap.demo").post(
        f"/moderation/products/{p['id']}/decision", {"decision": "approve"}).status_code == 409
