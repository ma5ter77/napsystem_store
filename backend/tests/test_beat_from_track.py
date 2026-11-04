"""NAP-117: the beat comes from the uploaded track itself; the carrier layer is optional; the author's beat editor."""
import uuid

from .conftest import Client, ok
from .helpers import profile_id, upload, wait_inline_jobs, wav_bytes


def _app(dev):
    return ok(dev.post("/developer/apps", {"slug": "bt-" + uuid.uuid4().hex[:8], "author_name": "Beat",
                                           "metadata": {"ru": {"title": "Ритм из трека"}}}), 201)


def _run(dev, a, asset, **body):
    j = ok(dev.post("/developer/conversions", {"app_id": a["id"], "source_asset_id": asset, "profile_id": profile_id(dev),
                                               **body}), 202)
    wait_inline_jobs()
    return ok(dev.get(f"/developer/conversions/{j['id']}"))


def test_default_is_the_beat_from_the_track_and_the_layer_is_optional():
    dev = Client().register(developer=True)
    a = _app(dev)
    asset = upload(dev, a["id"], wav_bytes(4.0))["ticket"]["asset_id"]
    plain = _run(dev, a, asset)                                     # no method given: beat from the track
    assert plain["state"] == "completed", plain
    assert plain["method"] == "shift" and plain["report"]["beat_method"] == "shift"
    assert plain["report"]["layer"] == "not_added" and plain["report"]["verification"]["passed"]
    assert [c["channel"] for c in plain["report"]["channels"]] == ["FL", "FR"]
    pt = plain["report"]["verification"]["points"][0]
    assert abs(pt["measured_beat_hz"] - 10.0) <= 0.1
    # the first finished track still becomes session 1 (NAP-069)
    assert ok(dev.get(f"/developer/apps/{a['id']}"))["versions"][0]["sessions"][0]["conversion_job_id"] == plain["id"]

    both = _run(dev, a, asset, method="shift_layer")
    assert both["state"] == "completed" and both["report"]["beat_method"] == "shift_layer"
    assert abs(both["report"]["layer"]["beat_hz"] - 10.0) <= 0.1

    legacy = _run(dev, a, asset, method="layer")                    # the MVP method stays available
    assert legacy["state"] == "completed" and isinstance(legacy["report"]["layer"], dict)

    r = dev.post("/developer/conversions", {"app_id": a["id"], "source_asset_id": asset, "profile_id": profile_id(dev),
                                            "layout": "surround_5_1", "method": "layer"})
    assert r.status_code == 422 and r.json()["error"]["details"]["field"] == "method"


def test_beat_editor_sets_the_beat_and_a_transition():
    dev = Client().register(developer=True)
    a = _app(dev)
    asset = upload(dev, a["id"], wav_bytes(6.0))["ticket"]["asset_id"]
    j = _run(dev, a, asset, beat={"beat_hz": 6, "to_hz": 4, "over_minutes": 0.1})
    assert j["state"] == "completed", j
    assert j["profile"]["beat_hz"] == 6.0 and j["profile"]["beat_curve"] == [[0.0, 6.0], [6.0, 4.0]]
    assert j["profile"]["edited"] == {"beat_hz": 6, "to_hz": 4, "over_minutes": 0.1}
    beats = sorted(p["beat_hz"] for p in j["report"]["verification"]["points"])
    assert beats == [4.0, 6.0] and all(p["error_hz"] <= 0.1 for p in j["report"]["verification"]["points"])
    for bad in ({"beat_hz": 50}, {"beat_hz": 6, "to_hz": 4}, {"layer_dbfs": -5}):
        r = dev.post("/developer/conversions", {"app_id": a["id"], "source_asset_id": asset, "profile_id": profile_id(dev),
                                                "method": "shift_layer", "beat": bad})
        assert r.status_code == 422, bad


def test_preview_fragment_with_the_beat_settings_and_a_quiet_warning():
    import json
    import numpy as np
    from app.dsp.engine import FS, measure_tone
    dev = Client().register(developer=True)
    a = _app(dev)
    loud = upload(dev, a["id"], wav_bytes(6.0, freq=1000.0))["ticket"]["asset_id"]
    r = dev.post("/developer/conversions/preview", {"app_id": a["id"], "source_asset_id": loud, "profile_id": profile_id(dev),
                                                    "beat": {"beat_hz": 8}, "seconds": 4})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav", r.text
    info = json.loads(r.headers["x-preview"])
    assert info["method"] == "shift" and info["beat_hz"] == 8.0 and not info["quiet"] and abs(info["seconds"] - 4) < 0.01
    x = np.frombuffer(r.content[44:], dtype="<i2").reshape(-1, 2).astype(float) / 32768
    seg = x[FS // 2: 3 * FS]
    assert abs(measure_tone(seg[:, 0], FS, 996.0)[0] - 996.0) < 0.1     # left −4 Hz, right +4 Hz
    assert abs(measure_tone(seg[:, 1], FS, 1004.0)[0] - 1004.0) < 0.1
    # a very quiet track is reported before the conversion
    import struct
    n = 3 * FS
    q = (0.005 * np.sin(2 * np.pi * 440 * np.arange(n) / FS) * 32767).astype("<i2")
    data = np.stack([q, q], 1).tobytes()
    quiet_wav = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 2, FS, FS * 4, 4, 16) + b"data" + struct.pack("<I", len(data)) + data
    quiet = upload(dev, a["id"], quiet_wav, name="quiet.wav")["ticket"]["asset_id"]
    r = dev.post("/developer/conversions/preview", {"app_id": a["id"], "source_asset_id": quiet, "profile_id": profile_id(dev), "seconds": 2})
    assert r.status_code == 200 and json.loads(r.headers["x-preview"])["quiet"] is True
    # someone else's file is not reachable
    other = Client().register(developer=True)
    r = other.post("/developer/conversions/preview", {"app_id": a["id"], "source_asset_id": loud, "profile_id": profile_id(other)})
    assert r.status_code == 404
