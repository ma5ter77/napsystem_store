"""Criterion 7: cancel, worker crash and retry never publish partial results or duplicate revisions."""
import threading
import time
import uuid
from datetime import timedelta

from sqlalchemy import func, select

from app.db import session_scope, utcnow
from app.models import Asset, ConversionJob
from app.services import conversion
from app.worker import wait_inline_jobs

from .conftest import Client, ok
from .helpers import profile_id, upload, wav_bytes


def _setup(seconds=2.0):
    dev = Client().register(developer=True)
    a = ok(dev.post("/developer/apps", {"slug": "job-" + uuid.uuid4().hex[:8], "author_name": "A",
                                        "metadata": {"ru": {"title": "Задания"}}}), 201)
    up = upload(dev, a["id"], wav_bytes(seconds))
    return dev, a, up["ticket"]["asset_id"]


def _outputs(app_id):
    with session_scope() as db:
        return db.scalar(select(func.count()).select_from(Asset).where(
            Asset.app_id == uuid.UUID(app_id), Asset.kind.in_(("master", "delivery", "preview"))))


def test_idempotent_create_returns_same_job():
    dev, a, asset = _setup(0.5)
    body = {"app_id": a["id"], "source_asset_id": asset, "profile_id": profile_id(dev)}
    j1 = ok(dev.post("/developer/conversions", body, headers={"Idempotency-Key": "same-key"}), 202)
    j2 = ok(dev.post("/developer/conversions", body, headers={"Idempotency-Key": "same-key"}), 202)
    wait_inline_jobs()
    assert j1["id"] == j2["id"] and j1["created"] and not j2["created"]
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(ConversionJob).where(
            ConversionJob.app_id == uuid.UUID(a["id"]))) == 1
    other = dict(body, profile_id=profile_id(dev, "theta-demo-v1"))
    assert dev.post("/developer/conversions", other, headers={"Idempotency-Key": "same-key"}).status_code == 409


def test_cancel_during_processing_leaves_no_result(monkeypatch):
    dev, a, asset = _setup(20.0)
    import app.dsp.pipeline as pl
    real = pl._iter_decoded

    def slow(*args, **kw):
        for blk in real(*args, **kw):
            time.sleep(0.05)
            yield blk
    monkeypatch.setattr(pl, "_iter_decoded", slow)
    j = ok(dev.post("/developer/conversions", {"app_id": a["id"], "source_asset_id": asset,
                                               "profile_id": profile_id(dev)}), 202)
    for _ in range(200):
        st = ok(dev.get(f"/developer/conversions/{j['id']}"))["state"]
        if st in ("rendering", "quality_check", "packaging"):
            break
        time.sleep(0.05)
    c = ok(dev.post(f"/developer/conversions/{j['id']}/cancel"))
    assert c["state"] == "canceled"
    wait_inline_jobs()
    final = ok(dev.get(f"/developer/conversions/{j['id']}"))
    assert final["state"] == "canceled" and final["result"] is None
    assert _outputs(a["id"]) == 0
    # canceling again is a no-op; retry starts from scratch as a new job
    assert ok(dev.post(f"/developer/conversions/{j['id']}/cancel"))["state"] == "canceled"
    monkeypatch.setattr(pl, "_iter_decoded", real)
    r = ok(dev.post(f"/developer/conversions/{j['id']}/retry"), 202)
    wait_inline_jobs()
    done = ok(dev.get(f"/developer/conversions/{r['id']}"))
    assert done["state"] == "completed" and done["retry_of"] == j["id"]
    assert _outputs(a["id"]) == 3


def test_cancel_vs_complete_race_has_exactly_one_terminal_state():
    dev, a, asset = _setup(1.0)
    with session_scope() as db:
        job, _ = conversion.create_job(db, uuid.UUID(dev.user_id), uuid.UUID(a["id"]), uuid.UUID(asset),
                                       uuid.UUID(profile_id(dev)), None)
        jid = job.id
    states = []
    t = threading.Thread(target=lambda: states.append(conversion.run_job(jid)))
    t.start()
    time.sleep(0.3)
    with session_scope() as db:
        conversion.cancel_job(db, jid, uuid.UUID(dev.user_id))
    t.join()
    with session_scope() as db:
        j = db.get(ConversionJob, jid)
        assert j.state in ("completed", "canceled")
        assert (j.state == "completed") == (j.result is not None)
        n = db.scalar(select(func.count()).select_from(Asset).where(Asset.app_id == uuid.UUID(a["id"]),
                                                                    Asset.kind == "master"))
        assert n == (1 if j.state == "completed" else 0)


