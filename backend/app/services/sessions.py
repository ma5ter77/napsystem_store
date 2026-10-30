"""Devices, play sessions (versioned commands, ordered events), questionnaires, measurements and consent.

created -> precheck -> ready -> running <-> paused -> completed;  alternatives: cancelled, failed
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..adapters.devices import SIM_PROFILES, WEB_HEADPHONES, DeviceProtocolUnavailable, adapter_for
from ..config import get_settings
from ..db import utcnow
from ..errors import ApiError
from ..models import (App, AppVersion, Asset, Consent, Device, Measurement, PlaySession, ProgramSession,
                      Questionnaire, QuestionnaireResponse, SessionCommand, SessionEvent, User)
from ..models.devices import SESSION_ACTIVE
from ..security import generate_device_token
from .assets import signed_url
from .billing import access_for
from .common import audit

TRANSITIONS = {
    "precheck": ({"created"}, "precheck"),
    "confirm_ready": ({"precheck"}, "ready"),
    "start": ({"ready"}, "running"),
    "pause": ({"running"}, "paused"),
    "resume": ({"paused"}, "running"),
    "complete": ({"running", "paused"}, "completed"),
    "stop": (set(SESSION_ACTIVE), "cancelled"),
    "fail": (set(SESSION_ACTIVE), "failed"),
}
MEASUREMENT_UNITS = {"self_report": {"score"}, "heart_rate": {"bpm"}, "hrv_rmssd": {"ms"}, "eeg": {"uV2"}}


# ------------------------------------------------------------------ devices

def register_device(db: Session, user: User, adapter: str, name: str, profile: str | None) -> tuple[Device, str]:
    if adapter == "simulated":
        prof = profile or "sim-chair-v1"
        if prof not in SIM_PROFILES:
            raise ApiError("validation_failed", 422, details={"field": "profile"})
        caps = SIM_PROFILES[prof]
        model = prof
    elif adapter == "web_headphones":
        caps, model = WEB_HEADPHONES, "web"
    elif adapter == "nap_chair":
        raise ApiError("device_protocol_unavailable", 501)
    else:
        raise ApiError("validation_failed", 422, details={"field": "adapter"})
    token, h = generate_device_token()
    d = Device(owner_id=user.id, adapter=adapter, name=(name or model)[:120], model_code=model,
               capabilities=caps.to_dict() | {"provides": sorted(caps.provides())}, token_hash=h)
    db.add(d)
    db.flush()
    audit(db, user.id, "device.registered", "device", d.id, {"adapter": adapter, "model": model})
    return d, token


def _api_tuple(v: str) -> tuple[int, int]:
    a, b = (v.split(".") + ["0"])[:2]
    return int(a), int(b)


def compatibility(device: Device, requirements: dict, headphones_confirmed: bool = False) -> list[str]:
    """Returns reasons blocking the start (empty = compatible)."""
    caps = device.capabilities
    provides = set(caps.get("provides", []))
    if caps.get("channel_separation") == "user_confirmation_required" and headphones_confirmed:
        provides.add("stereo_separation")  # user confirmation; NOT a hardware isolation measurement
    reasons = []
    for c in requirements.get("required_capabilities", []):
        if c not in provides:
            if c == "stereo_separation" and caps.get("channel_separation") == "user_confirmation_required":
                reasons.append("headphones_confirmation_required")
            else:
                reasons.append(f"missing_capability:{c}")
    if _api_tuple(caps.get("api_version", "0.0")) < _api_tuple(requirements.get("min_api_version", "1.0")):
        reasons.append("device_api_too_old")
    if 48000 not in caps.get("sample_rates", []):
        reasons.append("sample_rate_unsupported")
    return reasons


# ------------------------------------------------------------------ sessions

def create_session(db: Session, user: User, app_id: uuid.UUID, session_key: str, device_id: uuid.UUID) -> PlaySession:
    app = db.get(App, app_id)
    if not app or not app.published_version_id:
        raise ApiError("not_found", 404, entity="app")
    if not access_for(db, user.id, app.id)["has_access"]:
        raise ApiError("no_entitlement", 403)
    dev = db.get(Device, device_id)
    if not dev or dev.owner_id != user.id or dev.status != "active":
        raise ApiError("not_found", 404, entity="device")
    v = db.get(AppVersion, app.published_version_id)  # session is pinned to the published version
    ps = next((s for s in v.sessions if s.key == session_key), None)
    if ps is None:
        raise ApiError("not_found", 404, entity="program_session")
    reasons = [r for r in compatibility(dev, v.requirements) if r != "headphones_confirmation_required"]
    if reasons:
        raise ApiError("incompatible_device", 409, details={"reasons": reasons})
    s = PlaySession(user_id=user.id, device_id=dev.id, app_id=app.id, version_id=v.id, program_session_id=ps.id,
                    pre_status="pending" if v.questionnaire_pre else "skipped",
                    post_status="pending" if v.questionnaire_post else "skipped")
    db.add(s)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ApiError("device_busy", 409)
    audit(db, user.id, "session.created", "play_session", s.id, {"version": str(v.id)})
    return s


def grant(db: Session, s: PlaySession) -> dict:
    """Pinned version + files + short-lived permission (re-request when expired)."""
    ps = db.get(ProgramSession, s.program_session_id)
    a = db.get(Asset, ps.delivery_asset_id)
    v = db.get(AppVersion, s.version_id)
    return {"session_id": str(s.id), "version_id": str(v.id), "version": v.version_label,
            "manifest_sha256": v.manifest_sha256, "program_session": {"key": ps.key, "titles": ps.titles,
                                                                       "duration_seconds": ps.duration_seconds},
            "asset": {"id": str(a.id), "sha256": a.sha256, "size_bytes": a.size_bytes, "content_type": a.content_type,
                      "url": signed_url(a, f"{ps.key}.flac"), "expires_in": get_settings().signed_url_ttl_seconds},
            "playback": {"channels": 2, "sample_rate": 48000, "mono_downmix": False, "crossfeed": False,
                         "channel_swap": False}}


def own_session(db: Session, session_id: uuid.UUID, user_id: uuid.UUID | None, device_id: uuid.UUID | None = None,
                lock: bool = False) -> PlaySession:
    s = db.get(PlaySession, session_id, with_for_update=lock)
    if not s:
        raise ApiError("not_found", 404, entity="session")
    if device_id is not None:
        if s.device_id != device_id:
            raise ApiError("not_found", 404, entity="session")
    elif s.user_id != user_id:
        raise ApiError("not_found", 404, entity="session")
    return s


def command(db: Session, s: PlaySession, command_id: str, cmd: str, expected_version: int, data: dict) -> dict:
    prev = db.scalar(select(SessionCommand).where(SessionCommand.session_id == s.id,
                                                  SessionCommand.command_id == command_id))
    if prev:  # repeat returns the previous result; never re-executes
        return prev.result | {"replayed": True}
    if cmd not in TRANSITIONS:
        raise ApiError("validation_failed", 422, details={"field": "command"})
    if expected_version != s.state_version:
        raise ApiError("version_conflict", 409, details={"state": s.state, "state_version": s.state_version})
    allowed, target = TRANSITIONS[cmd]
    if s.state not in allowed:
        raise ApiError("invalid_transition", 409, details={"state": s.state, "command": cmd})
    dev = db.get(Device, s.device_id)
    v = db.get(AppVersion, s.version_id)
    if cmd == "precheck":
        s.headphones_confirmed = bool(data.get("headphones_confirmed"))
        s.lr_test_passed = bool(data.get("lr_test_passed"))
        reasons = compatibility(dev, v.requirements, s.headphones_confirmed)
        if dev.capabilities.get("channel_separation") == "user_confirmation_required" and not s.lr_test_passed:
            reasons.append("lr_test_required")
        s.channel_separation_source = ("device_capability" if dev.capabilities.get("channel_separation") ==
                                       "verified_hardware" else ("user_confirmed" if s.headphones_confirmed else None))
        s.precheck = {"reasons": reasons, "checked_at": utcnow().isoformat(),
                      "note": "user L/R test and headphone confirmation are not a hardware isolation measurement"
                      if s.channel_separation_source == "user_confirmed" else None}
    if cmd == "confirm_ready":
        reasons = (s.precheck or {}).get("reasons", ["precheck_missing"])
        if reasons:
            raise ApiError("incompatible_device", 409, details={"reasons": reasons})
    adapter = adapter_for(dev.adapter)
    try:
        if cmd == "confirm_ready":
            adapter.prepare_session(str(dev.id), {"id": str(s.id)})
        elif cmd in ("start", "pause", "resume"):
            getattr(adapter, cmd)(str(dev.id), str(s.id))
        elif cmd in ("stop", "complete", "fail"):
            adapter.stop(str(dev.id), str(s.id))
    except DeviceProtocolUnavailable:
        raise ApiError("device_protocol_unavailable", 501)
    s.state = target
    s.state_version += 1
    if "position_seconds" in data:
        s.position_seconds = float(data["position_seconds"])
    if target == "running" and not s.started_at:
        s.started_at = utcnow()
    if target in ("completed", "cancelled", "failed"):
        s.ended_at = utcnow()
        if target == "failed":
            s.failure_reason = str(data.get("reason", "unknown"))[:64]
    result = {"session_id": str(s.id), "state": s.state, "state_version": s.state_version, "command": cmd,
              "precheck": s.precheck if cmd == "precheck" else None}
    db.add(SessionCommand(session_id=s.id, command_id=command_id, command=cmd, expected_version=expected_version,
                          result=result))
    try:
        db.flush()
    except IntegrityError:  # concurrent duplicate of the same command
        db.rollback()
        prev = db.scalar(select(SessionCommand).where(SessionCommand.session_id == s.id,
                                                      SessionCommand.command_id == command_id))
        return prev.result | {"replayed": True}
    return result


def ingest_events(db: Session, s: PlaySession, events: list[dict]) -> dict:
    """Ordered, deduplicated controller events (also those buffered offline). Old commands are not re-run."""
    accepted, dup = [], []
    for e in sorted(events, key=lambda x: int(x["seq"])):
        res = db.execute(insert(SessionEvent).values(
            id=uuid.uuid4(), session_id=s.id, event_id=str(e["event_id"])[:64], seq=int(e["seq"]),
            type=str(e["type"])[:32], data=e.get("data") or {},
            occurred_at=datetime.fromisoformat(str(e["occurred_at"]).replace("Z", "+00:00")))
            .on_conflict_do_nothing().returning(SessionEvent.id))
        if res.scalar() is None:
            dup.append(e["event_id"])
            continue
        accepted.append(e["event_id"])
        s.last_event_seq = max(s.last_event_seq, int(e["seq"]))
        d = e.get("data") or {}
        if "position_seconds" in d:
            s.position_seconds = max(s.position_seconds, float(d["position_seconds"]))
        # controller reports its actual state (e.g. local stop/complete while offline)
        if e["type"] == "state" and s.state in SESSION_ACTIVE and d.get("state") in ("completed", "cancelled", "failed"):
            s.state = d["state"]
            s.state_version += 1
            s.ended_at = datetime.fromisoformat(str(e["occurred_at"]).replace("Z", "+00:00"))
    return {"accepted": accepted, "duplicates": dup, "state": s.state, "state_version": s.state_version}


# ------------------------------------------------------------------ questionnaires & measurements

def has_consent(db: Session, user_id: uuid.UUID) -> bool:
    return db.scalar(select(Consent).where(Consent.user_id == user_id, Consent.kind == "store_measurements",
                                           Consent.revoked_at.is_(None))) is not None


def answer_questionnaire(db: Session, s: PlaySession, phase: str, status: str, answers: dict) -> dict:
    if phase not in ("pre", "post") or status not in ("completed", "skipped"):
        raise ApiError("validation_failed", 422)
    v = db.get(AppVersion, s.version_id)
    key = v.questionnaire_pre if phase == "pre" else v.questionnaire_post
    if not key:
        raise ApiError("validation_failed", 422)
    if phase == "post" and s.state not in ("completed", "cancelled", "failed"):
        raise ApiError("invalid_state", 409)
    if getattr(s, f"{phase}_status") != "pending":
        raise ApiError("already_answered", 409)
    q = db.scalar(select(Questionnaire).where(Questionnaire.key == key))
    clean = {}
    if status == "completed":
        for item in q.items:
            if item["id"] not in answers:
                raise ApiError("validation_failed", 422, details={"field": item["id"]})
            val = int(answers[item["id"]])
            if not item["min"] <= val <= item["max"]:
                raise ApiError("validation_failed", 422, details={"field": item["id"]})
            clean[item["id"]] = val
    consent = has_consent(db, s.user_id)
    db.add(QuestionnaireResponse(session_id=s.id, user_id=s.user_id, phase=phase, questionnaire_key=key,
                                 status=status, answers=clean if consent else {}))
    setattr(s, f"{phase}_status", status)
    if consent:
        for k, val in clean.items():
            db.add(Measurement(user_id=s.user_id, session_id=s.id, type="self_report", metric=k, value=val,
                               unit="score", phase=phase, occurred_at=utcnow(), source=f"questionnaire:{key}",
                               quality="self_reported", calc_version="1"))
    return {"status": status, "stored": consent, "answers": clean}


def add_measurement(db: Session, s: PlaySession, m: dict) -> Measurement:
    mtype = m.get("type")
    if mtype not in MEASUREMENT_UNITS or m.get("unit") not in MEASUREMENT_UNITS[mtype]:
        raise ApiError("validation_failed", 422, details={"field": "type/unit"})
    if mtype == "eeg":
        raise ApiError("eeg_not_supported", 422)
    if not has_consent(db, s.user_id):
        raise ApiError("consent_required", 403)
    dev = db.get(Device, s.device_id)
    sensor = {"heart_rate": "heart_rate", "hrv_rmssd": "hrv"}.get(mtype)
    if sensor and sensor not in dev.capabilities.get("sensors", []):
        raise ApiError("sensor_unavailable", 422)
    synthetic = bool(dev.capabilities.get("simulated"))
    if synthetic and not m.get("synthetic"):
        raise ApiError("synthetic_must_be_marked", 422)
    mm = Measurement(user_id=s.user_id, session_id=s.id, type=mtype, metric=str(m.get("metric") or mtype)[:48],
                     value=float(m["value"]), unit=m["unit"], phase=m.get("phase"),
                     occurred_at=datetime.fromisoformat(str(m["occurred_at"]).replace("Z", "+00:00")),
                     source=f"device:{dev.id}", quality=str(m.get("quality", "ok"))[:16],
                     calc_version=str(m.get("calc_version", "1"))[:16], synthetic=synthetic)
    db.add(mm)
    return mm


def session_view(db: Session, s: PlaySession, locale: str) -> dict:
    ps = db.get(ProgramSession, s.program_session_id)
    app = db.get(App, s.app_id)
    resp = {r.phase: r for r in db.scalars(select(QuestionnaireResponse).where(QuestionnaireResponse.session_id == s.id))}
    pre = resp.get("pre").answers if resp.get("pre") else {}
    post = resp.get("post").answers if resp.get("post") else {}
    delta = {k: post[k] - pre[k] for k in pre if k in post}
    meas = db.scalars(select(Measurement).where(Measurement.session_id == s.id, Measurement.type != "self_report")).all()
    return {"id": str(s.id), "app": {"id": str(app.id), "slug": app.slug}, "state": s.state,
            "state_version": s.state_version, "program_session": {"key": ps.key, "title": ps.titles.get(locale) or
                                                                   next(iter(ps.titles.values())),
                                                                   "duration_seconds": ps.duration_seconds},
            "device_id": str(s.device_id), "position_seconds": s.position_seconds,
            "headphones_confirmed": s.headphones_confirmed, "lr_test_passed": s.lr_test_passed,
            "channel_separation_source": s.channel_separation_source, "precheck": s.precheck,
            "pre_status": s.pre_status, "post_status": s.post_status, "pre": pre, "post": post, "delta": delta,
            "measurements": [{"type": m.type, "metric": m.metric, "value": m.value, "unit": m.unit,
                              "synthetic": m.synthetic, "source": m.source, "quality": m.quality,
                              "occurred_at": m.occurred_at.isoformat()} for m in meas],
            "created_at": s.created_at.isoformat(), "ended_at": s.ended_at.isoformat() if s.ended_at else None}


def set_consent(db: Session, user: User, granted: bool, retention_days: int | None) -> None:
    cur = db.scalar(select(Consent).where(Consent.user_id == user.id, Consent.kind == "store_measurements",
                                          Consent.revoked_at.is_(None)))
    if granted and not cur:
        db.add(Consent(user_id=user.id, kind="store_measurements",
                       retention_days=retention_days or get_settings().measurement_retention_days))
    elif not granted and cur:
        cur.revoked_at = utcnow()
    audit(db, user.id, "consent.changed", "user", user.id, {"granted": granted})


def delete_measurements(db: Session, user: User) -> int:
    n = db.execute(delete(Measurement).where(Measurement.user_id == user.id)).rowcount
    db.execute(QuestionnaireResponse.__table__.update().where(QuestionnaireResponse.user_id == user.id)
               .values(answers={}))
    audit(db, user.id, "measurements.deleted", "user", user.id, {"count": n})
    return n


def purge_expired_measurements(db: Session) -> int:
    n = 0
    for c in db.scalars(select(Consent).where(Consent.kind == "store_measurements")):
        cutoff = utcnow() - timedelta(days=c.retention_days)
        n += db.execute(delete(Measurement).where(Measurement.user_id == c.user_id,
                                                  Measurement.created_at < cutoff)).rowcount
    return n


def developer_analytics(db: Session, app: App) -> dict:
    """Aggregates only; groups smaller than analytics_min_group are suppressed."""
    k = get_settings().analytics_min_group
    users = db.scalar(select(func.count(func.distinct(PlaySession.user_id))).where(PlaySession.app_id == app.id)) or 0
    total = db.scalar(select(func.count()).select_from(PlaySession).where(PlaySession.app_id == app.id)) or 0
    completed = db.scalar(select(func.count()).select_from(PlaySession).where(
        PlaySession.app_id == app.id, PlaySession.state == "completed")) or 0
    if users < k:
        return {"suppressed": True, "min_group": k, "reason": "insufficient_group_size"}
    deltas = {}
    rows = db.execute(select(Measurement.metric, Measurement.phase, func.avg(Measurement.value),
                             func.count(func.distinct(Measurement.user_id)))
                      .join(PlaySession, PlaySession.id == Measurement.session_id)
                      .where(PlaySession.app_id == app.id, Measurement.type == "self_report")
                      .group_by(Measurement.metric, Measurement.phase)).all()
    agg: dict[str, dict] = {}
    for metric, phase, avg, n in rows:
        agg.setdefault(metric, {})[phase] = (float(avg), int(n))
    for metric, ph in agg.items():
        if "pre" in ph and "post" in ph and min(ph["pre"][1], ph["post"][1]) >= k:
            deltas[metric] = round(ph["post"][0] - ph["pre"][0], 2)
    return {"suppressed": False, "users": users, "sessions": total, "completed": completed,
            "completion_rate": round(completed / total, 3) if total else None, "self_report_avg_delta": deltas,
            "note": "self-reported scores; not a medical index"}
