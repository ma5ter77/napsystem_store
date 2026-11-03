"""Conversion job state machine with leases, idempotency, transactional cancel/complete and retries.

queued -> validating -> decoding -> rendering -> quality_check -> packaging -> completed | failed | canceled
"""
from __future__ import annotations

import logging
import os
import shutil
import socket
import time
import uuid
from datetime import timedelta

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import session_scope, utcnow
from ..dsp.media import MediaError
from ..dsp.importer import STUDIO_LAYOUT, import_render
from ..dsp.modes import LAYOUTS
from ..dsp.pipeline import Cancelled, Limits, convert
from ..dsp.variants import convert_variant
from ..errors import ApiError
from ..models import App, Asset, ConversionJob, DspProfile
from ..models.content import BEAT_METHODS, JOB_LAYOUTS, JOB_TERMINAL
from ..storage import get_storage
from .assets import store_generated
from .common import audit

log = logging.getLogger("nap.jobs")


class LeaseLost(Exception):
    pass


def profile_snapshot(p: DspProfile) -> dict:
    return {"slug": p.slug, "version": p.version, **p.params}


def layout_channels(layout: str) -> list[str]:
    """Channel order of a job layout (headphones = the 2.0 track)."""
    return ["FL", "FR"] if layout == "headphones" else LAYOUTS[layout]


def _own_source(db: Session, owner_id: uuid.UUID, app_id: uuid.UUID, asset_id: uuid.UUID) -> Asset:
    asset = db.get(Asset, asset_id)
    if not asset or asset.owner_id != owner_id or asset.kind != "source" or asset.app_id != app_id:
        raise ApiError("not_found", 404, entity="asset")
    if asset.status != "uploaded":
        raise ApiError("asset_not_ready", 409)
    return asset


def edited_snapshot(base: dict, beat: dict | None, method: str) -> dict:
    """NAP-117: the author's beat editor on top of a profile: beat frequency, a smooth transition and the layer level.
    Validated like a profile; the result is frozen in the job (and so in the manifest)."""
    snap = dict(base)
    beat = {k: v for k, v in (beat or {}).items() if v is not None}
    if beat.get("beat_hz") is not None:
        snap["beat_hz"] = float(beat["beat_hz"])
    if beat.get("to_hz") is not None and float(beat["to_hz"]) != snap["beat_hz"]:
        minutes = float(beat.get("over_minutes") or 0)
        if not (0 < minutes <= 360):
            raise ApiError("validation_failed", 422, details={"field": "beat.over_minutes"})
        snap["beat_curve"] = [[0.0, snap["beat_hz"]], [round(minutes * 60, 3), float(beat["to_hz"])]]
    elif beat.get("beat_hz") is not None:
        snap.pop("beat_curve", None)          # a constant beat chosen in the editor replaces the profile's curve
    if beat.get("layer_dbfs") is not None:
        snap["tone_gain_dbfs"] = float(beat["layer_dbfs"])
    if method in ("layer", "shift_layer") and snap.get("tone_gain_dbfs") is None:
        snap["tone_gain_dbfs"] = -30.0
    from ..dsp.engine import Profile
    try:
        Profile.from_dict(snap)
    except (ValueError, KeyError) as e:
        raise ApiError("validation_failed", 422, details={"field": "beat", "message": str(e)}) from None
    if beat:
        snap["edited"] = {k: beat[k] for k in ("beat_hz", "to_hz", "over_minutes", "layer_dbfs") if k in beat}
    return snap


