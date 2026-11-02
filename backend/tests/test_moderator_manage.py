"""NAP-084: moderator manages products, apps and reviews; content editor manages site texts and categories."""
import uuid

from .conftest import Client, ok
from .helpers import make_published_app


def test_moderator_product_lifecycle():
    mod, seller, anon = Client().login("moderator@nap.demo"), Client().login("seller2@nap.demo"), Client()
    lst = ok(mod.get("/moderation/catalog/products"))
    assert lst["total"] > 0 and lst["sellers"]
    sid = next(s["id"] for s in lst["sellers"] if "Aurora" in s["name"])
    # create for a supplier, fill and publish directly
    tag = uuid.uuid4().hex[:6]
    p = ok(mod.post("/moderation/catalog/products", {"seller_id": sid, "category": "cables", "brand": "Mod",
                                                     "model": f"C-{tag}"}), 201)
    pid = p["id"]
    ok(mod.put(f"/moderation/catalog/products/{pid}", {"data": {"destinations": ["DE"]}}))
    ok(mod.post(f"/moderation/catalog/products/{pid}/skus", {"data": {
        "price_minor": 900, "weight_g": 50, "length_mm": 100, "width_mm": 80, "height_mm": 20, "origin_country": "DE"}}), 201)
    ok(mod.put(f"/moderation/catalog/products/{pid}/texts/ru", {"data": {
        "title": "Кабель модератора", "description": "Описание", "in_box": ["Кабель"]}}))
    p = ok(mod.post(f"/moderation/catalog/products/{pid}/publish", {}))
    assert p["status"] == "published"
    assert p["reason_required"] is False             # NAP-106: the moderator's own card needs no reason
    assert ok(anon.get(f"/hardware/products/{p['slug']}"))["title"] == "Кабель модератора"
    # the supplier sees it among own products
    assert any(x["id"] == pid for x in ok(seller.get("/seller/products"))["items"])

    # someone else's live card: edit needs a reason, goes live at once, owner notified
    demo = next(x for x in lst["items"] if x["status"] == "published" and x["seller_id"] == sid)
    assert ok(mod.get(f"/moderation/catalog/products/{demo['id']}"))["reason_required"] is True   # NAP-106
    r = mod.put(f"/moderation/catalog/products/{demo['id']}/texts/ru", {"data": {"title": "Новое", "description": "D"}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "reason_required"
    ok(mod.put(f"/moderation/catalog/products/{demo['id']}/texts/ru",
               {"data": {"title": "Новое название", "description": "D"}, "reason": "Опечатка в названии"}))
    assert ok(anon.get(f"/hardware/products/{demo['slug']}"))["title"] == "Новое название"
    notes = ok(seller.get("/me/notifications"))["items"]
    assert any(n["template"] == "product_moderator_action" and "Опечатка" in n["body"] for n in notes)
    v = ok(mod.get(f"/moderation/catalog/products/{demo['id']}"))
    assert v["audit"][0]["action"] == "product.mod_edited" and v["audit"][0]["reason"] == "Опечатка в названии"

    # take off sale, delete (soft), restore
    assert mod.post(f"/moderation/catalog/products/{pid}/unpublish", {}).status_code == 422  # reason
    ok(mod.post(f"/moderation/catalog/products/{pid}/unpublish", {"reason": "Нет сертификата"}))
    assert anon.get(f"/hardware/products/{p['slug']}").status_code == 404
    p = ok(mod.post(f"/moderation/catalog/products/{pid}/delete", {"reason": "Дубликат"}))
    assert p["status"] == "deleted" and p["restorable"] and not p["editable"]
    assert pid not in [x["id"] for x in ok(mod.get("/moderation/catalog/products"))["items"]]
    assert pid in [x["id"] for x in ok(mod.get("/moderation/catalog/products?status=deleted"))["items"]]
    assert seller.put(f"/seller/products/{pid}", {"model": "X"}).status_code == 409
    p = ok(mod.post(f"/moderation/catalog/products/{pid}/restore", {}))
    assert p["status"] == "draft"
    # access: suppliers and buyers cannot use moderator tools
    assert seller.get("/moderation/catalog/products").status_code == 403
    assert Client().login("user@nap.demo").post(f"/moderation/catalog/products/{pid}/delete",
                                                {"reason": "x"}).status_code == 403


def test_moderator_app_management():
    dev, mod, anon = Client().register(developer=True), Client().login("moderator@nap.demo"), Client()
    a = make_published_app(dev, mod)
    found = ok(mod.get(f"/moderation/catalog/apps?q={a['slug']}"))["items"]
    assert len(found) == 1 and found[0]["published"]
    r = mod.put(f"/moderation/catalog/apps/{a['app_id']}", {"data": {"texts": {"ru": {"title": "Новое"}}}})
    assert r.status_code == 422  # reason required
    v = ok(mod.put(f"/moderation/catalog/apps/{a['app_id']}",
                   {"data": {"texts": {"ru": {"title": "Исправленное название", "description": "Текст"}},
                             "category": "sleep"}, "reason": "Название не по правилам"}))
    assert v["texts"]["ru"]["title"] == "Исправленное название" and v["category"] == "sleep"
    card = ok(anon.get(f"/catalog/apps/{a['slug']}"))
    assert card["title"] == "Исправленное название"
    assert any(n["template"] == "app_moderator_action" for n in ok(dev.get("/me/notifications"))["items"])
    # stop and resume sales, delete and restore
    ok(mod.post(f"/moderation/catalog/apps/{a['app_id']}/stop-sales", {"reason": "Жалобы"}))
    assert a["slug"] not in [x["slug"] for x in ok(anon.get("/catalog/apps?limit=100"))["items"]]
    ok(mod.post(f"/moderation/catalog/apps/{a['app_id']}/resume-sales", {}))
    v = ok(mod.post(f"/moderation/catalog/apps/{a['app_id']}/delete", {"reason": "Нарушение прав"}))
    assert v["deleted_at"] and v["sales_status"] == "withdrawn"
    assert anon.get(f"/catalog/apps/{a['slug']}").status_code == 404
    v = ok(mod.post(f"/moderation/catalog/apps/{a['app_id']}/restore", {}))
    assert not v["deleted_at"] and v["sales_status"] == "active"
    assert ok(anon.get(f"/catalog/apps/{a['slug']}"))["slug"] == a["slug"]
    # prices are not the moderator's (decision 31.10.2026): no price endpoint here
    assert mod.put(f"/developer/apps/{a['app_id']}/price", {"kind": "free"}).status_code in (403, 404)


def test_moderator_review_edit_delete():
    mod = Client().login("moderator@nap.demo")
    lst = ok(mod.get("/moderation/reviews/all?kind=app&status=published&limit=5"))
    assert lst["total"] > 0
    r0 = lst["items"][0]
    rid, slug = r0["id"], r0["app"]["slug"]
    r = mod.post(f"/moderation/reviews/{rid}/decision", {"action": "edit", "body": "x"})
    assert r.status_code == 422  # reason
    ok(mod.post(f"/moderation/reviews/{rid}/decision", {"action": "edit", "body": "Текст без телефона",
                                                         "note": "Удалён номер телефона"}))
    pub = ok(Client().get(f"/catalog/apps/{slug}/reviews?limit=50"))["items"]
    mine = next(x for x in pub if x["id"] == rid)
    assert mine["body"] == "Текст без телефона" and mine["edited_by_moderator"]
    ok(mod.post(f"/moderation/reviews/{rid}/decision", {"action": "delete", "note": "Спам"}))
    assert rid not in [x["id"] for x in ok(Client().get(f"/catalog/apps/{slug}/reviews?limit=50"))["items"]]
    assert any(x["id"] == rid for x in ok(mod.get("/moderation/reviews/all?status=deleted"))["items"])
    ok(mod.post(f"/moderation/reviews/{rid}/decision", {"action": "publish", "note": "Восстановлен"}))
    assert any(x["id"] == rid for x in ok(Client().get(f"/catalog/apps/{slug}/reviews?limit=50"))["items"])


def test_content_editor_texts_and_categories():
    admin = Client().login("admin@nap.demo")
    mod = Client().login("moderator@nap.demo")
    assert mod.get("/content-editor/texts").status_code == 403  # superrights are a separate role
    ed = Client().register()
    ok(admin.post(f"/admin/users/{ed.user_id}/roles", {"role": "content_editor"}))
    ed = Client().login(ed.email, "long-password-123")
    key = "home.title"
    ok(ed.put(f"/content-editor/texts/{key}/ru", {"value": "Магазин NAP — новый заголовок"}))
    assert key not in ok(Client().get("/content/texts?locale=ru"))["texts"]  # draft is not public
    ok(ed.post(f"/content-editor/texts/{key}/ru/publish"))
    assert ok(Client().get("/content/texts?locale=ru"))["texts"][key] == "Магазин NAP — новый заголовок"
    ok(ed.put(f"/content-editor/texts/{key}/ru", {"value": "Второй вариант"}))
    ok(ed.post(f"/content-editor/texts/{key}/ru/publish"))
    hist = ok(ed.get(f"/content-editor/texts/{key}/ru/history"))["items"]
    assert [h["value"] for h in hist[:2]] == ["Второй вариант", "Магазин NAP — новый заголовок"]
    ok(ed.post(f"/content-editor/texts/{key}/ru/revert", {"version_id": hist[1]["id"]}))
    import app.services.site_texts as stx
    stx._invalidate()
    assert ok(Client().get("/content/texts?locale=ru"))["texts"][key] == "Магазин NAP — новый заголовок"
    ok(ed.post(f"/content-editor/texts/{key}/ru/reset"))
    stx._invalidate()
    assert key not in ok(Client().get("/content/texts?locale=ru"))["texts"]
    # e-mail templates are editable too and used by notifications
    tpl = ok(ed.get("/content-editor/mail-templates"))["items"]
    assert any(t["key"] == "notifications.version_approved.subject" for t in tpl)
    # categories: create, rename, delete when unused; refuse when used
    c = ok(ed.post("/content-editor/categories", {"kind": "app", "slug": f"c-{uuid.uuid4().hex[:6]}",
                                                   "names": {"ru": "Новая", "en": ""}}), 201)
    cats = ok(ed.get("/content-editor/categories?kind=app"))["items"]
    new = next(x for x in cats if x["id"] == c["id"])
    assert new["missing"] == ["en"]
    ok(ed.put(f"/content-editor/categories/{c['id']}", {"names": {"ru": "Новая", "en": "New"}}))
    assert ed.delete(f"/content-editor/categories/{c['id']}").status_code == 204
    used = next(x for x in cats if x["used"] > 0)
    r = ed.delete(f"/content-editor/categories/{used['id']}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "category_in_use"
