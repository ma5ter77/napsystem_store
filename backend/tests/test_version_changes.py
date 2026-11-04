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