def create_job(db: Session, owner_id: uuid.UUID, app_id: uuid.UUID, source_asset_id: uuid.UUID,
               profile_id: uuid.UUID, idempotency_key: str | None, layout: str = "headphones",
               stems: dict[str, uuid.UUID] | None = None, method: str = "layer",
               beat: dict | None = None) -> tuple[ConversionJob, bool]:
    # NAP-117: layer = carrier layer only (MVP, headphones only); shift = the beat from the track; shift_layer = both
    if method not in BEAT_METHODS or (method == "layer" and layout != "headphones"):
        raise ApiError("validation_failed", 422, details={"field": "method"})
    # NAP-108: speakers 3.0 / NAP chair 5.1 and 7.1, each channel optionally from the author's own file
    if layout not in JOB_LAYOUTS:
        raise ApiError("validation_failed", 422, details={"field": "layout"})
    stems = {k: v for k, v in (stems or {}).items() if v}
    if stems and layout == "headphones":
        raise ApiError("validation_failed", 422, details={"field": "stems"})
    bad = sorted(set(stems) - set(layout_channels(layout)))
    if bad:
        raise ApiError("validation_failed", 422, details={"field": f"stems.{bad[0]}"})
    stems_s = {k: str(v) for k, v in sorted(stems.items())}
    if idempotency_key:
        existing = db.scalar(select(ConversionJob).where(ConversionJob.owner_id == owner_id,
                                                         ConversionJob.idempotency_key == idempotency_key))
        if existing:
            if existing.source_asset_id != source_asset_id or existing.profile_id != profile_id \
                    or existing.layout != layout or (existing.stems or {}) != stems_s or existing.method != method:
                raise ApiError("idempotency_conflict", 409)
            return existing, False
    app = db.get(App, app_id)
    if not app or app.developer_id != owner_id:
        raise ApiError("not_found", 404, entity="app")
    _own_source(db, owner_id, app_id, source_asset_id)
    for ch, aid in stems.items():
        _own_source(db, owner_id, app_id, aid)
    prof = db.get(DspProfile, profile_id)
    if not prof or prof.status != "active":
        raise ApiError("profile_unavailable", 422)
    job = ConversionJob(owner_id=owner_id, app_id=app_id, source_asset_id=source_asset_id, profile_id=prof.id,
                        profile_snapshot=edited_snapshot(profile_snapshot(prof), beat, method),
                        idempotency_key=idempotency_key, max_attempts=get_settings().job_max_attempts,
                        layout=layout, stems=stems_s, method=method)
    db.add(job)
    try:
        db.flush()
    except IntegrityError:  # concurrent request with the same key
        db.rollback()
        existing = db.scalar(select(ConversionJob).where(ConversionJob.owner_id == owner_id,
                                                         ConversionJob.idempotency_key == idempotency_key))
        return existing, False
    audit(db, owner_id, "conversion.created", "conversion_job", job.id,
          {"profile": prof.slug, "layout": layout, "own_files": sorted(stems_s), "method": method})
    return job, True


def studio_info(job: ConversionJob) -> dict | None:
    return (job.profile_snapshot or {}).get("studio") if (job.profile_snapshot or {}).get("slug") == "studio" else None


def create_studio_import(db: Session, owner_id: uuid.UUID, render_job_id: uuid.UUID, app_id: uuid.UUID,
                         idempotency_key: str | None) -> tuple[ConversionJob, bool]:
    """NAP-110: a finished studio export joins an app as it is: a stereo export is the headphones track, a 3.0 /
    5.1 / 7.1 export is the speaker or chair version of the session made from the same project."""
    from ..models import AudioProject, AudioProjectRevision, RenderArtifact, RenderJob, RoutingProfile
    if idempotency_key:
        existing = db.scalar(select(ConversionJob).where(ConversionJob.owner_id == owner_id,
                                                         ConversionJob.idempotency_key == idempotency_key))
        if existing:
            if (studio_info(existing) or {}).get("render_job_id") != str(render_job_id) or existing.app_id != app_id:
                raise ApiError("idempotency_conflict", 409)
            return existing, False
    app = db.get(App, app_id)
    if not app or app.developer_id != owner_id:
        raise ApiError("not_found", 404, entity="app")
    rj = db.get(RenderJob, render_job_id)
    if not rj or rj.owner_id != owner_id:
        raise ApiError("not_found", 404, entity="render_job")
    art = db.scalar(select(RenderArtifact).where(RenderArtifact.render_job_id == rj.id))
    if rj.state != "completed" or art is None:
        raise ApiError("not_ready", 409)
    mode = STUDIO_LAYOUT.get(art.channel_layout)
    if mode is None:
        raise ApiError("layout_not_supported", 422, details={"layout": art.channel_layout})
    layout = "headphones" if mode == "stereo_2_0" else mode
    project = db.get(AudioProject, rj.project_id)
    rev = db.get(AudioProjectRevision, rj.revision_id)
    prof = db.scalar(select(RoutingProfile).where(RoutingProfile.key == rj.profile_key,
                                                  RoutingProfile.version == rj.profile_version))
    studio = {"project_id": str(project.id), "project_title": project.title, "revision": rev.number,
              "render_job_id": str(rj.id), "routing_profile": rj.profile_key, "routing_profile_version": rj.profile_version,
              "routing_profile_name": prof.name if prof else rj.profile_key, "layout": art.channel_layout,
              "export_sha256": art.sha256, "export_verified": bool((art.report or {}).get("verification", {}).get("passed")),
              "label": f"{project.title} · {prof.name if prof else rj.profile_key} · r{rev.number}"}
    job = ConversionJob(owner_id=owner_id, app_id=app_id, source_asset_id=art.asset_id, profile_id=None,
                        profile_snapshot={"slug": "studio", "version": rev.number, "studio": studio},
                        idempotency_key=idempotency_key, max_attempts=get_settings().job_max_attempts, layout=layout)
    db.add(job)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return db.scalar(select(ConversionJob).where(ConversionJob.owner_id == owner_id,
                                                     ConversionJob.idempotency_key == idempotency_key)), False
    audit(db, owner_id, "conversion.studio_import", "conversion_job", job.id,
          {"render_job_id": str(rj.id), "layout": layout, "app_id": str(app_id)})
    return job, True


