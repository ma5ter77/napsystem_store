"""NAP-027: developer analytics — funnel (views → access → started → completed), weekly trend and retention.

Privacy:
- page views are counted in Redis HyperLogLog sketches per app and day (`an:v:<app>:<yyyymmdd>`), kept 400 days.
  The sketch stores no visitor ids, only a probabilistic set (≈0.8 % error); the visitor key sent by the
  browser is a random id it keeps locally (or the signed-in user id), hashed with the server secret first;
- every number based on people (access, started, completed, cohorts, weekly activity) below
  `analytics_min_group` is returned as null with `suppressed: true`, so small groups are never visible.
If Redis is unavailable, views are reported as null with `views_unavailable: true`; everything else still works.
"""
from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import utcnow
from ..models import App, DigitalOrder, Entitlement, PlaySession

VIEW_TTL = 400 * 86400
PERIODS = (7, 30, 90)


def _redis():
    import redis
    return redis.Redis.from_url(get_settings().redis_url, socket_timeout=0.5, socket_connect_timeout=0.5)


def _vkey(app_id: uuid.UUID, d: date) -> str:
    return f"an:v:{app_id}:{d:%Y%m%d}"


def record_view(app_id: uuid.UUID, visitor: str) -> bool:
    digest = hmac.new(get_settings().secret_key.encode(), visitor.encode(), hashlib.sha256).hexdigest()[:32]
    try:
        r = _redis()
        k = _vkey(app_id, utcnow().date())
        pipe = r.pipeline()
        pipe.pfadd(k, digest)
        pipe.expire(k, VIEW_TTL)
        pipe.execute()
        return True
    except Exception:  # noqa: BLE001 — analytics must never break the store page
        return False


def unique_views(app_id: uuid.UUID, start: date, end: date) -> int | None:
    """Unique visitors over [start, end] (inclusive), or None when Redis is unavailable."""
    keys = [_vkey(app_id, start + timedelta(days=i)) for i in range((end - start).days + 1)]
    try:
        return int(_redis().pfcount(*keys))
    except Exception:  # noqa: BLE001
        return None


def _k() -> int:
    return get_settings().analytics_min_group


def _guard(n: int) -> int | None:
    return n if n >= _k() else None


def _distinct(db: Session, col, *where) -> int:
    return db.scalar(select(func.count(func.distinct(col))).where(*where)) or 0


def funnel(db: Session, app: App, days: int, now: datetime | None = None) -> dict:
    now = now or utcnow()
    since = now - timedelta(days=days)
    # a real funnel: starts and completions are counted among the people who got access in the period
    cohort = select(Entitlement.user_id).where(Entitlement.app_id == app.id, Entitlement.created_at >= since)
    access = _distinct(db, Entitlement.user_id, Entitlement.app_id == app.id, Entitlement.created_at >= since)
    paid = _distinct(db, DigitalOrder.buyer_id, DigitalOrder.app_id == app.id, DigitalOrder.paid_at >= since,
                     DigitalOrder.status.in_(("paid", "partially_refunded", "refunded")))
    started = _distinct(db, PlaySession.user_id, PlaySession.app_id == app.id, PlaySession.started_at >= since,
                        PlaySession.user_id.in_(cohort))
    completed = _distinct(db, PlaySession.user_id, PlaySession.app_id == app.id, PlaySession.state == "completed",
                          PlaySession.started_at >= since, PlaySession.user_id.in_(cohort))
    views = unique_views(app.id, since.date(), now.date())
    steps = {"views": views, "access": _guard(access), "paid": _guard(paid), "started": _guard(started),
             "completed": _guard(completed)}

    def rate(a, b):
        return round(steps[b] / steps[a], 3) if steps[a] and steps[b] is not None else None
    return {"days": days, "steps": steps,
            "suppressed": [k for k in ("access", "paid", "started", "completed") if steps[k] is None],
            "views_unavailable": views is None,
            "rates": {"view_to_access": rate("views", "access"), "access_to_started": rate("access", "started"),
                      "started_to_completed": rate("started", "completed")}}


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def weekly(db: Session, app: App, weeks: int = 12, now: datetime | None = None) -> list[dict]:
    """Per ISO week: unique views, sessions started and completed; activity hidden when fewer than k people."""
    now = now or utcnow()
    first = _week_start(now.date()) - timedelta(weeks=weeks - 1)
    since = datetime.combine(first, datetime.min.time(), tzinfo=timezone.utc)
    wk = func.date_trunc("week", PlaySession.started_at)
    rows = db.execute(select(wk, func.count(), func.count(func.distinct(PlaySession.user_id)),
                             func.count().filter(PlaySession.state == "completed"))
                      .where(PlaySession.app_id == app.id, PlaySession.started_at >= since).group_by(wk)).all()
    act = {r[0].date(): (r[1], r[2], r[3]) for r in rows}
    out = []
    for i in range(weeks):
        ws = first + timedelta(weeks=i)
        started, people, done = act.get(ws, (0, 0, 0))
        hidden = 0 < people < _k()
        out.append({"week": ws.isoformat(), "views": unique_views(app.id, ws, ws + timedelta(days=6)),
                    "started": None if hidden else started, "completed": None if hidden else done,
                    "suppressed": hidden})
    return out


def retention(db: Session, app: App, cohorts: int = 8, now: datetime | None = None) -> list[dict]:
    """Cohorts by the week of a listener's first started session; share returning in weeks +1…+4."""
    now = now or utcnow()
    first_week = _week_start(now.date()) - timedelta(weeks=cohorts - 1)
    since = datetime.combine(first_week, datetime.min.time(), tzinfo=timezone.utc)
    firsts = dict(db.execute(select(PlaySession.user_id, func.min(PlaySession.started_at))
                             .where(PlaySession.app_id == app.id, PlaySession.started_at.isnot(None))
                             .group_by(PlaySession.user_id)).all())
    weeks_of: dict[uuid.UUID, set[date]] = {}
    for uid, started in db.execute(select(PlaySession.user_id, PlaySession.started_at).where(
            PlaySession.app_id == app.id, PlaySession.started_at >= since)).all():
        weeks_of.setdefault(uid, set()).add(_week_start(started.date()))
    this_week = _week_start(now.date())
    out = []
    for i in range(cohorts):
        cw = first_week + timedelta(weeks=i)
        members = [u for u, f in firsts.items() if _week_start(f.date()) == cw]
        size = len(members)
        row = {"week": cw.isoformat(), "size": _guard(size), "suppressed": 0 < size < _k(), "returned": {}}
        if size >= _k():
            for off in (1, 2, 3, 4):
                target = cw + timedelta(weeks=off)
                if target > this_week:
                    break
                back = sum(1 for u in members if target in weeks_of.get(u, set()))
                row["returned"][str(off)] = round(back / size, 3)
        out.append(row)
    return out


def app_report(db: Session, app: App, days: int) -> dict:
    return {"funnel": funnel(db, app, days), "weekly": weekly(db, app), "retention": retention(db, app),
            "min_group": _k()}
