"""NAP-065: ratings and reviews, developer replies, moderation."""
import uuid

from .conftest import Client, ok
from .helpers import make_published_app, mock_pay


def _cmd(c, sid, cmd, ver, data=None):
    return ok(c.post(f"/sessions/{sid}/commands", {"command_id": uuid.uuid4().hex, "command": cmd,
                                                   "expected_version": ver, "data": data or {}}))


def _complete_session(buyer, app_id, key="s1"):
    d = ok(buyer.post("/devices", {"adapter": "simulated", "profile": "sim-chair-v1"}), 201)
    s = ok(buyer.post("/sessions", {"app_id": app_id, "session_key": key, "device_id": d["id"]}), 201)
    _cmd(buyer, s["id"], "precheck", 0)
    _cmd(buyer, s["id"], "confirm_ready", 1)
    _cmd(buyer, s["id"], "start", 2)
    assert _cmd(buyer, s["id"], "complete", 3, {"position_seconds": 2})["state"] == "completed"


def _buy(buyer, pub):
    co = ok(buyer.post("/checkout/digital", {"app_id": pub["app_id"], "kind": "one_time"}), 201)
    mock_pay(buyer, co["checkout_url"])


def test_eligibility_rating_summary_and_own_app():
    dev = Client().register(developer=True)
    mod = Client().login("moderator@nap.demo")
    pub = make_published_app(dev, mod)
    url = f"/catalog/apps/{pub['slug']}/review"
    guest = Client()
    assert ok(guest.get(f"/catalog/apps/{pub['slug']}/reviews"))["eligibility"]["reason"] == "login_required"
    assert dev.put(url, {"rating": 5}).json()["error"]["code"] == "review_own_app"
    b = Client().register()
    assert b.put(url, {"rating": 5}).json()["error"]["code"] == "review_no_access"
    _buy(b, pub)
    r = b.put(url, {"rating": 5})
    assert r.status_code == 403 and r.json()["error"]["code"] == "review_no_completed_session"
    _complete_session(b, pub["app_id"])
    mine = ok(b.put(url, {"rating": 4, "title": "Спокойно", "body": "Хорошая программа"}))
    assert mine["status"] == "published" and mine["version"] == "1.0"
    # editing keeps one review per user
    ok(b.put(url, {"rating": 5, "body": "Ещё лучше"}))
    lst = ok(guest.get(f"/catalog/apps/{pub['slug']}/reviews"))
    assert lst["total"] == 1 and lst["items"][0]["rating"] == 5 and lst["items"][0]["edited"] in (True, False)
    s = lst["summary"]
    assert s["count"] == 1 and s["average"] is None and not s["enough"]  # average shown from 5 ratings
    assert s["distribution"]["5"] == 1
    assert b.put(url, {"rating": 6}).status_code == 422
    assert b.put(url, {"rating": 3, "title": "Только заголовок"}).json()["error"]["details"]["field"] == "body"


def test_free_app_average_from_five_and_catalog_sort_filter():
    dev = Client().register(developer=True)
    mod = Client().login("moderator@nap.demo")
    pub = make_published_app(dev, mod, kind="free", amount=0)
    users = []
    for stars in (5, 4, 5, 3, 4):
        u = Client().register()
        ok(u.post("/checkout/digital", {"app_id": pub["app_id"], "kind": "free"}), 201)
        ok(u.put(f"/catalog/apps/{pub['slug']}/review", {"rating": stars}))  # free: no session needed
        users.append(u)
    card = ok(Client().get(f"/catalog/apps/{pub['slug']}"))
    assert card["rating"] == {"count": 5, "average": 4.2}
    assert card["rating_summary"]["current_version"]["average"] == 4.2
    top = ok(Client().get("/catalog/apps?sort=rating&limit=100"))["items"]
    avgs = [c["rating"]["average"] for c in top if c["rating"]["average"] is not None]
    assert avgs == sorted(avgs, reverse=True) and pub["slug"] in [c["slug"] for c in top[:len(avgs)]]
    assert pub["slug"] in [c["slug"] for c in ok(Client().get("/catalog/apps?min_rating=4&limit=100"))["items"]]
    assert pub["slug"] not in [c["slug"] for c in ok(Client().get("/catalog/apps?min_rating=4.5&limit=100"))["items"]]
    # helpful votes: not for own review, toggles
    rid = ok(Client().get(f"/catalog/apps/{pub['slug']}/reviews"))["items"][0]["id"]
    owner = next(u for u in users if ok(u.get(f"/catalog/apps/{pub['slug']}/reviews"))["mine"]["id"] == rid)
    assert owner.post(f"/reviews/{rid}/helpful").json()["error"]["code"] == "review_own_vote"
    other = next(u for u in users if u is not owner)
    assert ok(other.post(f"/reviews/{rid}/helpful")) == {"helpful_count": 1, "helpful_by_me": True}
    assert ok(other.post(f"/reviews/{rid}/helpful"))["helpful_count"] == 0
    # the user deletes own review; the developer cannot
    assert dev.delete(f"/catalog/apps/{pub['slug']}/review").status_code == 404
    assert users[0].delete(f"/catalog/apps/{pub['slug']}/review").status_code == 204
    assert ok(Client().get(f"/catalog/apps/{pub['slug']}"))["rating"]["average"] is None  # 4 left