def cancel_job(db: Session, job_id: uuid.UUID, owner_id: uuid.UUID) -> ConversionJob:
    job = db.get(ConversionJob, job_id, with_for_update=True)
    if not job or job.owner_id != owner_id:
        raise ApiError("not_found", 404, entity="conversion")
    if job.state in JOB_TERMINAL:
        return job  # exactly one terminal state: completed jobs cannot be canceled afterwards
    job.state = "canceled"
    job.cancel_requested = True
    job.finished_at = utcnow()
    job.lease_owner = None
    audit(db, owner_id, "conversion.canceled", "conversion_job", job.id)
    return job


def retry_job(db: Session, job_id: uuid.UUID, owner_id: uuid.UUID) -> ConversionJob:
    old = db.get(ConversionJob, job_id)
    if not old or old.owner_id != owner_id:
        raise ApiError("not_found", 404, entity="conversion")
    if old.state not in ("failed", "canceled"):
        raise ApiError("invalid_state", 409)
    prof = db.get(DspProfile, old.profile_id) if old.profile_id else None
    job = ConversionJob(owner_id=owner_id, app_id=old.app_id, source_asset_id=old.source_asset_id,
                        profile_id=old.profile_id, profile_snapshot=old.profile_snapshot if prof is None
                        else old.profile_snapshot, retry_of_id=old.id, max_attempts=old.max_attempts,
                        layout=old.layout, stems=dict(old.stems or {}), method=old.method)   # the retry keeps them
    db.add(job)
    db.flush()
    audit(db, owner_id, "conversion.retried", "conversion_job", job.id, {"retry_of": str(old.id)})
    return job


# ------------------------------------------------------------------ worker side

def claim(job_id: uuid.UUID, worker: str) -> bool:
    lease = get_settings().job_lease_seconds
    with session_scope() as db:
        res = db.execute(text("""
            UPDATE conversion_jobs SET state='validating', stage_progress=0, attempt=attempt+1,
                   lease_owner=:w, lease_expires_at=now() + make_interval(secs => :lease),
                   started_at=COALESCE(started_at, now()), updated_at=now(), error_code=NULL
             WHERE id=:id AND cancel_requested = false AND (
                   state='queued' OR (state NOT IN ('completed','failed','canceled') AND lease_expires_at < now()))
               AND attempt < max_attempts
            RETURNING id"""), {"w": worker, "lease": lease, "id": job_id})
        return res.scalar() is not None


