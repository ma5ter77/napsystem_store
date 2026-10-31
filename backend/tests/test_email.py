"""NAP-007: notifications are e-mailed once per notification, localized, with retry and backoff."""
import email
import uuid
from email import policy

import pytest

from app.config import get_settings
from app.db import session_scope
from app.models import Notification, Outbox, User
from app.services import billing
from app.services.common import notify

from .conftest import Client


def _notify(locale="ru"):
    c = Client(locale).register(locale=locale)
    key = f"test-mail:{uuid.uuid4().hex}"
    with session_scope() as db:
        u = db.get(User, uuid.UUID(c.user_id))
        notify(db, u.id, locale, "version_approved", key, app="Океан" if locale == "ru" else "Ocean", version="1.0",
               comment="ok")
    return c, key


def _outbox(key):
    with session_scope() as db:
        o = db.query(Outbox).filter(Outbox.dedupe_key == f"mail:{key}").one()
        return o.sent_at, o.attempts, o.next_attempt_at, o.last_error


@pytest.fixture
def mail(monkeypatch, tmp_path):
    s = get_settings()
    monkeypatch.setattr(s, "email_file_dir", str(tmp_path))
    return s, tmp_path, monkeypatch


def test_file_backend_writes_localized_message_once(mail):
    s, d, mp = mail
    mp.setattr(s, "email_backend", "file")
    c, key = _notify("ru")
    billing.dispatch_outbox(limit=500)
    files = list(d.glob("*.eml"))
    msgs = [email.message_from_bytes(f.read_bytes(), policy=policy.default) for f in files]
    m = next(x for x in msgs if x["X-NAP-Ref"] == key)
    assert m["Subject"] == "Версия 1.0 приложения «Океан» одобрена" and m["Content-Language"] == "ru"
    assert "Океан" in m.get_body(("plain",)).get_content() and m.get_body(("html",)) is not None
    with session_scope() as db:
        n = db.query(Notification).filter(Notification.dedupe_key == key).one()
        assert n.emailed_at is not None and n.email_error is None
    before = len(list(d.glob("*.eml")))
    billing.dispatch_outbox(limit=500)  # delivered once only
    assert len(list(d.glob("*.eml"))) == before
    assert _outbox(key)[0] is not None


def test_smtp_failure_backoff_then_delivery(mail):
    s, d, mp = mail
    mp.setattr(s, "email_backend", "smtp")
    mp.setattr(s, "smtp_host", "smtp.test")
    sent, state = [], {"down": True}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            if state["down"]:
                raise ConnectionRefusedError("smtp down")
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def ehlo(self): pass
        def starttls(self, context=None): pass
        def login(self, u, p): pass
        def send_message(self, m):
            sent.append(m)
            return {}
    import smtplib
    mp.setattr(smtplib, "SMTP", FakeSMTP)
    c, key = _notify("en")
    billing.dispatch_outbox(limit=500)
    sent_at, attempts, nxt, err = _outbox(key)
    assert sent_at is None and attempts == 1 and nxt is not None and "smtp down" in err
    with session_scope() as db:
        assert "ConnectionRefusedError" in db.query(Notification).filter(Notification.dedupe_key == key).one().email_error
    billing.dispatch_outbox(limit=500)  # still in backoff: not retried immediately
    assert _outbox(key)[1] == 1
    state["down"] = False
    with session_scope() as db:  # time passes
        db.query(Outbox).filter(Outbox.dedupe_key == f"mail:{key}").one().next_attempt_at = None
    billing.dispatch_outbox(limit=500)
    m = next(x for x in sent if x["X-NAP-Ref"] == key)
    assert m["Subject"] == "Version 1.0 of “Ocean” approved" and m["To"].endswith("@example.com")
    assert _outbox(key)[0] is not None