def test_reply_moderation_reports_and_notifications():
    dev = Client().register(developer=True)
    mod = Client().login("moderator@nap.demo")
    pub = make_published_app(dev, mod, kind="free", amount=0)
    b = Client().register()
    ok(b.post("/checkout/digital", {"app_id": pub["app_id"], "kind": "free"}), 201)
    # links and contacts wait for a moderator
    r = ok(b.put(f"/catalog/apps/{pub['slug']}/review", {"rating": 2, "body": "пишите мне a@b.cd или на site.com"}))
    assert r["status"] == "pending" and "link" in r["moderation_note"] and "contacts" in r["moderation_note"]
    assert ok(Client().get(f"/catalog/apps/{pub['slug']}/reviews"))["total"] == 0
    q = ok(mod.get("/moderation/reviews"))["items"]
    rid = next(x["id"] for x in q if x["app"]["slug"] == pub["slug"])
    ok(mod.post(f"/moderation/reviews/{rid}/decision", {"action": "publish", "note": "контакты допустимы"}))
    assert ok(Client().get(f"/catalog/apps/{pub['slug']}/reviews"))["total"] == 1
    # developer sees it and replies; a stranger developer cannot
    lst = ok(dev.get(f"/developer/reviews?app_id={pub['app_id']}&filter=unanswered"))
    assert lst["total"] == 1 and lst["summary"]["count"] == 1
    other_dev = Client().register(developer=True)
    assert other_dev.put(f"/developer/reviews/{rid}/reply", {"body": "x"}).status_code == 404
    ok(dev.put(f"/developer/reviews/{rid}/reply", {"body": "Спасибо за отзыв, исправим."}))
    assert ok(dev.get(f"/developer/reviews?app_id={pub['app_id']}&filter=unanswered"))["total"] == 0
    shown = ok(Client().get(f"/catalog/apps/{pub['slug']}/reviews"))["items"][0]
    assert shown["reply"]["body"].startswith("Спасибо")
    notes = ok(b.get("/me/notifications"))["items"]
    assert any(n["template"] == "review_reply" for n in notes)
    # a reply with a medical promise waits for a moderator
    assert ok(dev.put(f"/developer/reviews/{rid}/reply", {"body": "Наша программа вылечит бессонницу"}))["status"] == "pending"
    assert ok(Client().get(f"/catalog/apps/{pub['slug']}/reviews"))["items"][0]["reply"] is None
    # reports put the review into the queue; hide resolves them; the developer has no hide endpoint
    c = Client().register()
    assert c.post(f"/reviews/{rid}/report", {"reason": "abuse"}).status_code == 202
    assert c.post(f"/reviews/{rid}/report", {"reason": "nonsense"}).status_code == 422
    item = next(x for x in ok(mod.get("/moderation/reviews"))["items"] if x["id"] == rid)
    assert item["reports"][0]["reason"] == "abuse" and item["reply"]["status"] == "pending"
    ok(mod.post(f"/moderation/reviews/{rid}/decision", {"action": "hide", "note": "оскорбление"}))
    ok(mod.post(f"/moderation/reviews/{rid}/decision", {"target": "reply", "action": "hide"}))
    assert ok(Client().get(f"/catalog/apps/{pub['slug']}/reviews"))["total"] == 0
    assert all(x["id"] != rid for x in ok(mod.get("/moderation/reviews"))["items"])
    assert b.get("/moderation/reviews").status_code == 403