def _touch(job_id: uuid.UUID, worker: str, stage: str, progress: float) -> None:
    lease = get_settings().job_lease_seconds
    with session_scope() as db:
        res = db.execute(update(ConversionJob).where(
            ConversionJob.id == job_id, ConversionJob.lease_owner == worker,
            ConversionJob.state.notin_(JOB_TERMINAL), ConversionJob.cancel_requested.is_(False)).values(
            state=stage, stage_progress=round(progress, 4), updated_at=utcnow(),
            lease_expires_at=utcnow() + timedelta(seconds=lease)))
        if res.rowcount == 0:
            job = db.get(ConversionJob, job_id)
            if job is None or job.state == "canceled" or job.cancel_requested:
                raise Cancelled()
            raise LeaseLost()


def _fail(job_id: uuid.UUID, worker: str, code: str, detail: str, retryable: bool) -> str:
    with session_scope() as db:
        job = db.get(ConversionJob, job_id, with_for_update=True)
        if job is None or job.state in JOB_TERMINAL or job.lease_owner != worker:
            return job.state if job else "missing"
        if retryable and job.attempt < job.max_attempts:
            job.state = "queued"
            job.lease_owner = None
            job.lease_expires_at = None
            job.error_code = code
            job.error_detail = detail[:2000]
            return "queued"
        job.failed_stage = job.state if job.state not in ("queued",) else None   # the stage it was in
        job.state = "failed"
        job.error_code = code
        job.error_detail = detail[:2000]
        job.finished_at = utcnow()
        job.lease_owner = None
        return "failed"


def _complete(job_id: uuid.UUID, worker: str, out) -> str:
    """Upload all outputs, then open the full result set atomically in one transaction."""
    with session_scope() as db:
        job = db.get(ConversionJob, job_id)
        if job is None or job.state in JOB_TERMINAL:
            return job.state if job else "missing"
        owner, app_id = job.owner_id, job.app_id
    staged: list[Asset] = []
    with session_scope() as db:
        o = out.params["output"]
        files = [("master", out.master, "wav", o["master_sha256"]),
                 ("delivery", out.delivery, "flac", o["delivery_sha256"]),
                 ("preview", out.preview, "flac", o["preview_sha256"]),
                 ("layer", out.layer, "wav", None),
                 ("qa_report", out.report_json, "json", None),
                 ("params", out.params_json, "json", None)]
        from .assets import _ctype  # noqa
        from ..dsp.pipeline import sha256_file
        for kind, path, ext, sha in files:
            if not path:      # layout jobs have no separate layer file
                continue
            staged.append(store_generated(db, owner_id=owner, app_id=app_id, kind=kind, path=path, ext=ext,
                                          sha256=sha or sha256_file(path)))
        db.flush()
        job = db.get(ConversionJob, job_id, with_for_update=True)
        if job.state in JOB_TERMINAL or job.lease_owner != worker or job.cancel_requested:
            db.rollback()
            for a in staged:
                get_storage().delete(a.storage_key)
            return job.state
        dur = o["duration_seconds"]
        job.result = {a.kind: str(a.id) for a in staged} | {"duration_seconds": dur, "frames": o["frames"],
                                                             "delivery_sha256": o["delivery_sha256"],
                                                             "pcm_sha256": o["pcm_sha256"],
                                                             "layout": job.layout, "channels": o["channels"],
                                                             "channel_order": o.get("channel_order") or ["FL", "FR"]}
        job.report = {"qa": out.report, "params": out.params}
        job.state = "completed"
        job.stage_progress = 1.0
        job.finished_at = utcnow()
        job.lease_owner = None
        audit(db, owner, "conversion.completed", "conversion_job", job.id, {"frames": o["frames"]})
        from .apps import attach_layout, autofill_program  # local import: apps <-> conversion
        if job.layout == "headphones":
            autofill_program(db, job)
        else:
            attach_layout(db, job)
        return "completed"


