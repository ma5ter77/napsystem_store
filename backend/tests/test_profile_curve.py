"""NAP-021: frequency transition profiles through the admin API and a real conversion (needs the database)."""
from .conftest import Client, ok
from .helpers import convert as job_convert, upload, wav_bytes


def test_admin_creates_curved_profile_and_conversion_uses_it():
    """NAP-021 end to end: admin creates a profile with a transition; a conversion reports the curve check."""
    import uuid as _u
    admin = Client().login("admin@nap.demo")
    slug = "ramp-" + _u.uuid4().hex[:6]
    params = {"carrier_center_hz": 255.0, "beat_hz": 10.0, "source_gain_db": -6.0, "tone_gain_dbfs": -30.0,
              "fade_in_seconds": 0.5, "fade_out_seconds": 0.5}
    bad = admin.post("/admin/profiles", {"slug": slug, "names": {"ru": "R", "en": "R"},
                                         "params": {**params, "beat_curve": [[0, 10.0], [5, 80.0]]}})
    assert bad.status_code == 422
    ok(admin.post("/admin/profiles", {"slug": slug, "names": {"ru": "Переход 10→6", "en": "Ramp 10→6"},
                                      "params": {**params, "beat_curve": [[0, 10.0], [8, 6.0]]}}), 201)
    dev = Client().register(developer=True)
    a = ok(dev.post("/developer/apps", {"slug": "rp-" + _u.uuid4().hex[:8], "category": "meditation", "author_name": "A",
                                        "metadata": {"ru": {"title": "R"}}, "content_language": "ru"}), 201)
    up = upload(dev, a["id"], wav_bytes(12.0))
    job = job_convert(dev, a["id"], up["ticket"]["asset_id"], profile=slug)
    assert job["state"] == "completed", job
    layer = job["report"]["layer"]
    assert layer["curve"]["points"] and layer["curve"]["max_error_hz"] <= 0.05
    assert job["profile"]["beat_curve"] == [[0.0, 10.0], [8.0, 6.0]]
