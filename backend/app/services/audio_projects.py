"""NAP Audio Studio service (spec v2.1 M1): studio sources, projects, revisions, render jobs, previews."""
from __future__ import annotations

import logging
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import uuid
from datetime import timedelta

import numpy as np
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..audio.render import RenderError, decode_source, encode_flac, render, verify, verify_flac
from ..audio.schema import BUS_SETS, PROFILES, ProjectState, state_hash
from ..config import get_settings
from ..db import session_scope, utcnow
from ..errors import ApiError
from ..models import (Asset, AudioProject, AudioProjectRevision, RenderArtifact, RenderJob, RoutingProfile,
                      UploadTicket)
from ..security import sha256_hex
from ..storage import get_storage, new_key
from .assets import _ctype, store_generated
from .common import audit

STUDIO_EXT = {"wav", "flac", "aif", "aiff", "mp3", "m4a"}
MAX_STUDIO_SECONDS = 3 * 3600
PREVIEW_MAX_SECONDS = 60
LEASE = timedelta(minutes=10)

log = logging.getLogger("nap.audio")


# ------------------------------------------------------------------ routing profiles

def ensure_profiles(db: Session) -> list[RoutingProfile]:
    have = {(p.key, p.version) for p in db.scalars(select(RoutingProfile))}
    for key, p in PROFILES.items():
        if (key, 1) not in have:
            db.add(RoutingProfile(key=key, version=1, name=p["name"], layout=p["layout"], channels=p["channels"],
                                  matrix=p["matrix"], note=p["note"], builtin=True))
    db.flush()
    return list(db.scalars(select(RoutingProfile).order_by(RoutingProfile.key, RoutingProfile.version)))


def profile_view(p: RoutingProfile) -> dict:
    return {"key": p.key, "version": p.version, "name": p.name, "layout": p.layout, "channels": p.channels,
            "matrix": p.matrix, "note": p.note, "builtin": p.builtin}


# ------------------------------------------------------------------ studio sources (immutable)

def create_upload(db: Session, owner_id: uuid.UUID, filename: str, size: int, sha256: str | None,
                  rights: dict) -> dict:
    s = get_settings()
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    if ext not in STUDIO_EXT:
        raise ApiError("unsupported_format", 415)
    if size <= 0:
        raise ApiError("empty_file", 422)
    if size > s.max_upload_bytes:
        raise ApiError("file_too_large", 413, max_bytes=s.max_upload_bytes)
    if sha256 is not None and (len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256)):
        raise ApiError("validation_failed", 422, details={"field": "sha256"})
    if not rights.get("source") or not rights.get("license"):
        raise ApiError("validation_failed", 422, details={"field": "rights"})  # spec §2.10: provenance first
    asset = Asset(owner_id=owner_id, app_id=None, kind="studio_source", storage_key=new_key("studio", owner_id, ext),
                  original_name=filename[:255], content_type=_ctype(ext), status="pending",
                  probe={"rights": {k: str(v)[:300] for k, v in rights.items()}})
    db.add(asset)
    db.flush()
    token = secrets.token_urlsafe(32)
    db.add(UploadTicket(asset_id=asset.id, token_hash=sha256_hex(token), expected_size=size, expected_sha256=sha256,
                        expires_at=utcnow() + timedelta(seconds=s.upload_url_ttl_seconds)))
    audit(db, owner_id, "studio.asset_upload_created", "asset", asset.id, {"size": size})
    return {"asset_id": str(asset.id), "upload_url": f"/api/v1/uploads/{token}", "method": "PUT",
            "expires_in": s.upload_url_ttl_seconds}


def finalize_studio_source(asset: Asset, tmp: str) -> None:
    """Called by the upload pipeline: metadata from an independent probe; the bytes are stored unchanged."""
    from ..audio.render import source_meta_from_probe
    from ..dsp.media import MediaError, probe
    try:
        probe(tmp)  # allowed container/codec check
        meta = source_meta_from_probe(tmp)
    except MediaError as e:
        raise ApiError(e.code if e.code in ("unsupported_format", "no_audio_stream") else "unsupported_or_corrupt", 422)
    if not 1 <= meta["channels"] <= 8:
        raise ApiError("too_many_channels", 422)
    if meta["duration"] <= 0 or meta["duration"] > MAX_STUDIO_SECONDS:
        raise ApiError("too_long", 422)
    asset.probe = {**(asset.probe or {}), **meta}
    asset.immutable = True


