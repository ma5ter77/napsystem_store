"""NAP-110: exports of one audio-studio project become the headphones track and the chair 5.1 version of an app
session, imported as they are (no second binaural processing), and play in the store."""
import uuid

import numpy as np

from app.dsp.engine import FS, measure_tone

from .conftest import Client, ok
from .helpers import mock_pay, wait_inline_jobs
from .test_audio_studio import _wait, read_pcm_bytes


def _project(dev, seconds=2):
    pr = ok(dev.post("/audio/projects", {"title": "Chair focus", "bus_count": 6}), 201)
    st = pr["state"]
    n = seconds * FS
    st["generators"] = [{"id": "g1", "type": "binaural", "params": {"carrier_hz": 200, "delta_hz": 8, "amplitude_dbfs": -18}}]
    st["tracks"][0]["clips"] = [{"id": "c1", "generator_id": "g1", "generator_output": "left", "start_frame": 0, "duration_frames": n}]
    st["tracks"][1]["clips"] = [{"id": "c2", "generator_id": "g1", "generator_output": "right", "start_frame": 0, "duration_frames": n}]
    ok(dev.patch(f"/audio/projects/{pr['id']}", {"base_revision": pr["revision"], "state": st}))
    return pr["id"]


def _export(dev, pid, profile):
    j = _wait(dev, ok(dev.post(f"/audio/projects/{pid}/renders", {"profile": profile}), 201)["id"])
    assert j["state"] == "completed", (j["error_code"], j["error_detail"])
    return j


