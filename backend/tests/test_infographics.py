"""NAP-094: data behind the charts — the app page gets its DSP profile's carriers."""
from .conftest import Client, ok


def test_app_page_has_profile_signal():
    a = ok(Client().get("/catalog/apps/quiet-harbor"))
    assert a["signal"] == {"carrier_center_hz": 255.0, "beat_hz": 10.0, "beat_curve": []}
    assert a["dsp_profile"]["profile"] == "alpha-demo-v1"
