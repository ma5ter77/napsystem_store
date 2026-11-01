from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..db import utcnow
from ..errors import ApiError
from ..models import Consent, Notification, RoleGrant, User
from ..security import (check_login_rate, clear_session_cookies, create_session, hash_password, record_login,
                        set_session_cookies, verify_password)
from ..services.common import audit
from .deps import DB, P, locale_of

router = APIRouter(tags=["auth/profile"])


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    display_name: str = Field(min_length=1, max_length=120)
    locale: str | None = None
    as_developer: bool = False
    # NAP-062: two separate, unticked checkboxes on the form — the terms (with the privacy policy) and the
    # personal-data consent (a separate document under 152-FZ since 01.09.2025)
    accept_terms: bool = False
    consent_personal_data: bool = False


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=200)


def me_payload(p) -> dict:
    u = p.user
    return {"id": str(u.id), "email": u.email, "display_name": u.display_name, "locale": u.locale,
            "roles": sorted(p.roles), "csrf_token": p.session.csrf_token if p.session else None}


@router.post("/auth/register", status_code=201)
def register(body: RegisterIn, request: Request, response: Response, db=DB):
    loc = body.locale if body.locale in ("ru", "en") else locale_of(request)
    missing = [f for f in ("accept_terms", "consent_personal_data") if not getattr(body, f)]
    if missing:
        raise ApiError("legal_consent_required", 422, details={"fields": missing})
    u = User(email=body.email.lower(), password_hash=hash_password(body.password), display_name=body.display_name,
             locale=loc)
    db.add(u)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ApiError("email_taken", 409)
    db.add(RoleGrant(user_id=u.id, role="user"))
    if body.as_developer:
        db.add(RoleGrant(user_id=u.id, role="developer"))
    audit(db, u.id, "user.registered", "user", u.id, {"developer": body.as_developer})
    from .about import legal_version
    accepted = {"terms": legal_version("terms"), "privacy": legal_version("privacy"),
                "consent-personal-data": legal_version("consent-personal-data")}
    db.add(Consent(user_id=u.id, kind="personal_data", retention_days=0))
    audit(db, u.id, "legal.accepted", "user", u.id, {"documents": accepted, "locale": loc, "at": "registration"})
    token, sess = create_session(db, u)
    db.commit()
    set_session_cookies(response, token, sess.csrf_token)
    response.set_cookie("nap_locale", loc, max_age=3600 * 24 * 365, samesite="lax", path="/")
    return {"id": str(u.id), "locale": loc, "roles": ["user"] + (["developer"] if body.as_developer else []),
            "csrf_token": sess.csrf_token}


@router.post("/auth/login")
def login(body: LoginIn, response: Response, db=DB):
    email = body.email.lower()
    check_login_rate(db, email)
    u = db.scalar(select(User).where(User.email == email, User.deleted_at.is_(None)))
    ok = verify_password(u.password_hash if u else None, body.password)
    record_login(db, email, ok)
    if not ok:
        db.commit()
        raise ApiError("invalid_credentials", 401)
    token, sess = create_session(db, u)
    db.commit()
    set_session_cookies(response, token, sess.csrf_token)
    response.set_cookie("nap_locale", u.locale, max_age=3600 * 24 * 365, samesite="lax", path="/")
    return {"id": str(u.id), "locale": u.locale, "roles": sorted(u.role_names), "csrf_token": sess.csrf_token}


@router.post("/auth/logout")
def logout(response: Response, p=P, db=DB):
    if p.session:
        p.session.revoked_at = utcnow()
        db.merge(p.session)
        db.commit()
    clear_session_cookies(response)
    return {"ok": True}


@router.get("/me")
def me(p=P):
    p.require_user()
    return me_payload(p)


class ProfileIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    locale: str | None = None


@router.patch("/me")
def update_me(body: ProfileIn, response: Response, p=P, db=DB):
    u = db.merge(p.require_user())
    if body.locale is not None:
        if body.locale not in ("ru", "en"):
            raise ApiError("validation_failed", 422, details={"field": "locale"})
        u.locale = body.locale
        response.set_cookie("nap_locale", body.locale, max_age=3600 * 24 * 365, samesite="lax", path="/")
    if body.display_name:
        u.display_name = body.display_name
    db.commit()
    return {"locale": u.locale, "display_name": u.display_name}


@router.post("/me/roles/developer")
def become_developer(p=P, db=DB):
    u = p.require_user()
    if "developer" not in p.roles:
        db.add(RoleGrant(user_id=u.id, role="developer"))
        audit(db, u.id, "role.self_granted", "user", u.id, {"role": "developer"})
        db.commit()
    return {"roles": sorted(p.roles | {"developer"})}


@router.delete("/me")
def delete_me(response: Response, p=P, db=DB):
    """Anonymize the profile; financial journal and orders are kept (no cascading deletes)."""
    u = db.merge(p.require_user())
    audit(db, u.id, "user.anonymized", "user", u.id)
    u.email = None
    u.password_hash = None
    u.display_name = "deleted user"
    u.deleted_at = utcnow()
    from ..models import Address, Measurement
    db.query(Address).filter(Address.user_id == u.id).delete()
    db.query(Measurement).filter(Measurement.user_id == u.id).delete()
    db.commit()
    clear_session_cookies(response)
    return {"ok": True}


@router.get("/me/notifications")
def notifications(p=P, db=DB):
    u = p.require_user()
    rows = db.scalars(select(Notification).where(Notification.user_id == u.id)
                      .order_by(Notification.created_at.desc()).limit(50))
    return {"items": [{"id": str(n.id), "template": n.template, "locale": n.locale, "subject": n.subject,
                       "body": n.body, "created_at": n.created_at.isoformat()} for n in rows]}
