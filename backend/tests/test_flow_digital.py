"""Criteria 8, 9, 13, 14, 15, 22(key isolation) — author flow with a real file, purchase, access control."""
import time

from app.db import session_scope
from app.models import Asset, AppVersion, ConversionJob

from .conftest import Client, ok
from .helpers import convert, make_published_app, mock_pay, upload, wav_bytes


def test_real_file_to_publication_and_purchase():
    dev = Client().register(developer=True)
    mod = Client().login("moderator@nap.demo")
    pub = make_published_app(dev, mod)
    # catalog shows it to guests (criterion 8)
    guest = Client()
    card = ok(guest.get(f"/catalog/apps/{pub['slug']}"))
    assert card["prices"][0]["amount_minor"] == 10000
    assert card["program"][0]["duration_seconds"] > 2.9
    # QA report proves the layer parameters
    qa = pub["job"]["report"]
    assert abs(qa["layer"]["beat_hz"] - 10) <= 0.1 and qa["levels"]["true_peak_master_dbtp"] <= -1.0

    buyer = Client().register()
    dev_no = Client().register()
    # unpaid user gets no session and no file (criterion 9)
    devices = ok(buyer.post("/devices", {"adapter": "simulated", "profile": "sim-chair-v1"}), 201)
    r = buyer.post("/sessions", {"app_id": pub["app_id"], "session_key": "s1", "device_id": devices["id"]})
    assert r.status_code == 403 and r.json()["error"]["code"] == "no_entitlement"
    # the browser cannot set the price: only kind is accepted, server computes the amount (criterion 15)
    co = ok(buyer.post("/checkout/digital", {"app_id": pub["app_id"], "kind": "one_time", "amount_minor": 1}), 201)
    assert co["test_mode"] is True
    with session_scope() as db:
        from app.models import DigitalOrder
        import uuid
        assert db.get(DigitalOrder, uuid.UUID(co["order_id"])).amount_minor == 10000
    # redirect alone does not grant access
    assert not ok(buyer.get(f"/catalog/apps/{pub['slug']}"))["access"]["has_access"]
    mock_pay(buyer, co["checkout_url"])
    lib = ok(buyer.get("/library"))
    assert any(i["slug"] == pub["slug"] and i["access"]["has_access"] for i in lib["items"])
    s = ok(buyer.post("/sessions", {"app_id": pub["app_id"], "session_key": "s1", "device_id": devices["id"]}), 201)
    url = s["grant"]["asset"]["url"]
    f = buyer.raw().get(url)
    assert f.status_code == 200 and f.content[:4] == b"fLaC"
    # session is pinned to the published version
    assert s["grant"]["version_id"] == pub["version_id"]
    # another user cannot use the session or the file grant
    assert dev_no.get(f"/sessions/{s['id']}").status_code == 404


def test_isolation_between_developers_and_users():
    """Criterion 13: no access to foreign sources, finances, conversions or measurements."""
    dev1 = Client().register(developer=True)
    dev2 = Client().register(developer=True)
    a = ok(dev1.post("/developer/apps", {"slug": "iso-" + dev1.user_id[:8], "author_name": "A",
                                         "metadata": {"ru": {"title": "Изоляция"}}}), 201)
    up = upload(dev1, a["id"], wav_bytes(1.0))
    job = convert(dev1, a["id"], up["ticket"]["asset_id"])
    assert dev2.get(f"/developer/apps/{a['id']}").status_code == 404
    assert dev2.get(f"/developer/conversions/{job['id']}").status_code == 404
    assert dev2.post(f"/developer/apps/{a['id']}/uploads", {"filename": "x.wav", "size_bytes": 10}).status_code == 404
    # dev2 cannot start a conversion on dev1's asset
    r = dev2.post("/developer/conversions", {"app_id": a["id"], "source_asset_id": up["ticket"]["asset_id"],
                                             "profile_id": job["profile"]["slug"]})
    assert r.status_code == 404
    fin2 = ok(dev2.get("/developer/finance"))
    assert fin2["summary"] == [] and fin2["orders"] == []
    # a regular user is not a developer
    user = Client().register()
    assert user.get("/developer/apps").status_code == 403
    assert user.get("/admin/audit").status_code == 403
    assert user.get("/moderation/queue").status_code == 403
    # measurements of other users are unreachable
    assert user.get("/me/sessions").json()["items"] == []


