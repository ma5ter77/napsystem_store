import hashlib
import io
import uuid

import numpy as np

from app.dsp.pipeline import _wav_header
from app.worker import wait_inline_jobs

from .conftest import Client, ok


def wav_bytes(seconds=3.0, fs=48000, channels=2, freq=220.0) -> bytes:
    n = int(seconds * fs)
    t = np.arange(n) / fs
    sig = 0.25 * np.sin(2 * np.pi * freq * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.5 * t))
    data = sig[:, None] if channels == 1 else np.stack([sig, sig * 0.9], axis=1)
    q = np.clip(np.rint(data * 32767), -32768, 32767).astype("<i2")
    return _wav_header(n, channels, 16, fs) + q.tobytes()


def profile_id(c: Client, slug="alpha-demo-v1") -> str:
    return next(p["id"] for p in ok(c.get("/developer/profiles"))["items"] if p["slug"] == slug)


def upload(c: Client, app_id: str, data: bytes, name="track.wav", with_sha=True) -> dict:
    t = ok(c.post(f"/developer/apps/{app_id}/uploads", {"filename": name, "size_bytes": len(data),
                                                        "sha256": hashlib.sha256(data).hexdigest() if with_sha else None}), 201)
    r = c.raw().put(t["upload_url"], content=data, headers=c._h({"content-type": "application/octet-stream"}))
    return {"ticket": t, "response": r}


def convert(c: Client, app_id: str, asset_id: str, profile="alpha-demo-v1", key=None) -> dict:
    h = {"Idempotency-Key": key} if key else None
    j = ok(c.post("/developer/conversions", {"app_id": app_id, "source_asset_id": asset_id,
                                             "profile_id": profile_id(c, profile)}, headers=h), 202)
    wait_inline_jobs()
    return ok(c.get(f"/developer/conversions/{j['id']}"))


def make_published_app(dev: Client, moderator: Client, kind="one_time", amount=10000, interval=None, seconds=3.0):
    slug = "t-" + uuid.uuid4().hex[:10]
    a = ok(dev.post("/developer/apps", {"slug": slug, "category": "meditation", "author_name": "Test Author",
                                        "metadata": {"ru": {"title": "Тест " + slug}, "en": {"title": "Test " + slug}},
                                        "content_language": "ru"}), 201)
    up = upload(dev, a["id"], wav_bytes(seconds))
    assert up["response"].status_code == 200, up["response"].text
    job = convert(dev, a["id"], up["ticket"]["asset_id"])
    assert job["state"] == "completed", job
    v = a["version_id"]
    ok(dev.patch(f"/developer/apps/{a['id']}/versions/{v}", {"confirm_rights": True}))
    ok(dev.put(f"/developer/apps/{a['id']}/versions/{v}/program",
               {"sessions": [{"key": "s1", "titles": {"ru": "Сеанс", "en": "Session"}, "conversion_job_id": job["id"]}]}))
    ok(dev.put(f"/developer/apps/{a['id']}/price", {"kind": kind, "amount_minor": amount, "currency": "EUR",
                                                     "interval": interval}))
    ok(dev.post(f"/developer/apps/{a['id']}/versions/{v}/submit"))
    r = ok(moderator.post(f"/moderation/versions/{v}/decision", {"decision": "approve", "comment": "ok"}))
    assert r["state"] == "published"  # price set + automatic release (default): in the store right after approval
    assert r["release_blockers"] == []
    return {"app_id": a["id"], "slug": slug, "version_id": v, "job": job}


def mock_pay(buyer: Client, checkout_url: str, outcome="success"):
    ref = checkout_url.rsplit("/", 1)[-1]
    return ok(buyer.post(f"/mock-checkout/{ref}/complete", {"outcome": outcome}))
