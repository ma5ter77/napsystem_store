"""NAP-116: the moderator and the author see which version is in the store and what the new one changes."""
import uuid

from .conftest import Client, ok
from .helpers import profile_id, upload, wait_inline_jobs, wav_bytes


def _job(dev, app_id, asset_id, layout="headphones"):
    j = ok(dev.post("/developer/conversions", {"app_id": app_id, "source_asset_id": asset_id,
                                               "profile_id": profile_id(dev), "layout": layout}), 202)
    wait_inline_jobs()
    return ok(dev.get(f"/developer/conversions/{j['id']}"))


def test_update_lists_what_changes_against_the_store():
    dev = Client().register(developer=True)
    mod = Client().login("moderator@nap.demo")
    a = ok(dev.post("/developer/apps", {"slug": "chg-" + uuid.uuid4().hex[:8], "category": "meditation", "author_name": "Chg",
                                        "metadata": {"ru": {"title": "Покой", "subtitle": "Вечер"}}}), 201)
    main = upload(dev, a["id"], wav_bytes(3.0))["ticket"]["asset_id"]
    hp = _job(dev, a["id"], main)
    ok(dev.patch(f"/developer/apps/{a['id']}/versions/{a['version_id']}", {"confirm_rights": True}))
    ok(dev.put(f"/developer/apps/{a['id']}/price", {"kind": "one_time", "amount_minor": 500, "currency": "EUR"}))
    ok(dev.post(f"/developer/apps/{a['id']}/versions/{a['version_id']}/submit"))
    first = ok(mod.get(f"/moderation/versions/{a['version_id']}"))
    assert first["live"] is None and first["changes"] == [] and not first["is_live"]   # first publication
    ok(mod.post(f"/moderation/versions/{a['version_id']}/decision", {"decision": "approve", "comment": ""}))
    assert ok(mod.get(f"/moderation/versions/{a['version_id']}"))["is_live"]

    v2 = ok(dev.post(f"/developer/apps/{a['id']}/versions"), 201)
    assert ok(dev.get(f"/developer/apps/{a['id']}"))["changes_vs_live"] == []   # a copy of the store version
    ok(dev.patch(f"/developer/apps/{a['id']}/versions/{v2['id']}",
                 {"metadata": {"ru": {"title": "Покой и сон", "subtitle": "Вечер"}, "en": {"title": "Calm"}}, "confirm_rights": True}))
    c30 = _job(dev, a["id"], main, "acoustic_3_0")
    other = upload(dev, a["id"], wav_bytes(3.0, freq=330.0), name="new.wav")["ticket"]["asset_id"]
    hp2 = _job(dev, a["id"], other)
    ok(dev.put(f"/developer/apps/{a['id']}/versions/{v2['id']}/program", {"sessions": [
        {"key": "s1", "titles": {"ru": "Сеанс"}, "conversion_job_id": hp["id"], "variants": {"acoustic_3_0": c30["id"]}},
        {"key": "s2", "titles": {"ru": "Второй"}, "conversion_job_id": hp2["id"]}]}))
    mine = ok(dev.get(f"/developer/apps/{a['id']}"))["changes_vs_live"]
    ok(dev.post(f"/developer/apps/{a['id']}/versions/{v2['id']}/submit"))
    r = ok(mod.get(f"/moderation/versions/{v2['id']}"))
    assert r["live"]["id"] == a["version_id"] and r["live"]["version_label"] == "1.0" and not r["is_live"]
    got = {(c["area"], c.get("key"), c.get("locale"), c["kind"]) for c in r["changes"]}
    assert ("text", "title", "ru", "changed") in got and ("text", "title", "en", "added") in got
    assert ("text", "subtitle", "ru", "changed") not in got          # unchanged fields are not listed
    assert ("layout", "s1", None, "added") in got and ("session", "s2", None, "added") in got
    t = next(c for c in r["changes"] if c["area"] == "text" and c["locale"] == "ru")
    assert t["before"] == "Покой" and t["after"] == "Покой и сон"
    assert {(c["area"], c.get("key"), c["kind"]) for c in mine} == {(c["area"], c.get("key"), c["kind"]) for c in r["changes"]}
    # the store keeps v1.0 until the update is approved
    assert ok(Client().get(f"/catalog/apps/{r['app']['slug']}"))["title"] == "Покой"


