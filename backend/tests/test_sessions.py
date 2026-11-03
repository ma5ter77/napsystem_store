"""Criteria 16-20: capabilities, idempotent commands, single session per device, L/R honesty, offline sync,
questionnaires and measurements."""
import uuid
from datetime import timedelta

from app.db import utcnow

from .conftest import Client, ok


def _buyer_with_free_app():
    b = Client().register()
    app = ok(b.get("/catalog/apps/night-stories"))
    ok(b.post("/checkout/digital", {"app_id": app["id"], "kind": "free"}), 201)
    return b, app


def _cmd(c, sid, cmd, ver, data=None, cid=None):
    return c.post(f"/sessions/{sid}/commands", {"command_id": cid or uuid.uuid4().hex, "command": cmd,
                                                "expected_version": ver, "data": data or {}})


def test_incompatible_capability_blocks_start_with_reason():
    b, app = _buyer_with_free_app()
    spk = ok(b.post("/devices", {"adapter": "simulated", "profile": "sim-speakers"}), 201)
    r = b.post("/sessions", {"app_id": app["id"], "session_key": "harbor-night", "device_id": spk["id"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "incompatible_device"
    assert "missing_capability:stereo_separation" in r.json()["error"]["details"]["reasons"]
    old = ok(b.post("/devices", {"adapter": "simulated", "profile": "sim-legacy-api"}), 201)
    # night-stories requires api 1.0; legacy device reports 0.9
    r = b.post("/sessions", {"app_id": app["id"], "session_key": "harbor-night", "device_id": old["id"]})
    assert "device_api_too_old" in r.json()["error"]["details"]["reasons"]
    assert b.post("/devices", {"adapter": "nap_chair"}).json()["error"]["code"] == "device_protocol_unavailable"


def test_commands_idempotent_and_one_session_per_device():
    b, app = _buyer_with_free_app()
    dev = ok(b.post("/devices", {"adapter": "simulated", "profile": "sim-chair-v1"}), 201)
    s = ok(b.post("/sessions", {"app_id": app["id"], "session_key": "harbor-night", "device_id": dev["id"]}), 201)
    # second concurrent session on the same device is rejected
    r = b.post("/sessions", {"app_id": app["id"], "session_key": "harbor-night", "device_id": dev["id"]})
    assert r.json()["error"]["code"] == "device_busy"
    sid = s["id"]
    r1 = ok(_cmd(b, sid, "precheck", 0, {}, "c1"))
    assert r1["precheck"]["reasons"] == []  # hardware-verified separation (simulated device)
    again = ok(_cmd(b, sid, "precheck", 0, {}, "c1"))
    assert again["replayed"] and again["state_version"] == r1["state_version"]
    ok(_cmd(b, sid, "confirm_ready", 1))
    st = ok(_cmd(b, sid, "start", 2, cid="start-1"))
    assert st["state"] == "running"
    # stale expected version -> conflict, nothing re-executed
    r = _cmd(b, sid, "pause", 1)
    assert r.json()["error"]["code"] == "version_conflict"
    assert ok(_cmd(b, sid, "start", 2, cid="start-1"))["replayed"]
    ok(_cmd(b, sid, "pause", 3))
    ok(_cmd(b, sid, "resume", 4))
    done = ok(_cmd(b, sid, "complete", 5, {"position_seconds": 30}))
    assert done["state"] == "completed"
    # device is free again
    ok(b.post("/sessions", {"app_id": app["id"], "session_key": "harbor-night", "device_id": dev["id"]}), 201)


def test_web_headphones_lr_test_is_user_confirmation_not_measurement():
    b, app = _buyer_with_free_app()
    web = ok(b.post("/devices", {"adapter": "web_headphones"}), 201)
    s = ok(b.post("/sessions", {"app_id": app["id"], "session_key": "harbor-night", "device_id": web["id"]}), 201)
    pc = ok(_cmd(b, s["id"], "precheck", 0, {"headphones_confirmed": False, "lr_test_passed": False}))
    assert "headphones_confirmation_required" in pc["precheck"]["reasons"]
    assert _cmd(b, s["id"], "confirm_ready", 1).json()["error"]["code"] == "incompatible_device"
    ok(_cmd(b, s["id"], "stop", 1))
    s = ok(b.post("/sessions", {"app_id": app["id"], "session_key": "harbor-night", "device_id": web["id"]}), 201)
    ok(_cmd(b, s["id"], "precheck", 0, {"headphones_confirmed": True, "lr_test_passed": True}))
    ok(_cmd(b, s["id"], "confirm_ready", 1))
    v = ok(b.get(f"/sessions/{s['id']}"))
    assert v["channel_separation_source"] == "user_confirmed"
    assert "not a hardware isolation measurement" in v["precheck"]["note"]
    g = s["grant"]["playback"]
    assert g == {"layout": "headphones", "channels": 2, "channel_order": ["FL", "FR"], "available": ["headphones"],
                 "sample_rate": 48000, "mono_downmix": False, "crossfeed": False, "channel_swap": False}


def test_offline_buffered_events_sync_once_and_local_stop():
    b, app = _buyer_with_free_app()
    web = ok(b.post("/devices", {"adapter": "web_headphones"}), 201)
    s = ok(b.post("/sessions", {"app_id": app["id"], "session_key": "harbor-night", "device_id": web["id"]}), 201)
    sid = s["id"]
    ok(_cmd(b, sid, "precheck", 0, {"headphones_confirmed": True, "lr_test_passed": True}))
    ok(_cmd(b, sid, "confirm_ready", 1))
    ok(_cmd(b, sid, "start", 2))
    t = utcnow()
    # the client lost connectivity: it kept playing, stopped locally and buffered the events
    buffered = [{"event_id": f"e{i}", "seq": i, "type": "progress", "occurred_at": (t + timedelta(seconds=i)).isoformat(),
                 "data": {"position_seconds": i * 5}} for i in range(1, 4)]
    buffered.append({"event_id": "e4", "seq": 4, "type": "state", "occurred_at": (t + timedelta(seconds=20)).isoformat(),
                     "data": {"state": "cancelled", "reason": "local_stop"}})
    r1 = ok(b.post(f"/sessions/{sid}/events", {"events": buffered[2:] + buffered[:2]}))  # out of order
    assert sorted(r1["accepted"]) == ["e1", "e2", "e3", "e4"] and r1["state"] == "cancelled"
    r2 = ok(b.post(f"/sessions/{sid}/events", {"events": buffered}))  # resend after reconnect
    assert r2["accepted"] == [] and len(r2["duplicates"]) == 4
    # an old start command replayed after reconnect is not executed again
    assert _cmd(b, sid, "start", 2).json()["error"]["code"] in ("version_conflict", "invalid_transition")


def test_questionnaire_skip_consent_and_no_invented_measurements():
    b, app = _buyer_with_free_app()
    dev = ok(b.post("/devices", {"adapter": "simulated", "profile": "sim-chair-v1"}), 201)
    s = ok(b.post("/sessions", {"app_id": app["id"], "session_key": "harbor-night", "device_id": dev["id"]}), 201)
    sid = s["id"]
    # skipping works
    assert ok(b.post(f"/sessions/{sid}/questionnaire", {"phase": "pre", "status": "skipped"}))["status"] == "skipped"
    # interrupted session: no measurements appear by themselves
    ok(_cmd(b, sid, "stop", 0))
    v = ok(b.get(f"/sessions/{sid}"))
    assert v["state"] == "cancelled" and v["measurements"] == [] and v["pre"] == {}
    # without consent: sensor data refused
    s2 = ok(b.post("/sessions", {"app_id": app["id"], "session_key": "harbor-night", "device_id": dev["id"]}), 201)
    m = {"type": "heart_rate", "metric": "bpm", "value": 62, "unit": "bpm", "occurred_at": utcnow().isoformat(),
         "synthetic": True}
    assert b.post(f"/sessions/{s2['id']}/measurements", m).json()["error"]["code"] == "consent_required"
    ok(b.put("/me/consent", {"granted": True}))
    # simulator data must be explicitly marked synthetic
    assert b.post(f"/sessions/{s2['id']}/measurements", m | {"synthetic": False}).json()["error"]["code"] == \
        "synthetic_must_be_marked"
    assert ok(b.post(f"/sessions/{s2['id']}/measurements", m), 201)["synthetic"]
    # HRV is HRV (ms), never EEG; EEG is not accepted in this release
    hrv = m | {"type": "hrv_rmssd", "metric": "rmssd", "unit": "ms", "value": 48}
    ok(b.post(f"/sessions/{s2['id']}/measurements", hrv), 201)
    assert b.post(f"/sessions/{s2['id']}/measurements", m | {"type": "eeg", "unit": "uV2"}).json()["error"]["code"] \
        == "eeg_not_supported"
    assert b.post(f"/sessions/{s2['id']}/measurements", hrv | {"unit": "uV2"}).status_code == 422
    # pre/post answers and plain differences
    ok(b.post(f"/sessions/{s2['id']}/questionnaire", {"phase": "pre", "status": "completed",
                                                      "answers": {"relaxation": 3, "tension": 7, "alertness": 5}}))
    for i, cmd in enumerate(["precheck", "confirm_ready", "start", "complete"]):
        ok(_cmd(b, s2["id"], cmd, i))
    ok(b.post(f"/sessions/{s2['id']}/questionnaire", {"phase": "post", "status": "completed",
                                                      "answers": {"relaxation": 6, "tension": 4, "alertness": 5}}))
    v = ok(b.get(f"/sessions/{s2['id']}"))
    assert v["delta"] == {"relaxation": 3, "tension": -3, "alertness": 0}
    types = {(x["type"], x["unit"]) for x in v["measurements"]}
    assert types == {("heart_rate", "bpm"), ("hrv_rmssd", "ms")} and all(x["synthetic"] for x in v["measurements"])
    # deletion
    assert ok(b.delete("/me/measurements"))["deleted"] >= 2
    assert ok(b.get(f"/sessions/{s2['id']}"))["measurements"] == []


def test_developer_analytics_suppresses_small_groups():
    dev2 = Client().login("dev2@nap.demo")
    items = ok(dev2.get("/developer/analytics"))["items"]
    ns = next(i for i in items if i["slug"] == "night-stories")
    if ns["usage"].get("suppressed"):
        assert ns["usage"]["reason"] == "insufficient_group_size"
    else:
        assert ns["usage"]["users"] >= 5


def test_seek_backwards_position_and_late_events():
    """NAP-042: seeking back is stored; an older event delivered late does not move the position forward again."""
    b, app = _buyer_with_free_app()
    web = ok(b.post("/devices", {"adapter": "web_headphones"}), 201)
    s = ok(b.post("/sessions", {"app_id": app["id"], "session_key": "harbor-night", "device_id": web["id"]}), 201)
    sid = s["id"]
    ok(_cmd(b, sid, "precheck", 0, {"headphones_confirmed": True, "lr_test_passed": True}))
    ok(_cmd(b, sid, "confirm_ready", 1))
    ok(_cmd(b, sid, "start", 2))
    t = utcnow()
    ev = lambda i, pos, **extra: {"event_id": f"s{i}", "seq": i, "type": "progress",  # noqa: E731
                                  "occurred_at": (t + timedelta(seconds=i)).isoformat(),
                                  "data": {"position_seconds": pos, **extra}}
    ok(b.post(f"/sessions/{sid}/events", {"events": [ev(1, 60), ev(3, 20, seek_from=60)]}))
    assert ok(b.get(f"/sessions/{sid}"))["position_seconds"] == 20
    ok(b.post(f"/sessions/{sid}/events", {"events": [ev(2, 75)]}))  # older event arrives after the seek
    assert ok(b.get(f"/sessions/{sid}"))["position_seconds"] == 20
    ok(b.post(f"/sessions/{sid}/events", {"events": [ev(4, 35)]}))
    assert ok(b.get(f"/sessions/{sid}"))["position_seconds"] == 35
