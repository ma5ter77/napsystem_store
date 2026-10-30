"""Authentication (cookie sessions + CSRF, developer API keys, device tokens) and authorization."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db, utcnow
from .errors import ApiError
from .models import ApiKey, AuthSession, Device, LoginAttempt, SellerMember, User

_ph = PasswordHasher()  # argon2id

SESSION_COOKIE = "nap_session"
CSRF_COOKIE = "nap_csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

API_SCOPES = ("apps:read", "apps:write", "assets:write", "conversions:write", "conversions:read",
              "versions:submit", "analytics:read", "hardware:read", "entitlements:read", "sessions:write")


def sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(hashed: str | None, pw: str) -> bool:
    if not hashed:
        _ph.hash(pw)  # constant-ish time
        return False
    try:
        return _ph.verify(hashed, pw)
    except VerificationError:
        return False


@dataclass
class Principal:
    user: User | None = None
    method: str = "anonymous"  # anonymous / session / api_key / device
    roles: set[str] = field(default_factory=set)
    scopes: set[str] | None = None  # None => full user permissions (session)
    device_id: uuid.UUID | None = None
    session: AuthSession | None = None

    @property
    def id(self) -> uuid.UUID | None:
        return self.user.id if self.user else None

    def has(self, role: str) -> bool:
        return role in self.roles

    def require_user(self) -> User:
        if not self.user:
            raise ApiError("auth_required", 401)
        return self.user

    def require_role(self, *roles: str) -> User:
        u = self.require_user()
        if not any(r in self.roles for r in roles):
            raise ApiError("forbidden", 403)
        return u

    def require_scope(self, scope: str) -> None:
        if self.scopes is not None and scope not in self.scopes:
            raise ApiError("scope_missing", 403, scope=scope)


def create_session(db: Session, user: User) -> tuple[str, AuthSession]:
    token = secrets.token_urlsafe(32)
    s = AuthSession(user_id=user.id, token_hash=sha256_hex(token), csrf_token=secrets.token_urlsafe(24),
                    expires_at=utcnow() + timedelta(hours=get_settings().session_ttl_hours))
    db.add(s)
    return token, s


def set_session_cookies(response, token: str, csrf: str) -> None:
    s = get_settings()
    max_age = s.session_ttl_hours * 3600
    response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=s.cookie_secure, samesite="lax",
                        max_age=max_age, path="/")
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, secure=s.cookie_secure, samesite="lax",
                        max_age=max_age, path="/")


def clear_session_cookies(response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


def check_login_rate(db: Session, email: str) -> None:
    s = get_settings()
    since = utcnow() - timedelta(seconds=s.login_window_seconds)
    key = sha256_hex(email.lower())
    fails = db.scalar(select(func.count()).select_from(LoginAttempt).where(
        LoginAttempt.key == key, LoginAttempt.success.is_(False), LoginAttempt.created_at >= since))
    if fails >= s.login_max_attempts:
        raise ApiError("too_many_attempts", 429)


def record_login(db: Session, email: str, ok: bool) -> None:
    db.add(LoginAttempt(key=sha256_hex(email.lower()), success=ok))


def generate_api_key() -> tuple[str, str, str]:
    prefix = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    full = f"napk_{prefix}_{secret}"
    return full, prefix, sha256_hex(full)


def generate_device_token() -> tuple[str, str]:
    tok = "napd_" + secrets.token_urlsafe(32)
    return tok, sha256_hex(tok)


def _load_roles(db: Session, user: User) -> set[str]:
    return set(user.role_names)


def get_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    if hasattr(request.state, "principal"):
        return request.state.principal
    p = Principal()
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer napk_"):
        key = db.scalar(select(ApiKey).where(ApiKey.key_hash == sha256_hex(auth[7:].strip())))
        if not key or key.revoked_at is not None:
            raise ApiError("invalid_api_key", 401)
        user = db.get(User, key.user_id)
        if not user or user.deleted_at:
            raise ApiError("invalid_api_key", 401)
        key.last_used_at = utcnow()
        db.commit()
        roles = _load_roles(db, user)
        # developer keys never carry admin/moderator powers
        p = Principal(user=user, method="api_key", roles=roles & {"developer", "user"}, scopes=set(key.scopes))
    elif auth.startswith("Device "):
        dev = db.scalar(select(Device).where(Device.token_hash == sha256_hex(auth[7:].strip())))
        if not dev or dev.status != "active":
            raise ApiError("invalid_device_token", 401)
        user = db.get(User, dev.owner_id)
        p = Principal(user=user, method="device", roles={"device"}, scopes={"sessions:device"},
                      device_id=dev.id)
    else:
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            sess = db.scalar(select(AuthSession).where(AuthSession.token_hash == sha256_hex(token)))
            if sess and sess.revoked_at is None and sess.expires_at > utcnow():
                user = db.get(User, sess.user_id)
                if user and not user.deleted_at:
                    if request.method not in SAFE_METHODS:
                        sent = request.headers.get("x-csrf-token", "")
                        if not hmac.compare_digest(sent, sess.csrf_token):
                            raise ApiError("csrf_failed", 403)
                    p = Principal(user=user, method="session", roles=_load_roles(db, user), session=sess)
    request.state.principal = p
    if p.user is not None:
        request.state.user_id = str(p.user.id)
    return p


def seller_ids_for(db: Session, user_id: uuid.UUID, roles: tuple[str, ...] = ("seller", "fulfillment")) -> set[uuid.UUID]:
    rows = db.scalars(select(SellerMember.seller_id).where(SellerMember.user_id == user_id,
                                                           SellerMember.role.in_(roles)))
    return set(rows)
