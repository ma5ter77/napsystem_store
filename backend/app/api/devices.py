from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..errors import ApiError
from ..models import App, Device, PlaySession, Questionnaire
from ..services import sessions as svc
from .deps import DB, P, as_uuid, locale_of

router = APIRouter(tags=["devices/sessions/measurements"])


def _device_view(d: Device) -> dict:
    return {"id": str(d.id), "adapter": d.adapter, "name": d.name, "model": d.model_code,
            "capabilities": d.capabilities, "status": d.status, "simulated": bool(d.capabilities.get("simulated")),
            "calibration": d.calibration or {}}


class DeviceIn(BaseModel):
    adapter: str
    name: str = ""
    profile: str | None = None


@router.get("/devices")
def devices(p=P, db=DB):
    u = p.require_user()
    return {"items": [_device_view(d) for d in db.scalars(select(Device).where(Device.owner_id == u.id,
                                                                                 Device.status == "active"))]}


@router.post("/devices", status_code=201)
def register(body: DeviceIn, p=P, db=DB):
    u = p.require_user()
    d, token = svc.register_device(db, db.merge(u), body.adapter, body.name, body.profile)
    db.commit()
    return _device_view(d) | {"device_token": token, "note": "device-service credential, shown once"}


# NAP-101: output calibration — the listener evens out their speakers / chair at the listening place.
# Only level and arrival time change, per channel; the signal itself (and the binaural difference) is untouched.
CAL_GAIN_DB = 6.0
CAL_DELAY_MS = 20.0
CAL_LAYOUTS = ("acoustic_3_0", "surround_5_1", "surround_7_1")


class CalibrationIn(BaseModel):
    layout: str
    channels: dict[str, dict] = Field(default_factory=dict)   # {} = reset this layout


@router.put("/devices/{device_id}/calibration")
def set_calibration(device_id: str, body: CalibrationIn, p=P, db=DB):
    u = p.require_user()
    d = db.get(Device, as_uuid(device_id, "device"), with_for_update=True)
    if not d or d.owner_id != u.id or d.status != "active":
        raise ApiError("not_found", 404, entity="device")
    if body.layout not in CAL_LAYOUTS:
        raise ApiError("validation_failed", 422, details={"field": "layout"})
    from ..services.conversion import layout_channels
    allowed, out, bad = layout_channels(body.layout), {}, []
    for ch, v in body.channels.items():
        try:
            g, dl = float(v.get("gain_db", 0)), float(v.get("delay_ms", 0))
        except (TypeError, ValueError, AttributeError):
            bad.append(f"channels.{ch}")
            continue
        if ch not in allowed or abs(g) > CAL_GAIN_DB or not 0 <= dl <= CAL_DELAY_MS:
            bad.append(f"channels.{ch}")
        elif g or dl:
            out[ch] = {"gain_db": round(g, 1), "delay_ms": round(dl, 1)}
    if bad:
        raise ApiError("validation_failed", 422, details={"fields": bad})
    cal = dict(d.calibration or {})
    if out:
        cal[body.layout] = out
    else:
        cal.pop(body.layout, None)
    d.calibration = cal
    db.commit()
    return _device_view(d)


@router.get("/devices/{device_id}/compatibility")
def device_compat(device_id: str, app: str, p=P, db=DB):
    u = p.require_user()
    d = db.get(Device, as_uuid(device_id, "device"))
    a = db.scalar(select(App).where(App.slug == app))
    if not d or d.owner_id != u.id or not a or not a.published_version_id:
        raise ApiError("not_found", 404, entity="device")
    from ..models import AppVersion
    v = db.get(AppVersion, a.published_version_id)
    reasons = svc.compatibility(d, v.requirements)
    return {"compatible": not [r for r in reasons if r != "headphones_confirmation_required"], "reasons": reasons}


@router.get("/questionnaires/{key}")
def questionnaire(key: str, request: Request, db=DB):
    q = db.scalar(select(Questionnaire).where(Questionnaire.key == key))
    if not q:
        raise ApiError("not_found", 404, entity="questionnaire")
    loc = locale_of(request)
    return {"key": q.key, "items": [{"id": i["id"], "min": i["min"], "max": i["max"],
                                     "label": i["labels"].get(loc) or i["labels"]["ru"],
                                     "low": i.get("low", {}).get(loc), "high": i.get("high", {}).get(loc)}
                                    for i in q.items]}