def test_resubmitted_product_lists_what_changes_against_the_approved_card():
    """NAP-116 part 2: a supplier takes a card on sale back to edit it; the moderator sees "before → after"."""
    from .test_seller_products import _new_product
    seller = Client().login("seller2@nap.demo")
    mod = Client().login("moderator@nap.demo")
    p = _new_product(seller)
    pid = p["id"]
    ok(seller.put(f"/seller/products/{pid}", {"destinations": ["de"], "warranty_months": 12, "specs": {"driver_mm": 40}}))
    p = ok(seller.post(f"/seller/products/{pid}/skus", {"price_minor": 12900, "currency": "EUR", "weight_g": 380,
                                                        "length_mm": 220, "width_mm": 190, "height_mm": 90,
                                                        "origin_country": "de"}), 201)
    sku = p["skus"][0]
    ok(seller.put(f"/seller/products/{pid}/texts/ru", {"title": "Наушники Aurora", "description": "Мягкие амбушюры.",
                                                       "in_box": ["Наушники", "Кабель"]}))
    ok(seller.post(f"/seller/products/{pid}/submit"))
    first = ok(mod.get(f"/moderation/products/{pid}"))
    assert first["changes"] == [] and first["approved_at"] is None          # a new card: nothing to compare with
    assert not next(x for x in ok(mod.get("/moderation/products"))["items"] if x["id"] == pid)["update"]
    ok(mod.post(f"/moderation/products/{pid}/decision", {"decision": "approve", "comment": ""}))
    assert ok(seller.get(f"/seller/products/{pid}"))["changes"] == []        # on sale = the approved card

    ok(seller.post(f"/seller/products/{pid}/withdraw"))
    ok(seller.put(f"/seller/products/{pid}/skus/{sku['id']}", {"price_minor": 11900}))
    ok(seller.put(f"/seller/products/{pid}/texts/ru", {"title": "Наушники Aurora HX", "description": "Мягкие амбушюры.",
                                                       "in_box": ["Наушники", "Кабель", "Чехол"]}))
    ok(seller.put(f"/seller/products/{pid}", {"warranty_months": 24, "specs": {"driver_mm": 40, "impedance_ohm": 32}}))
    mine = ok(seller.get(f"/seller/products/{pid}"))["changes"]
    ok(seller.post(f"/seller/products/{pid}/submit"))
    q = next(x for x in ok(mod.get("/moderation/products"))["items"] if x["id"] == pid)
    m = ok(mod.get(f"/moderation/products/{pid}"))
    got = {(c["area"], c.get("key"), c.get("locale"), c["kind"]) for c in m["changes"]}
    assert q["update"] and q["changes"] == len(m["changes"]) and m["approved_at"]
    assert ("ptext", "title", "ru", "changed") in got and ("ptext", "in_box", "ru", "changed") in got
    assert ("ptext", "description", "ru", "changed") not in got
    assert ("pfield", "warranty_months", None, "changed") in got and ("spec", "impedance_ohm", None, "added") in got
    price = next(c for c in m["changes"] if c["area"] == "sku_price")
    assert price["key"] == sku["code"] and price["before"]["price_minor"] == 12900 and price["after"]["price_minor"] == 11900
    assert len(mine) == len(m["changes"])
    ok(mod.post(f"/moderation/products/{pid}/decision", {"decision": "approve", "comment": ""}))
    assert ok(mod.get(f"/moderation/products/{pid}"))["changes"] == []