def run_job(job_id: uuid.UUID | str) -> str:
    job_id = uuid.UUID(str(job_id))
    s = get_settings()
    worker = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
    if not claim(job_id, worker):
        return "not_claimed"
    with session_scope() as db:
        job = db.get(ConversionJob, job_id)
        snapshot = dict(job.profile_snapshot)
        src_asset = db.get(Asset, job.source_asset_id)
        src_key, attempt = src_asset.storage_key, job.attempt
        layout = job.layout
        method = job.method
        studio = studio_info(job)
        stem_keys = {ch: db.get(Asset, uuid.UUID(aid)).storage_key for ch, aid in (job.stems or {}).items()}
    workdir = os.path.join(s.job_workdir, str(job_id), f"attempt-{attempt}")
    shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir, exist_ok=True)
    last = [0.0]

    def progress(stage: str, frac: float) -> None:
        now = time.monotonic()
        if frac in (0.0, 1.0) or now - last[0] > 0.5:
            last[0] = now
            _touch(job_id, worker, stage, frac)

    def check() -> None:
        now = time.monotonic()
        if now - last[0] > 0.5:
            last[0] = now
            with session_scope() as db:
                j = db.get(ConversionJob, job_id)
                if j.state == "canceled" or j.cancel_requested:
                    raise Cancelled()
                if j.lease_owner != worker:
                    raise LeaseLost()

    try:
        src_path = os.path.join(workdir, "source.bin")
        get_storage().get_to_file(src_key, src_path)
        limits = Limits(max_bytes=s.max_upload_bytes, max_seconds=s.max_duration_seconds,
                        cpu_seconds=s.job_cpu_seconds, memory_bytes=s.job_memory_bytes,
                        disk_reserve=s.job_disk_reserve_bytes, timeout=s.job_timeout_seconds,
                        preview_seconds=s.preview_seconds)
        if studio is not None:   # NAP-110: the studio mix is final, imported as it is
            out = import_render(src_path, workdir, limits, "stereo_2_0" if layout == "headphones" else layout,
                                studio, progress, check)
        elif layout == "headphones" and method == "layer":
            out = convert(src_path, workdir, snapshot, limits, progress, check)
        elif layout == "headphones":   # NAP-117: the beat from the track itself (+ the layer when chosen)
            out = convert_variant(src_path, workdir, snapshot, limits, "stereo_2_0", None, progress, check,
                                  add_layer=method == "shift_layer")
        else:
            stem_paths = {}
            for ch, key in stem_keys.items():
                stem_paths[ch] = os.path.join(workdir, f"stem-{ch}.bin")
                get_storage().get_to_file(key, stem_paths[ch])
            out = convert_variant(src_path, workdir, snapshot, limits, layout, stem_paths, progress, check,
                                  add_layer=method == "shift_layer")
        return _complete(job_id, worker, out)
    except Cancelled:
        return "canceled"
    except LeaseLost:
        return "lease_lost"
    except MediaError as e:
        log.info("job failed", extra={"job_id": str(job_id), "code": e.code})
        return _fail(job_id, worker, e.code, e.detail, e.retryable)
    except Exception as e:  # unexpected/transient: bounded retry
        log.exception("job crashed", extra={"job_id": str(job_id)})
        return _fail(job_id, worker, "internal_error", repr(e), True)
    finally:
        shutil.rmtree(os.path.join(s.job_workdir, str(job_id)), ignore_errors=True)


def stale_queued_jobs(older_than_seconds: int = 120) -> list[str]:
    """Jobs still queued after a while (e.g. the broker was down when they were created): dispatch them again.
    Claiming is atomic in PostgreSQL, so a duplicate dispatch cannot run a job twice."""
    with session_scope() as db:
        rows = db.scalars(select(ConversionJob.id).where(
            ConversionJob.state == "queued", ConversionJob.lease_owner.is_(None),
            ConversionJob.created_at < utcnow() - timedelta(seconds=older_than_seconds))).all()
    return [str(x) for x in rows]


def recover_expired_leases() -> list[str]:
    """Worker crash recovery: re-queue (or fail) jobs whose lease expired."""
    out = []
    with session_scope() as db:
        rows = db.scalars(select(ConversionJob).where(ConversionJob.state.notin_(JOB_TERMINAL + ("queued",)),
                                                      ConversionJob.lease_expires_at < utcnow()).with_for_update(
            skip_locked=True)).all()
        for j in rows:
            if j.attempt < j.max_attempts:
                j.state, j.lease_owner = "queued", None
                j.error_code = "worker_lost"
            else:
                j.failed_stage = j.state if j.state != "queued" else None
                j.state, j.error_code, j.finished_at, j.lease_owner = "failed", "worker_lost", utcnow(), None
            out.append(str(j.id))
    return out