class SessionIn(BaseModel):
    app_id: str
    session_key: str
    device_id: str
    max_channels: int = 2   # NAP-108: channels of the listener's audio output (browser), picks the layout


@router.post("/sessions", status_code=201)
def create(body: SessionIn, request: Request, p=P, db=DB):
    u = p.require_user()
    p.require_scope("sessions:write")
    s = svc.create_session(db, db.merge(u), as_uuid(body.app_id, "app"), body.session_key, as_uuid(body.device_id, "device"))
    db.commit()
    return svc.session_view(db, s, locale_of(request)) | {"grant": svc.grant(db, s, body.max_channels)}


def _load(db, p, session_id, lock=False) -> PlaySession:
    if p.method == "device":
        return svc.own_session(db, as_uuid(session_id, "session"), None, p.device_id, lock)
    u = p.require_user()
    return svc.own_session(db, as_uuid(session_id, "session"), u.id, None, lock)


@router.get("/sessions/{session_id}")
def get(session_id: str, request: Request, p=P, db=DB):
    s = _load(db, p, session_id)
    return svc.session_view(db, s, locale_of(request))


@router.post("/sessions/{session_id}/grant")
def regrant(session_id: str, max_channels: int = 2, p=P, db=DB):
    """Signed links are short-lived; an expired link requires a fresh, re-authorized grant."""
    s = _load(db, p, session_id)
    if s.state not in ("created", "precheck", "ready", "running", "paused"):
        raise ApiError("invalid_state", 409)
    from ..models.content import ProgramSession
    from ..services.billing import access_for, require_session
    ps = db.get(ProgramSession, s.program_session_id)
    require_session(access_for(db, s.user_id, s.app_id), ps.key if ps else "")
    return svc.grant(db, s, max_channels)


class CommandIn(BaseModel):
    command_id: str = Field(min_length=1, max_length=64)
    command: str
    expected_version: int
    data: dict = {}


@router.post("/sessions/{session_id}/commands")
def command(session_id: str, body: CommandIn, p=P, db=DB):
    s = _load(db, p, session_id, lock=True)
    res = svc.command(db, s, body.command_id, body.command, body.expected_version, body.data)
    db.commit()
    return res


class EventsIn(BaseModel):
    events: list[dict]


@router.post("/sessions/{session_id}/events")
def events(session_id: str, body: EventsIn, p=P, db=DB):
    s = _load(db, p, session_id, lock=True)
    if len(body.events) > 500:
        raise ApiError("validation_failed", 422)
    res = svc.ingest_events(db, s, body.events)
    db.commit()
    return res


class AnswerIn(BaseModel):
    phase: str
    status: str
    answers: dict = {}


@router.post("/sessions/{session_id}/questionnaire")
def answer(session_id: str, body: AnswerIn, p=P, db=DB):
    u = p.require_user()
    s = svc.own_session(db, as_uuid(session_id, "session"), u.id, lock=True)
    res = svc.answer_questionnaire(db, s, body.phase, body.status, body.answers)
    db.commit()
    return res


@router.post("/sessions/{session_id}/measurements", status_code=201)
def measurement(session_id: str, body: dict, p=P, db=DB):
    s = _load(db, p, session_id, lock=True)
    m = svc.add_measurement(db, s, body)
    db.commit()
    return {"id": str(m.id), "synthetic": m.synthetic}


@router.get("/me/sessions")
def history(request: Request, p=P, db=DB):
    u = p.require_user()
    rows = db.scalars(select(PlaySession).where(PlaySession.user_id == u.id).order_by(PlaySession.created_at.desc())
                      .limit(100))
    return {"items": [svc.session_view(db, s, locale_of(request)) for s in rows]}


class ConsentIn(BaseModel):
    granted: bool
    retention_days: int | None = Field(default=None, ge=1, le=3650)


@router.get("/me/consent")
def get_consent(p=P, db=DB):
    u = p.require_user()
    return {"store_measurements": svc.has_consent(db, u.id)}


@router.put("/me/consent")
def consent(body: ConsentIn, p=P, db=DB):
    u = p.require_user()
    svc.set_consent(db, db.merge(u), body.granted, body.retention_days)
    db.commit()
    return {"store_measurements": body.granted}


@router.delete("/me/measurements")
def delete_measurements(p=P, db=DB):
    u = p.require_user()
    n = svc.delete_measurements(db, db.merge(u))
    db.commit()
    return {"deleted": n}
