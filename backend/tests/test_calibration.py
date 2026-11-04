"""NAP-101: the listener calibrates level and delay per channel of their speakers / chair, per layout."""
from .conftest import Client, ok


def test_calibration_per_layout_is_validated_and_kept():
    u = Client().register()
    chair = ok(u.post("/devices", {"adapter": "simulated", "profile": "sim-chair-v1"}), 201)
    assert chair["calibration"] == {}
    d = ok(u.put(f"/devices/{chair['id']}/calibration", {"layout": "surround_5_1", "channels": {
        "FL": {"gain_db": -1.5, "delay_ms": 2.25}, "FR": {"gain_db": 0, "delay_ms": 0}, "LFE": {"gain_db": 3}}}))
    assert d["calibration"] == {"surround_5_1": {"FL": {"gain_db": -1.5, "delay_ms": 2.2}, "LFE": {"gain_db": 3.0, "delay_ms": 0.0}}}
    ok(u.put(f"/devices/{chair['id']}/calibration", {"layout": "acoustic_3_0", "channels": {"FC": {"gain_db": 2}}}))
    listed = next(x for x in ok(u.get("/devices"))["items"] if x["id"] == chair["id"])
    assert set(listed["calibration"]) == {"surround_5_1", "acoustic_3_0"}
    # limits: ±6 dB, 0–20 ms, channels of that layout only; headphones are never calibrated (the binaural pair)
    for body in ({"layout": "surround_5_1", "channels": {"FL": {"gain_db": 7}}},
                 {"layout": "surround_5_1", "channels": {"FL": {"delay_ms": 25}}},
                 {"layout": "acoustic_3_0", "channels": {"SL": {"gain_db": 1}}},
                 {"layout": "surround_5_1", "channels": {"FL": {"gain_db": "loud"}}}):
        r = u.put(f"/devices/{chair['id']}/calibration", body)
        assert r.status_code == 422, body
    assert u.put(f"/devices/{chair['id']}/calibration", {"layout": "headphones", "channels": {}}).status_code == 422
    # reset one layout; someone else's device does not exist for you
    d = ok(u.put(f"/devices/{chair['id']}/calibration", {"layout": "acoustic_3_0", "channels": {}}))
    assert set(d["calibration"]) == {"surround_5_1"}
    assert Client().register().put(f"/devices/{chair['id']}/calibration", {"layout": "surround_5_1"}).status_code == 404
