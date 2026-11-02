"""NAP Audio Studio API (spec v2.1 §10, M1). Authors (role `developer`) own their sources, projects and renders;
every object is checked against its owner on the backend."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import ratelimit
from ..errors import ApiError
from ..models import Asset, AudioProject, RenderArtifact, RenderJob
from ..services import audio_projects as svc
from ..services.assets import signed_url
from .deps import DB, P, as_uuid, idem

router = APIRouter(prefix="/audio", tags=["audio-studio"])


def author(p):
    return p.require_role("developer")


class AssetIn(BaseModel):
    filename: str = Field(max_length=255)
    size_bytes: int
    sha256: str | None = None
    rights: dict = Field(description="provenance: {source, license, holder?, notes?} — required (spec §2.10)")


@router.post("/assets", status_code=201)
def create_asset(body: AssetIn, request: Request, p=P, db=DB):
    ratelimit.check(request, "upload", p.id)
    u = author(p)
    out = svc.create_upload(db, u.id, body.filename, body.size_bytes, body.sha256, body.rights)
    db.commit()
    return out


@router.get("/assets")
def list_assets(p=P, db=DB):
    u = author(p)
    rows = db.scalars(select(Asset).where(Asset.owner_id == u.id, Asset.kind == "studio_source",
                                          Asset.status == "uploaded").order_by(Asset.created_at.desc()).limit(500))
    return {"items": [svc.asset_view(a) for a in rows]}


@router.get("/assets/{asset_id}")
def get_asset(asset_id: str, p=P, db=DB):
    u = author(p)
    return svc.asset_view(svc.own_source(db, u.id, asset_id))


@router.get("/assets/{asset_id}/waveform")
def get_waveform(asset_id: str, buckets: int = 2048, p=P, db=DB):
    u = author(p)
    a = svc.own_source(db, u.id, asset_id)
    wf = svc.waveform(db, a, max(64, min(buckets, 8192)))
    db.commit()
    return wf


class ProjectIn(BaseModel):
    title: str = Field(max_length=160)
    sample_rate: int = 48_000
    bit_depth: int = 24
    bus_count: int = 2


@router.post("/projects", status_code=201)
def create_project(body: ProjectIn, p=P, db=DB):
    u = author(p)
    pr = svc.create_project(db, u.id, body.title, body.sample_rate, body.bit_depth, body.bus_count)
    db.commit()
    return svc.project_view(pr)


@router.get("/projects")
def list_projects(p=P, db=DB):
    u = author(p)
    rows = db.scalars(select(AudioProject).where(AudioProject.owner_id == u.id).order_by(AudioProject.updated_at.desc()))
    return {"items": [svc.project_view(x, full=False) for x in rows]}


@router.get("/projects/{project_id}")
def get_project(project_id: str, p=P, db=DB):
    return svc.project_view(svc.own_project(db, author(p).id, project_id))


class ProjectPatch(BaseModel):
    base_revision: int
    state: dict | None = None
    title: str | None = Field(default=None, max_length=160)


@router.patch("/projects/{project_id}")
def patch_project(project_id: str, body: ProjectPatch, p=P, db=DB):
    u = author(p)
    pr = svc.own_project(db, u.id, project_id, lock=True)
    svc.update_project(db, pr, u.id, body.base_revision, body.state, body.title)
    db.commit()
    return svc.project_view(pr)


class RevisionIn(BaseModel):
    note: str = Field(default="", max_length=200)


@router.post("/projects/{project_id}/revisions", status_code=201)
def create_revision(project_id: str, body: RevisionIn, p=P, db=DB):
    u = author(p)
    pr = svc.own_project(db, u.id, project_id)
    svc.validate_state(db, u.id, pr.state)
    rev = svc.snapshot(db, pr, u.id, body.note)
    db.commit()
    return {"id": str(rev.id), "number": rev.number, "state_sha256": rev.state_sha256,
            "created_at": rev.created_at.isoformat()}


class RenderIn(BaseModel):
    profile: str
    format: str = "wav"          # wav (RIFF, RF64 above 4 GiB) | flac (lossless, 16/24-bit)


@router.post("/projects/{project_id}/renders", status_code=201)
def create_render(project_id: str, body: RenderIn, request: Request, p=P, db=DB, key=Depends(idem)):
    ratelimit.check(request, "conversion", p.id)
    u = author(p)
    pr = svc.own_project(db, u.id, project_id)
    job = svc.start_render(db, pr, u.id, body.profile, key, body.format)
    db.commit()
    from ..worker import dispatch_render
    dispatch_render(job.id)
    db.refresh(job)
    return svc.job_view(db, job)


@router.get("/projects/{project_id}/renders")
def list_renders(project_id: str, p=P, db=DB):
    u = author(p)
    pr = svc.own_project(db, u.id, project_id)
    rows = db.scalars(select(RenderJob).where(RenderJob.project_id == pr.id).order_by(RenderJob.created_at.desc()).limit(50))
    return {"items": [svc.job_view(db, j) for j in rows]}


@router.get("/render-jobs/{job_id}")
def get_render_job(job_id: str, p=P, db=DB):
    u = author(p)
    j = db.get(RenderJob, as_uuid(job_id, "render_job"))
    if not j or j.owner_id != u.id:
        raise ApiError("not_found", 404, entity="render_job")
    return svc.job_view(db, j)


@router.post("/render-jobs/{job_id}/cancel")
def cancel_render(job_id: str, p=P, db=DB):
    u = author(p)
    j = db.get(RenderJob, as_uuid(job_id, "render_job"), with_for_update=True)
    if not j or j.owner_id != u.id:
        raise ApiError("not_found", 404, entity="render_job")
    if j.state in ("completed", "failed", "canceled"):
        raise ApiError("invalid_state", 409)
    j.cancel_requested = True
    if j.state == "queued":
        j.state = "canceled"
    db.commit()
    return svc.job_view(db, j)


@router.get("/render-jobs/{job_id}/download")
def download_render(job_id: str, p=P, db=DB):
    """Short-lived signed link for the author only (masters are never public, spec §6)."""
    u = author(p)
    j = db.get(RenderJob, as_uuid(job_id, "render_job"))
    if not j or j.owner_id != u.id:
        raise ApiError("not_found", 404, entity="render_job")
    art = db.scalar(select(RenderArtifact).where(RenderArtifact.render_job_id == j.id))
    if not art:
        raise ApiError("not_ready", 409)
    a = db.get(Asset, art.asset_id)
    return {"url": signed_url(a, f"render-{j.profile_key}-{str(j.id)[:8]}.{art.format}"), "sha256": art.sha256,
            "format": art.format}


@router.get("/routing-profiles")
def routing_profiles(p=P, db=DB):
    u = author(p)
    out = [svc.profile_view(x) for x in svc.list_profiles(db, u.id)]
    db.commit()
    return {"items": out}


@router.post("/routing-profiles", status_code=201)
def create_routing_profile(body: dict, p=P, db=DB):
    u = author(p)
    prof = svc.create_profile(db, u.id, body)
    db.commit()
    return svc.profile_view(prof)


@router.put("/routing-profiles/{key}")
def update_routing_profile(key: str, body: dict, p=P, db=DB):
    u = author(p)
    prof = svc.update_profile(db, u.id, key, body)
    db.commit()
    return svc.profile_view(prof)


@router.delete("/routing-profiles/{key}", status_code=204)
def archive_routing_profile(key: str, p=P, db=DB):
    u = author(p)
    svc.archive_profile(db, u.id, key)
    db.commit()
    return Response(status_code=204)


class PreviewIn(BaseModel):
    start_frame: int = Field(ge=0)
    frames: int = Field(gt=0)
    profile: str


@router.post("/projects/{project_id}/preview")
def preview(project_id: str, body: PreviewIn, request: Request, p=P, db=DB):
    ratelimit.check(request, "conversion", p.id)
    u = author(p)
    pr = svc.own_project(db, u.id, project_id)
    data = svc.preview(db, pr, u.id, body.start_frame, body.frames, body.profile)
    return Response(content=data, media_type="audio/wav", headers={"Cache-Control": "no-store",
                                                                   "X-Render-Path": "export-identical"})
