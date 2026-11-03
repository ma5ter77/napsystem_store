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