def asset_view(a: Asset) -> dict:
    p = a.probe or {}
    return {"id": str(a.id), "name": a.original_name, "status": a.status, "sha256": a.sha256, "size_bytes": a.size_bytes,
            "codec": p.get("codec"), "channels": p.get("channels"), "sample_rate": p.get("sample_rate"),
            "bit_depth": p.get("bit_depth"), "channel_layout": p.get("channel_layout"), "duration": p.get("duration"),
            "lossy": p.get("lossy"), "rights": p.get("rights"), "immutable": a.immutable,
            "created_at": a.created_at.isoformat() if a.created_at else None}


def own_source(db: Session, owner_id: uuid.UUID, asset_id) -> Asset:
    try:
        a = db.get(Asset, uuid.UUID(str(asset_id)))
    except ValueError:
        a = None
    if not a or a.owner_id != owner_id or a.kind != "studio_source":
        raise ApiError("not_found", 404, entity="asset")
    return a


def waveform(db: Session, a: Asset, buckets: int = 2048) -> dict:
    """Per-channel min/max peaks (cached on the asset) for the timeline."""
    p = a.probe or {}
    cached = p.get("waveform")
    if cached and cached.get("buckets") == buckets:
        return cached
    if a.status != "uploaded":
        raise ApiError("asset_not_ready", 409)
    ch = int(p.get("channels") or 1)
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "src")
        get_storage().get_to_file(a.storage_key, src)
        raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", src, "-f", "f32le", "-acodec", "pcm_f32le",
                              "pipe:1"], capture_output=True, timeout=600).stdout
    x = np.frombuffer(raw, dtype="<f4")
    x = x[: len(x) - len(x) % ch].reshape(-1, ch) if len(x) else np.zeros((0, ch), dtype="<f4")
    n = len(x)
    edges = np.linspace(0, n, buckets + 1).astype(int)
    peaks = []
    for c in range(ch):
        col = x[:, c]
        mins, maxs = [], []
        for i in range(buckets):
            seg = col[edges[i]:edges[i + 1]]
            mins.append(round(float(seg.min()), 4) if len(seg) else 0.0)
            maxs.append(round(float(seg.max()), 4) if len(seg) else 0.0)
        peaks.append({"min": mins, "max": maxs})
    wf = {"buckets": buckets, "frames": n, "channels": peaks}
    a.probe = {**p, "waveform": wf}
    return wf


# ------------------------------------------------------------------ projects

def default_state(bus_count: int) -> dict:
    return {"tracks": [{"id": f"t{i + 1}", "name": bus, "bus": bus, "mute": False, "solo": False, "clips": []}
                       for i, bus in enumerate(BUS_SETS[bus_count])], "generators": [], "output": {"on_clip": "fail"}}


def create_project(db: Session, owner_id: uuid.UUID, title: str, sample_rate: int, bit_depth: int,
                   bus_count: int) -> AudioProject:
    if sample_rate not in (44_100, 48_000, 88_200, 96_000):
        raise ApiError("validation_failed", 422, details={"field": "sample_rate"})
    if bit_depth not in (16, 24, 32):
        raise ApiError("validation_failed", 422, details={"field": "bit_depth"})
    if bus_count not in BUS_SETS:
        raise ApiError("validation_failed", 422, details={"field": "bus_count"})
    pr = AudioProject(owner_id=owner_id, title=title.strip()[:160] or "Untitled", sample_rate=sample_rate,
                      bit_depth=bit_depth, bus_count=bus_count, state=default_state(bus_count), revision=1,
                      updated_at=utcnow())
    db.add(pr)
    db.flush()
    audit(db, owner_id, "studio.project_created", "audio_project", pr.id, {"rate": sample_rate, "buses": bus_count})
    return pr


def own_project(db: Session, owner_id: uuid.UUID, project_id, lock: bool = False) -> AudioProject:
    try:
        pr = db.get(AudioProject, uuid.UUID(str(project_id)), with_for_update=lock)
    except ValueError:
        pr = None
    if not pr or pr.owner_id != owner_id:
        raise ApiError("not_found", 404, entity="project")
    return pr


def validate_state(db: Session, owner_id: uuid.UUID, state: dict) -> ProjectState:
    try:
        st = ProjectState.model_validate(state)
    except ValidationError as e:
        raise ApiError("invalid_project", 422, details={"errors": [
            {"path": ".".join(str(x) for x in err["loc"]), "message": err["msg"]} for err in e.errors()[:20]]})
    for t in st.tracks:
        for c in t.clips:
            if c.asset_id:
                a = own_source(db, owner_id, c.asset_id)
                if a.status != "uploaded":
                    raise ApiError("asset_not_ready", 409)
                if c.source_channel >= int((a.probe or {}).get("channels") or 1):
                    raise ApiError("invalid_project", 422, details={"errors": [
                        {"path": f"clip {c.id}", "message": "source channel does not exist in the file"}]})
    return st


