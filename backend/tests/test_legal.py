"""NAP-062: legal documents are published with versions; sign-up records acceptance; measurement consent is versioned."""
import uuid

from sqlalchemy import select

from app.db import session_scope
from app.models import AuditLog, Consent

from .conftest import Client, ok

DOCS = ("terms", "privacy", "consent-personal-data", "consent-measurements")


def test_documents_are_published_in_both_languages_with_a_version():
    c = Client()
    idx = ok(c.get("/legal"))
    keys = {d["key"]: d for d in idx["documents"]}
    assert set(DOCS) <= set(keys)
    for k in DOCS:
        assert keys[k]["version"] and keys[k]["date"] and set(keys[k]["languages"]) == {"ru", "en"}
        for lang in ("ru", "en"):
            r = c.raw().get(f"/api/v1/legal/{k}/{lang}")
            assert r.status_code == 200 and r.text.lstrip().startswith("#") and len(r.text) > 1500, (k, lang)
            assert "1.0" in r.text[:600]  # the document states its version at the top
    assert c.raw().get("/api/v1/legal/terms/de").status_code == 404
    assert c.raw().get("/api/v1/legal/../release/ru").status_code == 404


def test_signup_requires_both_consents_and_records_versions():
    c = Client()
    body = {"email": f"l{uuid.uuid4().hex[:8]}@example.com", "password": "long-password-123", "display_name": "L"}
    r = c.post("/auth/register", body)
    assert r.status_code == 422 and set(r.json()["error"]["details"]["fields"]) == {"accept_terms", "consent_personal_data"}
    r = c.post("/auth/register", body | {"accept_terms": True})
    assert r.json()["error"]["details"]["fields"] == ["consent_personal_data"]
    uid = ok(c.post("/auth/register", body | {"accept_terms": True, "consent_personal_data": True}), 201)["id"]
    with session_scope() as db:
        a = db.scalar(select(AuditLog).where(AuditLog.actor_id == uuid.UUID(uid), AuditLog.action == "legal.accepted"))
        assert a.data["documents"] == {"terms": "1.0", "privacy": "1.0", "consent-personal-data": "1.0"}
        assert db.scalar(select(Consent).where(Consent.user_id == uuid.UUID(uid), Consent.kind == "personal_data"))


def test_measurement_consent_is_versioned_and_withdrawal_deletes_data():
    b = Client().register()
    ok(b.put("/me/consent", {"granted": True}))
    ok(b.put("/me/consent", {"granted": False}))
    with session_scope() as db:
        rows = list(db.scalars(select(AuditLog).where(AuditLog.actor_id == uuid.UUID(b.user_id),
                                                      AuditLog.action.in_(("consent.changed", "measurements.deleted")))
                               .order_by(AuditLog.created_at)))
    acts = [(r.action, (r.data or {}).get("granted")) for r in rows]
    assert ("consent.changed", True) in acts and ("measurements.deleted", None) in acts
    assert all(r.data["version"] == "1.0" for r in rows if r.action == "consent.changed")
