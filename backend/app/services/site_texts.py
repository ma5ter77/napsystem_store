"""Site content editing (NAP-084, role content_editor).

Any UI text (frontend dictionary key, e.g. `home.title`) or e-mail template (`notifications.<template>.subject|body`)
can be overridden per language: edit a draft -> publish -> the site / e-mails use it; every publication is kept as a
version, so the editor can return to any earlier text or back to the built-in one. Categories (names RU/EN, order,
new categories) are edited here too.
"""
from __future__ import annotations

import re
import time
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..errors import ApiError
from ..models import App, Category, Product, SiteText, SiteTextVersion, User
from .common import audit

KEY_RE = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_-]+){1,5}$")
MAX_LEN = 5000
_cache: dict[str, tuple[float, dict[str, str]]] = {}
CACHE_SECONDS = 10


def _check(key: str, locale: str) -> None:
    if not KEY_RE.match(key) or len(key) > 160:
        raise ApiError("validation_failed", 422, details={"fields": ["key"]})
    if locale not in ("ru", "en"):
        raise ApiError("validation_failed", 422, details={"fields": ["locale"]})


def published_map(db: Session, locale: str) -> dict[str, str]:
    """Published overrides for one language (cached briefly: read on every page load and e-mail)."""
    hit = _cache.get(locale)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    out = {k: v for k, v in db.execute(select(SiteText.key, SiteText.published)
                                       .where(SiteText.locale == locale, SiteText.published.isnot(None)))}
    _cache[locale] = (time.monotonic(), out)
    return out


def _invalidate() -> None:
    _cache.clear()


def list_texts(db: Session) -> list[dict]:
    rows = db.scalars(select(SiteText).order_by(SiteText.key, SiteText.locale))
    names = {u.id: u.display_name for u in db.scalars(select(User).where(
        User.id.in_(select(SiteText.updated_by).distinct())))}
    return [{"key": r.key, "locale": r.locale, "draft": r.draft, "published": r.published,
             "updated_at": r.updated_at.isoformat(), "updated_by": names.get(r.updated_by)} for r in rows]


def _row(db: Session, key: str, locale: str, create: bool = True) -> SiteText | None:
    r = db.scalar(select(SiteText).where(SiteText.key == key, SiteText.locale == locale).with_for_update())
    if r is None and create:
        r = SiteText(key=key, locale=locale)
        db.add(r)
    return r


def save_draft(db: Session, user: User, key: str, locale: str, value: str) -> SiteText:
    _check(key, locale)
    value = value if value is not None else ""
    if len(value) > MAX_LEN or not value.strip():
        raise ApiError("validation_failed", 422, details={"fields": ["value"]})
    r = _row(db, key, locale)
    r.draft, r.updated_by, r.updated_at = value, user.id, utcnow()
    audit(db, user.id, "site_text.draft", "site_text", f"{key}:{locale}")
    return r


def discard_draft(db: Session, user: User, key: str, locale: str) -> None:
    _check(key, locale)
    r = _row(db, key, locale, create=False)
    if r is None or r.draft is None:
        raise ApiError("invalid_state", 409)
    r.draft = None
    if r.published is None:
        db.delete(r)
    audit(db, user.id, "site_text.discard", "site_text", f"{key}:{locale}")


def _publish_value(db: Session, user: User, key: str, locale: str, value: str | None, note: str) -> SiteText:
    r = _row(db, key, locale)
    r.published, r.draft, r.updated_by, r.updated_at = value, None, user.id, utcnow()
    db.add(SiteTextVersion(key=key, locale=locale, value=value, published_by=user.id, note=note))
    audit(db, user.id, f"site_text.{note}", "site_text", f"{key}:{locale}")
    _invalidate()
    return r


def publish(db: Session, user: User, key: str, locale: str) -> SiteText:
    _check(key, locale)
    r = _row(db, key, locale, create=False)
    if r is None or r.draft is None:
        raise ApiError("invalid_state", 409)
    return _publish_value(db, user, key, locale, r.draft, "publish")