def update_project(db: Session, pr: AudioProject, actor_id: uuid.UUID, base_revision: int, state: dict | None,
                   title: str | None) -> AudioProject:
    if base_revision != pr.revision:
        raise ApiError("revision_conflict", 409, current=pr.revision)
    if state is not None:
        st = validate_state(db, actor_id, state)
        pr.state = st.model_dump(mode="json")
        pr.duration_frames = st.duration_frames()
    if title is not None:
        pr.title = title.strip()[:160] or pr.title
    pr.revision += 1
    pr.updated_at = utcnow()
    return pr


def snapshot(db: Session, pr: AudioProject, actor_id: uuid.UUID, note: str = "") -> AudioProjectRevision:
    """Immutable revision of the current state; reuses the newest one when nothing changed."""
    h = state_hash(pr.state)
    last = db.scalar(select(AudioProjectRevision).where(AudioProjectRevision.project_id == pr.id)
                     .order_by(AudioProjectRevision.number.desc()))
    if last and last.state_sha256 == h and last.sample_rate == pr.sample_rate and last.bit_depth == pr.bit_depth:
        return last
    rev = AudioProjectRevision(project_id=pr.id, number=(last.number + 1) if last else 1, state=pr.state,
                               state_sha256=h, sample_rate=pr.sample_rate, bit_depth=pr.bit_depth, note=note[:200],
                               created_by=actor_id)
    db.add(rev)
    db.flush()
    audit(db, actor_id, "studio.revision", "audio_project", pr.id, {"number": rev.number, "sha256": h})
    return rev


def project_view(pr: AudioProject, full: bool = True) -> dict:
    out = {"id": str(pr.id), "title": pr.title, "sample_rate": pr.sample_rate, "bit_depth": pr.bit_depth,
           "bus_count": pr.bus_count, "revision": pr.revision, "duration_frames": pr.duration_frames,
           "status": pr.status, "updated_at": pr.updated_at.isoformat() if pr.updated_at else None}
    if full:
        out["state"] = pr.state
    return out


# ------------------------------------------------------------------ renders

EXPORT_FORMATS = ("wav", "flac")


def start_render(db: Session, pr: AudioProject, actor_id: uuid.UUID, profile_key: str, idem: str | None,
                 fmt: str = "wav") -> RenderJob:
    if idem:
        ex = db.scalar(select(RenderJob).where(RenderJob.owner_id == actor_id, RenderJob.idempotency_key == idem))
        if ex:
            return ex
    profiles = {p.key: p for p in ensure_profiles(db) if p.builtin}
    if profile_key not in profiles:
        raise ApiError("validation_failed", 422, details={"field": "profile"})
    if fmt not in EXPORT_FORMATS:
        raise ApiError("validation_failed", 422, details={"field": "format"})
    if fmt == "flac" and pr.bit_depth not in (16, 24):
        raise ApiError("flac_needs_integer", 422)
    st = validate_state(db, actor_id, pr.state)
    if st.duration_frames() <= 0:
        raise ApiError("empty_project", 422)
    rev = snapshot(db, pr, actor_id, note=f"render {profile_key}")
    job = RenderJob(owner_id=actor_id, project_id=pr.id, revision_id=rev.id, profile_key=profile_key,
                    profile_version=profiles[profile_key].version, idempotency_key=idem, format=fmt,
                    max_attempts=get_settings().job_max_attempts)
    db.add(job)
    db.flush()
    audit(db, actor_id, "studio.render_started", "render_job", job.id, {"profile": profile_key, "revision": rev.number,
                                                                          "format": fmt})
    return job


def job_view(db: Session, j: RenderJob) -> dict:
    art = db.scalar(select(RenderArtifact).where(RenderArtifact.render_job_id == j.id))
    rev = db.get(AudioProjectRevision, j.revision_id)
    out = {"id": str(j.id), "project_id": str(j.project_id), "revision": rev.number if rev else None,
           "profile": j.profile_key, "profile_version": j.profile_version, "format": j.format, "state": j.state,
           "progress": round(j.progress, 3), "attempt": j.attempt, "error_code": j.error_code,
           "error_detail": j.error_detail, "logs": j.logs[-20:], "created_at": j.created_at.isoformat(),
           "finished_at": j.finished_at.isoformat() if j.finished_at else None, "artifact": None}
    if art:
        out["artifact"] = {"id": str(art.id), "sha256": art.sha256, "format": art.format, "layout": art.channel_layout,
                           "channel_order": art.channel_order, "sample_rate": art.sample_rate,
                           "bit_depth": art.bit_depth, "frames": art.frames, "size_bytes": art.size_bytes,
                           "report": art.report}
    return out


