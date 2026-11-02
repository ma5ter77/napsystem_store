"""Public servers never accept the documented demo password (it is printed in docs and previews)."""
from sqlalchemy import select

from app.config import get_settings
from app.db import session_scope, utcnow
from app.models import AuthSession, User
from app.seed import secure_demo_accounts
from app.security import DOCUMENTED_DEMO_PASSWORD, demo_account_password, hash_password, verify_password

from .conftest import Client


def _settings(**kw):
    return get_settings().model_copy(update=kw)


def test_password_rules():
    assert demo_account_password(_settings(public_demo=False, demo_password="")) == DOCUMENTED_DEMO_PASSWORD
    a = demo_account_password(_settings(public_demo=True, demo_password="", secret_key="server-a"))
    b = demo_account_password(_settings(public_demo=True, demo_password="", secret_key="server-b"))
    assert a != b and DOCUMENTED_DEMO_PASSWORD not in (a, b) and len(a) >= 20
    assert a == demo_account_password(_settings(public_demo=True, demo_password="", secret_key="server-a"))
    assert demo_account_password(_settings(public_demo=True, demo_password="chosen-by-owner")) == "chosen-by-owner"


def test_public_server_moves_demo_accounts_off_the_documented_password(monkeypatch):
    with session_scope() as db:                        # a demo account still on the documented password
        u = User(email="probe-admin@nap.demo", password_hash=hash_password(DOCUMENTED_DEMO_PASSWORD),
                 display_name="probe", locale="ru")
        db.add(u)
        db.commit()
    c = Client()
    r = c.c.post("/api/v1/auth/login", json={"email": "probe-admin@nap.demo", "password": DOCUMENTED_DEMO_PASSWORD},
                 headers=c._h())
    assert r.status_code == 200                       # works before (this is the hole on a public server)

    import app.seed as seed
    monkeypatch.setattr(seed, "demo_account_password",
                        lambda: demo_account_password(_settings(public_demo=True, demo_password="", secret_key="srv")))
    with session_scope() as db:
        assert secure_demo_accounts(db, ["probe-admin@nap.demo"]) == 1
        db.commit()
    with session_scope() as db:
        u = db.scalar(select(User).where(User.email == "probe-admin@nap.demo"))
        assert not verify_password(u.password_hash, DOCUMENTED_DEMO_PASSWORD)
        assert all(s.revoked_at for s in db.scalars(select(AuthSession).where(AuthSession.user_id == u.id)))
    assert c.get("/me").status_code == 401           # the old session no longer works
    r = Client().c.post("/api/v1/auth/login", json={"email": "probe-admin@nap.demo", "password": DOCUMENTED_DEMO_PASSWORD})
    assert r.status_code == 401
    with session_scope() as db:                        # second run changes nothing
        assert secure_demo_accounts(db, ["probe-admin@nap.demo"]) == 0