def test_studio_exports_become_app_versions_and_play(tmp_path):
    dev = Client().register(developer=True)
    mod = Client().login("moderator@nap.demo")
    slug = "st-" + uuid.uuid4().hex[:8]
    a = ok(dev.post("/developer/apps", {"slug": slug, "category": "meditation", "author_name": "Studio",
                                        "metadata": {"ru": {"title": "Из студии"}}}), 201)
    pid = _project(dev)
    hp = _export(dev, pid, "HEADPHONES_2_0")
    ch = _export(dev, pid, "NAP_CHAIR_5_1")

    j = ok(dev.post(f"/audio/render-jobs/{hp['id']}/to-app", {"app_id": a["id"]}, headers={"Idempotency-Key": "imp-1"}), 202)
    again = ok(dev.post(f"/audio/render-jobs/{hp['id']}/to-app", {"app_id": a["id"]}, headers={"Idempotency-Key": "imp-1"}), 202)
    assert again["id"] == j["id"] and not again["created"]
    wait_inline_jobs()
    hpj = ok(dev.get(f"/developer/conversions/{j['id']}"))
    assert hpj["state"] == "completed", (hpj["error_code"], hpj.get("error_detail"))
    assert hpj["layout"] == "headphones" and hpj["studio"]["project_title"] == "Chair focus"
    assert hpj["source"]["name"].startswith("Chair focus")
    # imported as it is: the studio's 196 / 204 Hz carriers are still there, not shifted again
    body = dev.raw().get(ok(dev.get(f"/developer/conversions/{j['id']}"))["urls"]["delivery"]).content
    x, _ = read_pcm_bytes(tmp_path, body)
    fl, _ = measure_tone(x[FS // 4:, 0].astype(float), FS, 196.0)
    fr, _ = measure_tone(x[FS // 4:, 1].astype(float), FS, 204.0)
    assert abs(fl - 196.0) < 0.05 and abs(fr - 204.0) < 0.05

    c = ok(dev.post(f"/audio/render-jobs/{ch['id']}/to-app", {"app_id": a["id"]}), 202)
    wait_inline_jobs()
    cj = ok(dev.get(f"/developer/conversions/{c['id']}"))
    assert cj["state"] == "completed" and cj["layout"] == "surround_5_1"
    assert [x["channel"] for x in cj["report"]["channels"]] == ["FL", "FR", "FC", "LFE", "SL", "SR"]
    assert all(x["source"] == "studio" for x in cj["report"]["channels"]) and cj["report"]["verification"]["passed"]
    # the headphones export filled session 1, the 5.1 export of the same project joined it
    v = ok(dev.get(f"/developer/apps/{a['id']}"))["versions"][0]
    assert v["sessions"][0]["conversion_job_id"] == j["id"] and v["sessions"][0]["variants"] == {"surround_5_1": c["id"]}

    # someone else's export or app is not reachable
    other = Client().register(developer=True)
    assert other.post(f"/audio/render-jobs/{hp['id']}/to-app", {"app_id": a["id"]}).status_code == 404
    oa = ok(other.post("/developer/apps", {"slug": "o-" + uuid.uuid4().hex[:8], "author_name": "O",
                                           "metadata": {"ru": {"title": "Чужое"}}}), 201)
    assert dev.post(f"/audio/render-jobs/{hp['id']}/to-app", {"app_id": oa["id"]}).status_code == 404

    ok(dev.patch(f"/developer/apps/{a['id']}/versions/{a['version_id']}", {"confirm_rights": True}))
    ok(dev.put(f"/developer/apps/{a['id']}/price", {"kind": "one_time", "amount_minor": 500, "currency": "EUR"}))
    ok(dev.post(f"/developer/apps/{a['id']}/versions/{a['version_id']}/submit"))
    assert ok(mod.post(f"/moderation/versions/{a['version_id']}/decision", {"decision": "approve", "comment": ""}))["state"] == "published"
    buyer = Client().register()
    mock_pay(buyer, ok(buyer.post("/checkout/digital", {"app_id": a["id"], "kind": "one_time"}), 201)["checkout_url"])
    chair = ok(buyer.post("/devices", {"adapter": "simulated", "profile": "sim-chair-v1"}), 201)
    s = ok(buyer.post("/sessions", {"app_id": a["id"], "session_key": "s1", "device_id": chair["id"], "max_channels": 6}), 201)
    assert s["grant"]["playback"]["layout"] == "surround_5_1"
    f = buyer.raw().get(s["grant"]["asset"]["url"]).content
    y, info = read_pcm_bytes(tmp_path, f)
    assert y.shape[1] == 6 and info["channel_layout"] == "5.1(side)"
    # same placement as the studio rendered it: the binaural buses reach the sides next to the ears, the centre
    # and LFE stay silent in this project (the studio test checks the same layout)
    assert y[:, 4].any() and y[:, 5].any() and not y[:, 2].any() and not y[:, 3].any()
    art = ok(dev.get(f"/audio/render-jobs/{ch['id']}"))["artifact"]
    exp, _ = read_pcm_bytes(tmp_path, dev.raw().get(ok(dev.get(f"/audio/render-jobs/{ch['id']}/download"))["url"]).content)
    if art["bit_depth"] == 24:                # 48 kHz / 24 bit export: the store file has exactly the same samples
        assert np.array_equal(exp, y)


def test_export_for_all_equipment_builds_one_session():
    """NAP-112: one click exports 2.0 / 3.0 / 5.1 for a 6-bus project and sends them to the app, where they become
    one session (headphones track + speaker and chair versions), whatever order they finish in."""
    dev = Client().register(developer=True)
    a = ok(dev.post("/developer/apps", {"slug": "all-" + uuid.uuid4().hex[:8], "author_name": "All",
                                        "metadata": {"ru": {"title": "Для всего"}}}), 201)
    pid = _project(dev)
    r = ok(dev.post(f"/audio/projects/{pid}/renders/all", {"app_id": a["id"]}, headers={"Idempotency-Key": "all-1"}), 201)
    assert [j["profile"] for j in r["items"]] == ["HEADPHONES_2_0", "NAP_ACOUSTIC_3_0", "NAP_CHAIR_5_1"]
    for j in r["items"]:
        assert _wait(dev, j["id"])["state"] == "completed"
    wait_inline_jobs()
    jobs = ok(dev.get(f"/developer/conversions?app_id={a['id']}"))["items"]
    assert sorted(j["layout"] for j in jobs) == ["acoustic_3_0", "headphones", "surround_5_1"]
    assert all(j["state"] == "completed" for j in jobs)
    v = ok(dev.get(f"/developer/apps/{a['id']}"))["versions"][0]
    assert len(v["sessions"]) == 1 and set(v["sessions"][0]["variants"]) == {"acoustic_3_0", "surround_5_1"}
    # someone else's app cannot be a target
    other = Client().register(developer=True)
    oa = ok(other.post("/developer/apps", {"slug": "o-" + uuid.uuid4().hex[:8], "author_name": "O",
                                           "metadata": {"ru": {"title": "Чужое"}}}), 201)
    assert dev.post(f"/audio/projects/{pid}/renders/all", {"app_id": oa["id"]}).status_code == 404


def test_layout_version_finished_first_joins_the_session_later():
    """The 5.1 version is imported before the headphones track: it joins when the headphones track fills session 1."""
    dev = Client().register(developer=True)
    a = ok(dev.post("/developer/apps", {"slug": "ord-" + uuid.uuid4().hex[:8], "author_name": "Ord",
                                        "metadata": {"ru": {"title": "Порядок"}}}), 201)
    pid = _project(dev)
    ch = _export(dev, pid, "NAP_CHAIR_5_1")
    hp = _export(dev, pid, "HEADPHONES_2_0")
    ok(dev.post(f"/audio/render-jobs/{ch['id']}/to-app", {"app_id": a["id"]}), 202)
    wait_inline_jobs()
    assert ok(dev.get(f"/developer/apps/{a['id']}"))["versions"][0]["sessions"] == []
    ok(dev.post(f"/audio/render-jobs/{hp['id']}/to-app", {"app_id": a["id"]}), 202)
    wait_inline_jobs()
    s = ok(dev.get(f"/developer/apps/{a['id']}"))["versions"][0]["sessions"]
    assert len(s) == 1 and list(s[0]["variants"]) == ["surround_5_1"]
