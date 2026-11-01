"""NAP-047: product photos — cleaned on upload, part of the card, moderated with it, gallery on the public page."""
import io

from .conftest import Client, ok
from .test_covers import _img
from .test_seller_products import _new_product


def _upload(c, path, data, name="shot.jpg", **params):
    q = "&".join([f"filename={name}"] + [f"{k}={v}" for k, v in params.items()])
    return c.raw().post(f"/api/v1{path}?{q}", content=data,
                        headers=c._h({"content-type": "application/octet-stream"}))


def _ready_product(seller):
    p = _new_product(seller)
    pid = p["id"]
    ok(seller.put(f"/seller/products/{pid}", {"destinations": ["DE", "RS"]}))
    ok(seller.post(f"/seller/products/{pid}/skus", {"price_minor": 9900, "currency": "EUR", "weight_g": 300,
                                                    "length_mm": 200, "width_mm": 150, "height_mm": 80,
                                                    "origin_country": "DE"}), 201)
    ok(seller.put(f"/seller/products/{pid}/texts/ru", {"title": "Фото-тест", "description": "Описание",
                                                       "in_box": ["Наушники"]}))
    return pid


def test_upload_cleans_images_and_enforces_rules():
    from PIL import Image
    seller = Client().login("seller2@nap.demo")
    pid = _ready_product(seller)
    # photos are recommended, not required
    p = ok(seller.get(f"/seller/products/{pid}"))
    assert next(c for c in p["checks"] if c["key"] == "photos") == {"key": "photos", "ok": False, "tab": "photos",
                                                                    "optional": True}
    r = _upload(seller, f"/seller/products/{pid}/photos", _img(2400, 1600, exif_gps=True))
    assert r.status_code == 201, r.text
    ph = r.json()["photos"]
    assert len(ph) == 1 and (ph[0]["width"], ph[0]["height"]) == (1600, 1067)  # proportions kept, ≤ 1600 px
    img = seller.raw().get(ph[0]["url"], headers=seller._h())
    assert img.status_code == 200 and img.headers["content-type"] == "image/webp"
    assert img.headers["cache-control"] == "private, no-store"  # draft: not public yet
    im = Image.open(io.BytesIO(img.content))
    assert im.format == "WEBP" and not im.getexif()  # EXIF with GPS and camera is gone
    # a draft photo is invisible to others
    assert Client().raw().get(ph[0]["url"]).status_code == 404
    assert Client().login("seller@nap.demo").raw().get(ph[0]["url"]).status_code == 404
    # rules
    assert _upload(seller, f"/seller/products/{pid}/photos", _img(500, 900)).json()["error"]["code"] == "photo_too_small"
    assert _upload(seller, f"/seller/products/{pid}/photos", _img(3000, 700)).json()["error"]["code"] == "photo_invalid"
    assert _upload(seller, f"/seller/products/{pid}/photos", b"not an image", "x.png").json()["error"]["code"] == "photo_invalid"
    assert _upload(seller, f"/seller/products/{pid}/photos", _img(800, 800), "x.gif").status_code == 415
    assert _upload(seller, f"/seller/products/{pid}/photos", b"", "x.png").status_code == 422
    # transparent PNG is flattened on white
    r = _upload(seller, f"/seller/products/{pid}/photos", _img(900, 900, "PNG", mode="RGBA"), "cut.png")
    assert r.status_code == 201
    # another supplier cannot touch the card
    assert _upload(Client().login("seller@nap.demo"), f"/seller/products/{pid}/photos", _img(900, 900)).status_code == 404


def test_order_delete_limit_and_moderation_to_public_gallery():
    seller = Client().login("seller2@nap.demo")
    mod = Client().login("moderator@nap.demo")
    pid = _ready_product(seller)
    for i in range(3):
        ok(_upload(seller, f"/seller/products/{pid}/photos", _img(1000 + i * 10, 800)), 201)
    p = ok(seller.get(f"/seller/products/{pid}"))
    ids = [x["id"] for x in p["photos"]]
    p = ok(seller.put(f"/seller/products/{pid}/photos", {"ids": [ids[2], ids[0], ids[1]]}))
    assert [x["id"] for x in p["photos"]] == [ids[2], ids[0], ids[1]]
    assert seller.put(f"/seller/products/{pid}/photos", {"ids": ids[:2]}).status_code == 422
    p = ok(seller.delete(f"/seller/products/{pid}/photos/{ids[1]}"))
    assert [x["id"] for x in p["photos"]] == [ids[2], ids[0]]
    # limit of 8
    for _ in range(6):
        ok(_upload(seller, f"/seller/products/{pid}/photos", _img(900, 900)), 201)
    assert _upload(seller, f"/seller/products/{pid}/photos", _img(900, 900)).json()["error"]["code"] == "photo_limit"
    for x in ok(seller.get(f"/seller/products/{pid}"))["photos"][2:]:
        ok(seller.delete(f"/seller/products/{pid}/photos/{x['id']}"))

    # submitted: locked for the supplier; the moderator sees the photos in the review
    p = ok(seller.post(f"/seller/products/{pid}/submit"))
    assert _upload(seller, f"/seller/products/{pid}/photos", _img(900, 900)).json()["error"]["code"] == "product_locked"
    m = ok(mod.get(f"/moderation/catalog/products/{pid}"))
    assert len(m["photos"]) == 2 and mod.raw().get(m["photos"][0]["url"], headers=mod._h()).status_code == 200
    ok(mod.post(f"/moderation/products/{pid}/decision", {"decision": "approve", "comment": ""}))

    # published: the gallery is public, cacheable, in the catalog card and on the product page
    slug = p["slug"]
    pub = ok(Client().get(f"/hardware/products/{slug}"))
    assert [x["id"] for x in pub["photos"]] == [ids[2], ids[0]]
    img = Client().raw().get(pub["photos"][0]["url"])
    assert img.status_code == 200 and "immutable" in img.headers["cache-control"]
    lst = ok(Client().get("/hardware/products?limit=100"))["items"]
    assert next(i for i in lst if i["slug"] == slug)["photos"]

    # the moderator removes a photo from the live card: a reason is needed, the supplier is told
    r = mod.raw().delete(f"/api/v1/moderation/catalog/products/{pid}/photos/{ids[0]}", headers=mod._h())
    assert r.status_code == 422
    r = mod.raw().delete(f"/api/v1/moderation/catalog/products/{pid}/photos/{ids[0]}?reason=Чужой логотип на фото",
                         headers=mod._h())
    assert r.status_code == 200 and len(r.json()["photos"]) == 1
    assert len(ok(Client().get(f"/hardware/products/{slug}"))["photos"]) == 1
    assert any(n["template"].startswith("product_") for n in ok(seller.get("/me/notifications"))["items"])
    # a removed photo is no longer served
    assert Client().raw().get(pub["photos"][1]["url"]).status_code == 404