def _deliver(buyer, code="NAP-CBL35"):
    from .test_hardware import buy
    o = buy(buyer, [(code, 1)])
    seller = Client().login("seller@nap.demo")
    ok(seller.post(f"/seller/orders/{o['id']}/acknowledge"))
    item = o["items"][0]
    o = ok(seller.post(f"/seller/orders/{o['id']}/parcels", {"items": [{"order_item_id": item["id"], "quantity": 1}],
                                                             "weight_g": 300, "dims_mm": [200, 150, 100]}), 201)
    p = o["parcels"][0]
    ok(seller.post(f"/seller/orders/{o['id']}/parcels/{p['id']}/manual-shipment",
                   {"carrier": "Local", "tracking_number": "T-" + uuid.uuid4().hex[:8]}))
    ok(seller.post(f"/seller/orders/{o['id']}/parcels/{p['id']}/handover"))
    return seller, o, p


def test_product_review_needs_delivered_purchase_seller_replies():
    """NAP-079: verified purchase only (delivered parcel), seller cannot rate own product, seller replies."""
    slug = "nap-cable-35"
    url = f"/hardware/products/{slug}/review"
    b = Client().register()
    assert ok(Client().get(f"/hardware/products/{slug}/reviews"))["eligibility"]["reason"] == "login_required"
    assert b.put(url, {"rating": 5}).json()["error"]["code"] == "review_no_delivered_purchase"
    seller, o, p = _deliver(b)
    # shipped but not delivered yet: still not allowed
    assert b.put(url, {"rating": 5}).json()["error"]["code"] == "review_no_delivered_purchase"
    ok(seller.post(f"/seller/orders/{o['id']}/parcels/{p['id']}/events", {"status": "delivered", "description": "POD"}))
    r = ok(b.put(url, {"rating": 4, "body": "Кабель как кабель, работает"}))
    assert r["verified_purchase"] and r["version"] == "NAP-CBL35" and r["status"] == "published"
    assert seller.put(url, {"rating": 5}).json()["error"]["code"] == "review_own_product"
    lst = ok(Client().get(f"/hardware/products/{slug}/reviews"))
    assert lst["total"] >= 1 and any(x["id"] == r["id"] for x in lst["items"])
    card = ok(Client().get(f"/hardware/products/{slug}"))
    assert card["rating"]["count"] >= 1 and card["rating_summary"]["count"] >= 1
    # the seller's cabinet lists it; reply notifies the buyer; another seller cannot reply
    mine = ok(seller.get("/seller/reviews?filter=unanswered&limit=100"))
    assert any(x["id"] == r["id"] for x in mine["items"])
    assert Client().login("seller2@nap.demo").put(f"/seller/reviews/{r['id']}/reply", {"body": "x"}).status_code == 404
    ok(seller.put(f"/seller/reviews/{r['id']}/reply", {"body": "Спасибо за покупку!"}))
    assert any(n["template"] == "review_reply" for n in ok(b.get("/me/notifications"))["items"])
    shown = next(x for x in ok(Client().get(f"/hardware/products/{slug}/reviews?limit=50"))["items"] if x["id"] == r["id"])
    assert shown["reply"]["body"] == "Спасибо за покупку!"
    # catalog: filter/sort parameters accepted, rating present on cards
    items = ok(Client().get("/hardware/products?kind=devices&sort=rating"))["items"]
    assert all("rating" in i for i in items)
    # moderation queue shows product reviews with their kind
    c = Client().register()
    ok(c.post(f"/reviews/{r['id']}/report", {"reason": "spam"}), 202)
    q = ok(Client().login("moderator@nap.demo").get("/moderation/reviews"))["items"]
    assert next(x for x in q if x["id"] == r["id"])["app"]["kind"] == "product"
