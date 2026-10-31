"""NAP-083: after the moderator approves, the app reaches the store without another click when the author keeps
"publish right after approval" (default) and a price is set; otherwise the reason is visible to both sides."""
import uuid

from .conftest import Client, ok
from .helpers import convert, upload, wav_bytes


def _submitted_app(dev: Client, price: bool):
    slug = "ar-" + uuid.uuid4().hex[:8]
    a = ok(dev.post("/developer/apps", {"slug": slug, "category": "meditation", "author_name": "A",
                                        "metadata": {"ru": {"title": "Океан " + slug}}, "content_language": "ru"}), 201)
    up = upload(dev, a["id"], wav_bytes(2.0))
    job = convert(dev, a["id"], up["ticket"]["asset_id"])
    v = a["version_id"]
    ok(dev.patch(f"/developer/apps/{a['id']}/versions/{v}", {"confirm_rights": True}))
    ok(dev.put(f"/developer/apps/{a['id']}/versions/{v}/program",
               {"sessions": [{"key": "s1", "titles": {"ru": "Сеанс"}, "conversion_job_id": job["id"]}]}))
    if price:
        ok(dev.put(f"/developer/apps/{a['id']}/price", {"kind": "free"}))
    ok(dev.post(f"/developer/apps/{a['id']}/versions/{v}/submit"))
    return a, v, slug


def test_approval_without_price_waits_then_price_publishes():
    dev, mod, anon = Client().register(developer=True), Client().login("moderator@nap.demo"), Client()
    a, v, slug = _submitted_app(dev, price=False)
    q = ok(mod.get("/moderation/queue"))["items"]
    item = next(x for x in q if x["version"]["id"] == v)
    assert item["app"]["title"] == f"Океан {slug}" and item["app"]["price_set"] is False  # the queue names the app
    r = ok(mod.post(f"/moderation/versions/{v}/decision", {"decision": "approve", "comment": ""}))
    assert r["state"] == "approved" and r["release_blockers"] == ["price"]
    assert anon.get(f"/catalog/apps/{slug}").status_code == 404
    notes = ok(dev.get("/me/notifications"))["items"]
    assert any(n["template"] == "version_approved" and "Океан" in n["subject"] for n in notes)
    ok(dev.put(f"/developer/apps/{a['id']}/price", {"kind": "one_time", "amount_minor": 500, "currency": "EUR"}))
    assert ok(dev.get(f"/developer/apps/{a['id']}"))["versions"][0]["state"] == "published"
    assert ok(anon.get(f"/catalog/apps/{slug}"))["slug"] == slug


def test_approval_with_price_publishes_immediately():
    dev, mod, anon = Client().register(developer=True), Client().login("moderator@nap.demo"), Client()
    a, v, slug = _submitted_app(dev, price=True)
    r = ok(mod.post(f"/moderation/versions/{v}/decision", {"decision": "approve", "comment": "ok"}))
    assert r["state"] == "published"
    assert ok(anon.get(f"/catalog/apps/{slug}"))["slug"] == slug
    assert any(n["template"] == "version_published_auto" for n in ok(dev.get("/me/notifications"))["items"])


def test_manual_release_keeps_the_author_in_control():
    dev, mod = Client().register(developer=True), Client().login("moderator@nap.demo")
    a, v, slug = _submitted_app(dev, price=True)
    r = ok(dev.put(f"/developer/apps/{a['id']}/versions/{v}/auto-release", {"on": False}))
    assert r["auto_release"] is False
    r = ok(mod.post(f"/moderation/versions/{v}/decision", {"decision": "approve", "comment": ""}))
    assert r["state"] == "approved" and r["release_blockers"] == ["manual_release"]
    # turning it back on releases at once; the explicit "Publish" button keeps working as before
    r = ok(dev.put(f"/developer/apps/{a['id']}/versions/{v}/auto-release", {"on": True}))
    assert r["state"] == "published"
    assert dev.put(f"/developer/apps/{a['id']}/versions/{v}/auto-release", {"on": False}).status_code == 409