def reset(db: Session, user: User, key: str, locale: str) -> SiteText:
    """Back to the built-in text (kept in history, so it can be undone)."""
    _check(key, locale)
    r = _row(db, key, locale, create=False)
    if r is None or r.published is None:
        raise ApiError("invalid_state", 409)
    return _publish_value(db, user, key, locale, None, "reset")


def history(db: Session, key: str, locale: str) -> list[dict]:
    _check(key, locale)
    rows = list(db.scalars(select(SiteTextVersion).where(SiteTextVersion.key == key, SiteTextVersion.locale == locale)
                           .order_by(SiteTextVersion.published_at.desc()).limit(50)))
    names = {u.id: u.display_name for u in db.scalars(select(User).where(User.id.in_({r.published_by for r in rows})))}
    return [{"id": str(r.id), "value": r.value, "note": r.note, "at": r.published_at.isoformat(),
             "by": names.get(r.published_by)} for r in rows]


def revert(db: Session, user: User, key: str, locale: str, version_id: uuid.UUID) -> SiteText:
    _check(key, locale)
    v = db.get(SiteTextVersion, version_id)
    if v is None or v.key != key or v.locale != locale:
        raise ApiError("not_found", 404, entity="version")
    return _publish_value(db, user, key, locale, v.value, "revert")


# ------------------------------------------------------------------ categories

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


def categories(db: Session, kind: str) -> list[dict]:
    used_apps = dict(db.execute(select(App.category_id, func.count()).group_by(App.category_id)).all())
    used_products = dict(db.execute(select(Product.category_id, func.count()).group_by(Product.category_id)).all())
    rows = db.scalars(select(Category).where(Category.kind == kind).order_by(Category.position, Category.slug))
    return [{"id": str(c.id), "slug": c.slug, "kind": c.kind, "names": c.names, "position": c.position,
             "used": used_apps.get(c.id, 0) + used_products.get(c.id, 0),
             "missing": [loc for loc in ("ru", "en") if not (c.names or {}).get(loc)]} for c in rows]


def _names(d: dict) -> dict:
    names = {loc: (d.get("names") or {}).get(loc, "").strip() for loc in ("ru", "en")}
    if not names["ru"] and not names["en"] or any(len(v) > 80 for v in names.values()):
        raise ApiError("validation_failed", 422, details={"fields": ["names"]})
    return {k: v for k, v in names.items() if v}


def create_category(db: Session, user: User, d: dict) -> Category:
    kind, slug = d.get("kind"), (d.get("slug") or "").strip()
    if kind not in ("app", "hardware"):
        raise ApiError("validation_failed", 422, details={"fields": ["kind"]})
    if not SLUG_RE.match(slug):
        raise ApiError("validation_failed", 422, details={"fields": ["slug"]})
    if db.scalar(select(Category.id).where(Category.kind == kind, Category.slug == slug)):
        raise ApiError("slug_taken", 409, details={"fields": ["slug"]})
    pos = d.get("position")
    if not isinstance(pos, int):
        pos = (db.scalar(select(func.max(Category.position)).where(Category.kind == kind)) or 0) + 1
    c = Category(kind=kind, slug=slug, names=_names(d), position=pos)
    db.add(c)
    db.flush()
    audit(db, user.id, "category.created", "category", c.id, {"slug": slug, "kind": kind})
    return c


def update_category(db: Session, user: User, c: Category, d: dict) -> Category:
    if "names" in d:
        c.names = _names(d)
    if "position" in d:
        if not isinstance(d["position"], int):
            raise ApiError("validation_failed", 422, details={"fields": ["position"]})
        c.position = d["position"]
    audit(db, user.id, "category.updated", "category", c.id, {"fields": sorted(d.keys())})
    return c


def delete_category(db: Session, user: User, c: Category) -> None:
    used = (db.scalar(select(func.count()).select_from(App).where(App.category_id == c.id)) or 0) + \
        (db.scalar(select(func.count()).select_from(Product).where(Product.category_id == c.id)) or 0)
    if used:
        raise ApiError("category_in_use", 409, count=used)
    audit(db, user.id, "category.deleted", "category", c.id, {"slug": c.slug})
    db.delete(c)
