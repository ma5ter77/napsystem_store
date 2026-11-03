"""NAP-108: an app with versions for headphones 2.0, speakers 3.0 and the NAP chair 5.1 / 7.1, each channel from the
author's own file or the main track; the player gets the richest version the listener's output can play."""
import uuid

from app.db import session_scope
from app.models import ProgramSession

from .conftest import Client, ok
from .helpers import mock_pay, profile_id, upload, wait_inline_jobs, wav_bytes


def _job(dev, app_id, asset_id, layout="headphones", stems=None):
    j = ok(dev.post("/developer/conversions", {"app_id": app_id, "source_asset_id": asset_id,
                                               "profile_id": profile_id(dev), "layout": layout, "stems": stems or {}}), 202)
    wait_inline_jobs()
    return ok(dev.get(f"/developer/conversions/{j['id']}"))


def test_layouts_from_upload_to_player():
    dev = Client().register(developer=True)
    mod = Client().login("moderator@nap.demo")
    slug = "lay-" + uuid.uuid4().hex[:8]
    a = ok(dev.post("/developer/apps", {"slug": slug, "category": "meditation", "author_name": "Layouts",
                                        "metadata": {"ru": {"title": "Раскладки"}}}), 201)
    main = upload(dev, a["id"], wav_bytes(4.0))["ticket"]["asset_id"]
    voice = upload(dev, a["id"], wav_bytes(4.0, channels=1, freq=330.0), name="voice.wav")["ticket"]["asset_id"]

    # headphones track: becomes session 1 by itself (NAP-069)
    hp = _job(dev, a["id"], main)
    assert hp["state"] == "completed" and hp["layout"] == "headphones"
    # 5.1 with the voice in the centre, the rest from the main file: joins session 1 by itself
    c51 = _job(dev, a["id"], main, "surround_5_1", {"FC": voice})
    assert c51["state"] == "completed", c51
    assert c51["channels"] == ["FL", "FR", "FC", "LFE", "SL", "SR"]
    own = {c["channel"]: c["uploaded"] for c in c51["report"]["channels"]}
    assert own == {"FL": False, "FR": False, "FC": True, "LFE": False, "SL": False, "SR": False}
    assert c51["stems"]["FC"]["name"] == "voice.wav"
    v = ok(dev.get(f"/developer/apps/{a['id']}"))["versions"][0]
    assert v["sessions"][0]["variants"] == {"surround_5_1": c51["id"]}
    # 3.0 without own files, set on the Program tab together with 5.1
    c30 = _job(dev, a["id"], main, "acoustic_3_0")
    assert c30["result"]["channels"] == 3
    ok(dev.put(f"/developer/apps/{a['id']}/versions/{a['version_id']}/program", {"sessions": [
        {"key": "s1", "titles": {"ru": "Сеанс"}, "conversion_job_id": hp["id"],
         "variants": {"surround_5_1": c51["id"], "acoustic_3_0": c30["id"]}}]}))

    # wrong combinations are refused with a field the form can point at
    bad = dev.put(f"/developer/apps/{a['id']}/versions/{a['version_id']}/program", {"sessions": [
        {"key": "s1", "titles": {"ru": "Сеанс"}, "conversion_job_id": hp["id"], "variants": {"surround_7_1": c51["id"]}}]})
    assert bad.status_code == 422 and bad.json()["error"]["details"]["field"] == "sessions.1.variants.surround_7_1"
    bad = dev.put(f"/developer/apps/{a['id']}/versions/{a['version_id']}/program", {"sessions": [
        {"key": "s1", "titles": {"ru": "Сеанс"}, "conversion_job_id": c51["id"]}]})
    assert bad.status_code == 422 and bad.json()["error"]["details"]["field"] == "sessions.1.track"
    longer = upload(dev, a["id"], wav_bytes(7.0), name="long.wav")["ticket"]["asset_id"]
    c71 = _job(dev, a["id"], longer, "surround_7_1")
    bad = dev.put(f"/developer/apps/{a['id']}/versions/{a['version_id']}/program", {"sessions": [
        {"key": "s1", "titles": {"ru": "Сеанс"}, "conversion_job_id": hp["id"], "variants": {"surround_7_1": c71["id"]}}]})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "variant_duration_mismatch"
    # own files only on channels the layout has
    r = dev.post("/developer/conversions", {"app_id": a["id"], "source_asset_id": main, "profile_id": profile_id(dev),
                                            "layout": "acoustic_3_0", "stems": {"SL": voice}})
    assert r.status_code == 422 and r.json()["error"]["details"]["field"] == "stems.SL"
    r = dev.post("/developer/conversions", {"app_id": a["id"], "source_asset_id": main, "profile_id": profile_id(dev),
                                            "stems": {"FC": voice}})
    assert r.status_code == 422

    # publish: the manifest lists the layouts and the integrity check covers their files
    ok(dev.patch(f"/developer/apps/{a['id']}/versions/{a['version_id']}", {"confirm_rights": True}))
    ok(dev.put(f"/developer/apps/{a['id']}/price", {"kind": "one_time", "amount_minor": 500, "currency": "EUR"}))
    ok(dev.post(f"/developer/apps/{a['id']}/versions/{a['version_id']}/submit"))
    assert ok(mod.post(f"/moderation/versions/{a['version_id']}/decision", {"decision": "approve", "comment": ""}))["state"] == "published"
    card = ok(Client().get(f"/catalog/apps/{slug}"))
    assert card["layouts"] == ["headphones", "acoustic_3_0", "surround_5_1"]
    assert card["program"][0]["layouts"] == ["headphones", "acoustic_3_0", "surround_5_1"]

    buyer = Client().register()
    co = ok(buyer.post("/checkout/digital", {"app_id": a["id"], "kind": "one_time"}), 201)
    mock_pay(buyer, co["checkout_url"])
    chair = ok(buyer.post("/devices", {"adapter": "simulated", "profile": "sim-chair-v1"}), 201)
    phones = ok(buyer.post("/devices", {"adapter": "web_headphones"}), 201)

    def start(device, channels):
        s = ok(buyer.post("/sessions", {"app_id": a["id"], "session_key": "s1", "device_id": device["id"],
                                        "max_channels": channels}), 201)
        ok(buyer.post(f"/sessions/{s['id']}/commands", {"command_id": uuid.uuid4().hex, "command": "stop",
                                                         "expected_version": s["state_version"], "data": {}}))
        return s["grant"]

    g = start(chair, 6)        # chair on a 5.1 output: the 5.1 version
    assert g["playback"]["layout"] == "surround_5_1" and g["playback"]["channels"] == 6
    assert g["playback"]["channel_order"] == ["FL", "FR", "FC", "LFE", "SL", "SR"]
    f = buyer.raw().get(g["asset"]["url"])
    assert f.status_code == 200 and f.content[:4] == b"fLaC"
    assert start(chair, 8)["playback"]["layout"] == "surround_5_1"     # no 7.1 version: best that exists
    assert start(chair, 3)["playback"]["layout"] == "acoustic_3_0"
    assert start(chair, 2)["playback"]["layout"] == "headphones"       # a stereo output plays the 2.0 track
    assert start(phones, 8)["playback"]["layout"] == "headphones"      # headphones always get the 2.0 track
    with session_scope() as db:
        ps = db.query(ProgramSession).filter(ProgramSession.conversion_job_id == uuid.UUID(hp["id"])).one()
        assert set(ps.variants) == {"surround_5_1", "acoustic_3_0"}