def test_worker_crash_lease_expiry_then_single_completion():
    dev, a, asset = _setup(1.0)
    with session_scope() as db:
        job, _ = conversion.create_job(db, uuid.UUID(dev.user_id), uuid.UUID(a["id"]), uuid.UUID(asset),
                                       uuid.UUID(profile_id(dev)), None)
        jid = job.id
    # a worker claims the job and "crashes" (never reports back)
    assert conversion.claim(jid, "dead-worker")
    assert not conversion.claim(jid, "second-worker")  # lease held
    with session_scope() as db:
        db.get(ConversionJob, jid).lease_expires_at = utcnow() - timedelta(seconds=1)
    assert [str(jid)] == conversion.recover_expired_leases()
    assert conversion.run_job(jid) == "completed"
    # the dead worker coming back cannot complete or fail the job
    assert conversion._fail(jid, "dead-worker", "x", "", False) == "completed"
    with session_scope() as db:
        j = db.get(ConversionJob, jid)
        assert j.attempt == 2 and j.state == "completed"
        assert db.scalar(select(func.count()).select_from(Asset).where(Asset.app_id == uuid.UUID(a["id"]),
                                                                       Asset.kind == "master")) == 1


def test_corrupt_file_is_not_retried_automatically():
    dev = Client().register(developer=True)
    a = ok(dev.post("/developer/apps", {"slug": "bad-" + uuid.uuid4().hex[:8], "author_name": "A",
                                        "metadata": {"ru": {"title": "Порча"}}}), 201)
    good = wav_bytes(2.0)
    bad = good[: len(good) // 2]  # header announces 2 s, data contains 1 s
    up = upload(dev, a["id"], bad)
    assert up["response"].status_code == 200
    j = ok(dev.post("/developer/conversions", {"app_id": a["id"], "source_asset_id": up["ticket"]["asset_id"],
                                               "profile_id": profile_id(dev)}), 202)
    wait_inline_jobs()
    res = ok(dev.get(f"/developer/conversions/{j['id']}"))
    assert res["state"] == "failed" and res["attempt"] == 1 and res["error_code"] in ("truncated_audio", "decode_failed")


def test_broker_down_job_stays_queued_and_is_redispatched(monkeypatch):
    """A conversion request must not fail with 500 when the queue broker is down: the job stays queued in
    PostgreSQL and the periodic recovery dispatches it again."""
    from datetime import timedelta
    import uuid as _uuid
    from app import worker
    from app.config import get_settings
    from app.db import session_scope, utcnow
    from app.models import ConversionJob
    from app.services.conversion import stale_queued_jobs
    from .conftest import Client, ok
    from .helpers import profile_id, upload, wav_bytes

    dev = Client().register(developer=True)
    a = ok(dev.post("/developer/apps", {"slug": "bk-" + dev.user_id[:8], "author_name": "A",
                                        "metadata": {"ru": {"title": "Брокер"}}}), 201)
    up = upload(dev, a["id"], wav_bytes(0.5))
    monkeypatch.setattr(get_settings(), "run_jobs_inline", False)

    def down(*a, **k):
        raise ConnectionError("redis down")
    monkeypatch.setattr(worker.run_conversion, "delay", down)
    j = ok(dev.post("/developer/conversions", {"app_id": a["id"], "source_asset_id": up["ticket"]["asset_id"],
                                               "profile_id": profile_id(dev)}), 202)
    assert j["state"] == "queued"
    with session_scope() as db:
        db.get(ConversionJob, _uuid.UUID(j["id"])).created_at = utcnow() - timedelta(minutes=5)
    assert j["id"] in stale_queued_jobs()
