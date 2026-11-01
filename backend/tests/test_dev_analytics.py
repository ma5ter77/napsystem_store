"""NAP-027: developer analytics — funnel, weekly trend, retention; groups smaller than k are hidden."""
import uuid
from datetime import timedelta

from sqlalchemy import select

from app.db import session_scope, utcnow
from app.models import App, AppVersion, Device, Entitlement, PlaySession, ProgramSession, User
from app.services import analytics

from .conftest import Client, ok
from .helpers import make_published_app


def _listeners(app_id: str, n: int, *, completed: int, weeks_ago: int = 0, returning: int = 0):
    """n listeners got access and started a session `weeks_ago` weeks ago; `returning` of them came back a week later."""
    with session_scope() as db:
        app = db.get(App, uuid.UUID(app_id))
        ver = db.get(AppVersion, app.published_version_id)
        ps = db.scalar(select(ProgramSession).where(ProgramSession.version_id == ver.id))
        start = utcnow() - timedelta(weeks=weeks_ago)
        for i in range(n):
            u = User(email=f"an-{uuid.uuid4().hex[:10]}@test.local", password_hash="x", display_name="Listener", locale="ru")
            db.add(u)
            db.flush()
            d = Device(owner_id=u.id, adapter="simulated", name="Test headphones", capabilities={})
            db.add(d)
            db.flush()
            db.add(Entitlement(user_id=u.id, app_id=app.id, source="free", created_at=start))
            done = i < completed
            db.add(PlaySession(user_id=u.id, device_id=d.id, app_id=app.id, version_id=ver.id, program_session_id=ps.id,
                               state="completed" if done else "cancelled", created_at=start, started_at=start,
                               ended_at=start + timedelta(minutes=10)))
            if i < returning:
                later = start + timedelta(weeks=1)
                db.add(PlaySession(user_id=u.id, device_id=d.id, app_id=app.id, version_id=ver.id,
                                   program_session_id=ps.id, state="completed", created_at=later, started_at=later,
                                   ended_at=later + timedelta(minutes=10)))


def _app():
    dev = Client().register(developer=True)
    pub = make_published_app(dev, Client().login("moderator@nap.demo"), kind="free", amount=0)
    return dev, pub


def test_views_are_unique_per_visitor_and_need_a_published_app():
    dev, pub = _app()
    anon = Client()
    for _ in range(3):
        assert anon.post(f"/catalog/apps/{pub['slug']}/view", {"visitor": "visitor-aaaa-1111"}).status_code == 204
    assert Client().post(f"/catalog/apps/{pub['slug']}/view", {"visitor": "visitor-bbbb-2222"}).status_code == 204
    assert anon.post("/catalog/apps/no-such-app/view", {"visitor": "visitor-aaaa-1111"}).status_code == 404
    rep = ok(dev.get(f"/developer/analytics/apps/{pub['app_id']}?days=7"))
    assert rep["funnel"]["steps"]["views"] == 2
    assert rep["weekly"][-1]["views"] == 2


def test_small_groups_are_hidden_everywhere():
    dev, pub = _app()
    _listeners(pub["app_id"], 3, completed=2)
    rep = ok(dev.get(f"/developer/analytics/apps/{pub['app_id']}?days=30"))
    f = rep["funnel"]
    assert f["steps"]["access"] is None and f["steps"]["started"] is None and f["steps"]["completed"] is None
    assert set(f["suppressed"]) >= {"access", "started", "completed"}
    last = rep["weekly"][-1]
    assert last["suppressed"] and last["started"] is None
    assert rep["retention"][-1]["size"] is None and rep["retention"][-1]["suppressed"]
    assert rep["min_group"] == 5


def test_funnel_weekly_and_retention_with_enough_people():
    dev, pub = _app()
    _listeners(pub["app_id"], 6, completed=5, weeks_ago=2, returning=3)
    _listeners(pub["app_id"], 5, completed=2)
    rep = ok(dev.get(f"/developer/analytics/apps/{pub['app_id']}?days=30"))
    s = rep["funnel"]["steps"]
    assert s["access"] == 11 and s["started"] == 11 and s["completed"] == 7  # distinct people: 5 + 2
    assert rep["funnel"]["rates"]["access_to_started"] == 1.0
    active = [w for w in rep["weekly"] if w["started"] or w["suppressed"]]
    # 6 people two weeks ago, 3 returning a week ago (fewer than 5 → hidden), 5 people this week
    assert [w["started"] for w in active] == [6, None, 5] and active[1]["suppressed"]
    cohort = next(c for c in rep["retention"] if c["size"] == 6)
    assert cohort["returned"]["1"] == 0.5
    # only 7-, 30- and 90-day windows; other developers cannot see the app
    assert dev.get(f"/developer/analytics/apps/{pub['app_id']}?days=5").status_code == 422
    other = Client().register(developer=True)
    assert other.get(f"/developer/analytics/apps/{pub['app_id']}").status_code == 404


def test_views_unavailable_without_redis(monkeypatch):
    dev, pub = _app()

    def boom():
        raise ConnectionError("redis down")
    monkeypatch.setattr(analytics, "_redis", boom)
    assert Client().post(f"/catalog/apps/{pub['slug']}/view", {"visitor": "visitor-cccc-3333"}).status_code == 204
    rep = ok(dev.get(f"/developer/analytics/apps/{pub['app_id']}?days=7"))
    assert rep["funnel"]["steps"]["views"] is None and rep["funnel"]["views_unavailable"]
