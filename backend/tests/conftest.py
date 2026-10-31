import os
import tempfile
import uuid

_tmp = tempfile.mkdtemp(prefix="nap-test-")
os.environ.setdefault("NAP_DATABASE_URL", "postgresql+psycopg://nap:nap@localhost:5432/nap_test")
os.environ["NAP_ENV"] = "test"
os.environ["NAP_RUN_JOBS_INLINE"] = "true"
os.environ["NAP_STORAGE_ROOT"] = os.path.join(_tmp, "storage")
os.environ["NAP_JOB_WORKDIR"] = os.path.join(_tmp, "jobs")
os.environ["NAP_SECRET_KEY"] = "test-secret-key"
os.environ["NAP_RATE_LIMITS_ENABLED"] = "false"  # enabled explicitly in test_rate_limits

import pytest  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402


def _reset_db():
    url = os.environ["NAP_DATABASE_URL"]
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    eng.dispose()
    from alembic import command
    from alembic.config import Config
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "migrations"))
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session", autouse=True)
def database(request):
    if not any("test_dsp" in str(i.fspath) for i in request.session.items) or \
            any("test_dsp" not in str(i.fspath) for i in request.session.items):
        _reset_db()
        from app import seed
        seed.run()
    yield


class Client:
    def __init__(self, locale: str = "ru"):
        from fastapi.testclient import TestClient
        from app.main import app
        self.c = TestClient(app, raise_server_exceptions=True)
        self.csrf = None
        self.locale = locale
        self.headers = {"x-locale": locale}

    def _h(self, extra=None):
        h = dict(self.headers)
        if self.csrf:
            h["x-csrf-token"] = self.csrf
        h.update(extra or {})
        return h

    def login(self, email, password="demo-password-1"):
        r = self.c.post("/api/v1/auth/login", json={"email": email, "password": password}, headers=self._h())
        assert r.status_code == 200, r.text
        self.csrf = r.json()["csrf_token"]
        return self

    def register(self, developer=False, locale=None):
        email = f"u{uuid.uuid4().hex[:10]}@example.com"
        r = self.c.post("/api/v1/auth/register", json={"email": email, "password": "long-password-123",
                                                       "display_name": "Tester", "as_developer": developer,
                                                       "locale": locale or self.locale}, headers=self._h())
        assert r.status_code == 201, r.text
        self.csrf = r.json()["csrf_token"]
        self.email = email
        self.user_id = r.json()["id"]
        return self

    def get(self, url, **kw):
        return self.c.get("/api/v1" + url, headers=self._h(kw.pop("headers", None)), **kw)

    def post(self, url, json=None, **kw):
        return self.c.post("/api/v1" + url, json=json, headers=self._h(kw.pop("headers", None)), **kw)

    def put(self, url, json=None, **kw):
        return self.c.put("/api/v1" + url, json=json, headers=self._h(kw.pop("headers", None)), **kw)

    def patch(self, url, json=None, **kw):
        return self.c.patch("/api/v1" + url, json=json, headers=self._h(kw.pop("headers", None)), **kw)

    def delete(self, url, **kw):
        return self.c.delete("/api/v1" + url, headers=self._h(kw.pop("headers", None)), **kw)

    def raw(self):
        return self.c


@pytest.fixture
def client():
    return Client()


def ok(r, code=200):
    assert r.status_code == code, f"{r.status_code}: {r.text[:800]}"
    return r.json()