def test_substitution_cannot_bypass_moderation():
    """Criterion 14: files/manifest/profile substitutions after submission are detected; frozen versions."""
    dev = Client().register(developer=True)
    mod = Client().login("moderator@nap.demo")
    a = ok(dev.post("/developer/apps", {"slug": "sub-" + dev.user_id[:8], "author_name": "A",
                                        "metadata": {"ru": {"title": "Подмена"}}}), 201)
    up = upload(dev, a["id"], wav_bytes(1.0))
    job = convert(dev, a["id"], up["ticket"]["asset_id"])
    v = a["version_id"]
    ok(dev.patch(f"/developer/apps/{a['id']}/versions/{v}", {"confirm_rights": True}))
    ok(dev.put(f"/developer/apps/{a['id']}/versions/{v}/program",
               {"sessions": [{"key": "s1", "titles": {"ru": "С"}, "conversion_job_id": job["id"]}]}))
    ok(dev.put(f"/developer/apps/{a['id']}/price", {"kind": "one_time", "amount_minor": 500, "currency": "EUR"}))
    ok(dev.post(f"/developer/apps/{a['id']}/versions/{v}/submit"))
    # frozen: no edits after submission
    r = dev.patch(f"/developer/apps/{a['id']}/versions/{v}", {"metadata": {"ru": {"title": "Другое"}}})
    assert r.json()["error"]["code"] == "version_frozen"
    # the source upload link cannot be reused to overwrite
    r = dev.raw().put(up["ticket"]["upload_url"], content=b"x" * 10)
    assert r.status_code == 410
    # tamper with the stored delivery object on disk -> moderation approve refuses
    import uuid
    from app.storage import get_storage
    with session_scope() as db:
        j = db.get(ConversionJob, uuid.UUID(job["id"]))
        a_del = db.get(Asset, uuid.UUID(j.result["delivery"]))
        path = get_storage().local_path(a_del.storage_key)
    original = open(path, "rb").read()
    open(path, "wb").write(original[:-10] + b"0123456789")
    r = mod.post(f"/moderation/versions/{v}/decision", {"decision": "approve"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "integrity_failed"
    open(path, "wb").write(original)
    # tamper with the frozen manifest in DB -> detected as well
    with session_scope() as db:
        ver = db.get(AppVersion, uuid.UUID(v))
        m = dict(ver.manifest)
        m["dsp"] = dict(m["dsp"], profile="something-else")
        ver.manifest = m
    r = mod.post(f"/moderation/versions/{v}/decision", {"decision": "approve"})
    assert r.json()["error"]["code"] == "integrity_failed"


def test_manifest_schema_rejects_scripts_and_urls(client):
    dev = Client().register(developer=True)
    m = {"schema_version": "1.0", "app_id": "00000000-0000-0000-0000-000000000000", "app_version": "1.0",
         "version_number": 1, "author": {"name": "x", "developer_id": "00000000-0000-0000-0000-000000000000"},
         "metadata": {"ru": {"title": "x"}}, "content_language": "ru",
         "device": {"min_api_version": "1.0", "required_capabilities": []},
         "playback": {"delivery_format": "flac", "sample_rate": 48000, "channels": 2, "requires_channel_separation": True},
         "dsp": {"profile": "a", "profile_version": 1, "engine_version": "x"},
         "sessions": [{"id": "s1", "position": 1, "title": {"ru": "t"}, "asset_id": "00000000-0000-0000-0000-000000000000",
                       "sha256": "0" * 64, "duration_seconds": 1, "dsp_profile": "a", "dsp_profile_version": 1}],
         "questionnaires": {"pre": None, "post": None}}
    assert ok(dev.post("/developer/manifest/validate", m))["valid"]
    for evil in ({"script": "alert(1)"}, {"hardware_commands": ["motor 100"]}, {"download_url": "http://x"}):
        assert not ok(dev.post("/developer/manifest/validate", m | evil))["valid"]


def test_signed_link_expires_and_needs_new_grant(monkeypatch):
    """Criterion 15: an expired link requires a fresh authorized grant."""
    from app.storage import get_storage
    import app.storage as st
    with session_scope() as db:
        from sqlalchemy import select
        a = db.scalar(select(Asset).where(Asset.kind == "delivery"))
        key = a.storage_key
    url = get_storage().signed_get_url(key, "x.flac", "audio/flac", ttl=1)
    c = Client()
    assert c.raw().get(url).status_code == 200
    real = time.time
    monkeypatch.setattr(st.time, "time", lambda: real() + 5)
    r = c.raw().get(url)
    assert r.status_code == 410 and r.json()["error"]["code"] == "link_expired"
    # tampered token
    assert c.raw().get(url[:-3] + "abc").status_code == 410


def test_upload_checks_size_and_checksum():
    dev = Client().register(developer=True)
    a = ok(dev.post("/developer/apps", {"slug": "up-" + dev.user_id[:8], "author_name": "A",
                                        "metadata": {"ru": {"title": "Загрузка"}}}), 201)
    data = wav_bytes(0.5)
    t = ok(dev.post(f"/developer/apps/{a['id']}/uploads", {"filename": "a.wav", "size_bytes": len(data),
                                                           "sha256": "0" * 64}), 201)
    r = dev.raw().put(t["upload_url"], content=data)
    assert r.json()["error"]["code"] == "upload_checksum_mismatch"
    t = ok(dev.post(f"/developer/apps/{a['id']}/uploads", {"filename": "a.wav", "size_bytes": len(data) - 5}), 201)
    r = dev.raw().put(t["upload_url"], content=data)
    assert r.json()["error"]["code"] == "upload_size_mismatch"
    big = dev.post(f"/developer/apps/{a['id']}/uploads", {"filename": "a.wav", "size_bytes": 2 * 1024 ** 3})
    assert big.json()["error"]["code"] == "file_too_large"
    junk = b"not audio" * 100
    t = ok(dev.post(f"/developer/apps/{a['id']}/uploads", {"filename": "a.wav", "size_bytes": len(junk)}), 201)
    r = dev.raw().put(t["upload_url"], content=junk)
    assert r.status_code == 422


def test_csrf_and_locale_of_errors():
    c = Client().register()
    r = c.raw().post("/api/v1/devices", json={"adapter": "simulated"})  # cookie auth without CSRF header
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    r_ru = c.raw().get("/api/v1/catalog/apps/does-not-exist", headers={"x-locale": "ru"}).json()["error"]
    r_en = c.raw().get("/api/v1/catalog/apps/does-not-exist", headers={"x-locale": "en"}).json()["error"]
    assert r_ru["code"] == r_en["code"] == "not_found" and r_ru["message"] != r_en["message"]
    assert r_ru["request_id"]