def _claim(job_id: uuid.UUID, worker: str) -> bool:
    with session_scope() as db:
        now = utcnow()
        r = db.execute(update(RenderJob).where(
            RenderJob.id == job_id, RenderJob.state.in_(("queued",)),
            (RenderJob.lease_expires_at.is_(None)) | (RenderJob.lease_expires_at < now))
            .values(lease_owner=worker, lease_expires_at=now + LEASE, state="decoding",
                    attempt=RenderJob.attempt + 1, started_at=now))
        return r.rowcount == 1


def recover_render_leases(stale_after: timedelta = timedelta(minutes=5)) -> list[str]:
    """Jobs whose worker died (lease expired mid-render) go back to the queue, or fail once attempts run out;
    queued jobs nobody picked up (lost queue message) are returned for re-dispatch."""
    out = []
    with session_scope() as db:
        now = utcnow()
        dead = db.scalars(select(RenderJob).where(RenderJob.state.in_(("decoding", "rendering", "verifying")),
                                                  RenderJob.lease_expires_at < now).with_for_update(skip_locked=True)).all()
        for j in dead:
            final = j.attempt >= j.max_attempts or j.cancel_requested
            j.state = ("canceled" if j.cancel_requested else "failed") if final else "queued"
            j.lease_owner, j.lease_expires_at = None, None
            if final:
                j.error_code, j.error_detail, j.finished_at = ("canceled" if j.cancel_requested else "lease_expired"), {
                    "message": "the render worker stopped responding"}, now
            _log(db, j, "worker lease expired: " + ("giving up" if final else "re-queued"))
            if not final:
                out.append(str(j.id))
        stale = db.scalars(select(RenderJob.id).where(RenderJob.state == "queued", RenderJob.lease_owner.is_(None),
                                                      RenderJob.created_at < now - stale_after)).all()
        out += [str(i) for i in stale]
        db.commit()
    return list(dict.fromkeys(out))


def _log(db: Session, job: RenderJob, msg: str) -> None:
    job.logs = [*job.logs, {"at": utcnow().isoformat(), "msg": msg[:300]}][-100:]


def run_render(job_id) -> str:
    job_id = uuid.UUID(str(job_id))
    worker = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
    if not _claim(job_id, worker):
        return "not_claimed"
    s = get_settings()
    with session_scope() as db:
        job = db.get(RenderJob, job_id)
        rev = db.get(AudioProjectRevision, job.revision_id)
        state, fs, bits, profile, fmt = rev.state, rev.sample_rate, rev.bit_depth, job.profile_key, job.format
        ids = ProjectState.model_validate(state).asset_ids()
        keys = {i: db.get(Asset, uuid.UUID(i)).storage_key for i in ids}
        owner, attempt = job.owner_id, job.attempt
    workdir = os.path.join(s.job_workdir, "render", str(job_id), f"attempt-{attempt}")
    cache = os.path.join(s.job_workdir, "studio-cache")
    shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir, exist_ok=True)

    def set_state(st: str, progress: float | None = None, msg: str | None = None):
        with session_scope() as db:
            j = db.get(RenderJob, job_id)
            if j.cancel_requested:
                raise RenderError("canceled")
            j.state = st
            if progress is not None:
                j.progress = progress
            j.lease_expires_at = utcnow() + LEASE
            if msg:
                _log(db, j, msg)
            db.commit()

    try:
        sources = {}
        for i, key in keys.items():
            local = os.path.join(workdir, f"src-{i}")
            get_storage().get_to_file(key, local)
            sources[i] = decode_source(local, cache, i, fs)
            os.remove(local)
        set_state("rendering", 0.0, f"decoded {len(sources)} source(s) at {fs} Hz")
        out = os.path.join(workdir, "render.wav")
        last = [0.0]

        def progress(f):
            if f - last[0] >= 0.05 or f >= 1:
                last[0] = f
                set_state("rendering", round(f * 0.9, 3))
        res = render(state, fs=fs, bits=bits, profile_key=profile, sources=sources, out_path=out, progress=progress)
        set_state("verifying", 0.92, "independent check of the written file")
        ver = verify(out, res)
        if not ver["passed"]:
            raise RenderError("verification_failed", "the file read back differs from the render", ver)
        res.report["verification"] = ver
        final, ext, digest = out, "wav", ver["sha256"]
        if fmt == "flac":
            set_state("verifying", 0.95, "lossless FLAC from the verified WAV")
            final, ext = os.path.join(workdir, "render.flac"), "flac"
            encode_flac(out, final, bits)
            fv = verify_flac(final, out, ver, res)
            if not fv["passed"]:
                raise RenderError("verification_failed", "the FLAC does not decode to the verified samples", fv)
            res.report["flac"] = fv
            res.report["verification"] = {**ver, "container": "FLAC", "sha256": fv["sha256"], "passed": True,
                                          "samples_identical_to_wav": True, "wav_sha256": ver["sha256"]}
            digest = fv["sha256"]
            os.remove(out)
        with session_scope() as db:
            job = db.get(RenderJob, job_id)
            a = store_generated(db, owner_id=owner, app_id=None, kind="studio_render", path=final, ext=ext,
                                sha256=digest)
            a.probe = {"render_job_id": str(job_id), "layout": res.report["layout"]}
            db.flush()
            db.add(RenderArtifact(render_job_id=job_id, asset_id=a.id, sha256=digest, format=ext,
                                  channel_layout=res.report["layout"], channel_order=res.report["channel_order"],
                                  sample_rate=fs, bit_depth=bits, frames=res.frames, size_bytes=os.path.getsize(final),
                                  report=res.report))
            job.state, job.progress, job.finished_at, job.lease_owner = "completed", 1.0, utcnow(), None
            _log(db, job, f"completed: {ext} {res.report['container'] if ext == 'wav' else ''} {res.frames} frames, "
                          f"sha256 {digest[:12]}…")
            audit(db, owner, "studio.render_completed", "render_job", job_id, {"sha256": digest, "format": ext})
            db.commit()
        return "completed"
    except RenderError as e:
        _fail(job_id, e.code, {"message": e.detail, **e.data}, final=True)
        return "failed"
    except Exception as e:  # transient (storage, process) — retried while attempts remain
        log.exception("render job %s attempt failed", job_id)
        return _fail(job_id, "render_error", {"message": f"{type(e).__name__}: {str(e)[:300]}"}, final=False)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _fail(job_id: uuid.UUID, code: str, detail: dict, final: bool) -> str:
    with session_scope() as db:
        j = db.get(RenderJob, job_id)
        retry = not final and j.attempt < j.max_attempts and not j.cancel_requested
        j.state = "queued" if retry else ("canceled" if code == "canceled" else "failed")
        j.error_code, j.error_detail = code, detail
        j.lease_owner, j.lease_expires_at = None, None
        if not retry:
            j.finished_at = utcnow()
        _log(db, j, f"{'retry' if retry else 'failed'}: {code}")
        db.commit()
        return "queued" if retry else "failed"


def preview(db: Session, pr: AudioProject, actor_id: uuid.UUID, start: int, frames: int, profile_key: str) -> bytes:
    """Server-rendered window with exactly the export code path (spec §2.4)."""
    if profile_key not in PROFILES:
        raise ApiError("validation_failed", 422, details={"field": "profile"})
    frames = max(1, min(frames, PREVIEW_MAX_SECONDS * pr.sample_rate))
    st = validate_state(db, actor_id, pr.state)
    s = get_settings()
    cache = os.path.join(s.job_workdir, "studio-cache")
    with tempfile.TemporaryDirectory(dir=s.job_workdir) as d:
        sources = {}
        for i in st.asset_ids():
            a = db.get(Asset, uuid.UUID(i))
            spool = os.path.join(cache, f"{i}-{pr.sample_rate}.f64")
            local = os.path.join(d, f"src-{i}")
            if not os.path.exists(spool):
                get_storage().get_to_file(a.storage_key, local)
            sources[i] = decode_source(local, cache, i, pr.sample_rate)
        out = os.path.join(d, "preview.wav")
        try:
            render(pr.state, fs=pr.sample_rate, bits=pr.bit_depth, profile_key=profile_key, sources=sources,
                   out_path=out, start=start, frames=frames)
        except RenderError as e:
            raise ApiError(e.code if e.code in ("clipping", "empty_project", "too_large_for_wav") else "render_failed",
                           422, details={"message": e.detail, **e.data})
        return open(out, "rb").read()
