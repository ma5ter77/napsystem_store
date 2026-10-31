"""Cross-cutting helpers: audit, localized notifications, translations, pagination, idempotency."""
from __future__ import annotations

import uuid
from contextvars import ContextVar
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..i18n import t
from ..models import AuditLog, Notification, Outbox, Translation, User

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

SENSITIVE_KEYS = {"password", "token", "secret", "address", "phone", "url", "answers", "card"}


def _scrub(data: dict) -> dict:
    out = {}
    for k, v in (data or {}).items():
        if any(s in k.lower() for s in SENSITIVE_KEYS):
            out[k] = "[redacted]"
        elif isinstance(v, dict):
            out[k] = _scrub(v)
        else:
            out[k] = v if isinstance(v, (str, int, float, bool, type(None), list)) else str(v)
    return out


def audit(db: Session, actor_id: uuid.UUID | None, action: str, entity_type: str, entity_id: Any,
          data: dict | None = None) -> None:
    db.add(AuditLog(actor_id=actor_id, action=action, entity_type=entity_type,
                    entity_id=str(entity_id) if entity_id is not None else None,
                    data=_scrub(data or {}), request_id=request_id_var.get()))


def notify(db: Session, user_id: uuid.UUID | None, locale: str, template: str, dedupe_key: str, **params) -> None:
    """Render a localized notification in the order/user locale and queue it for delivery once."""
    if user_id is None:
        return
    # NAP-084: a content editor may have rewritten this e-mail template
    from ..i18n import format_message
    from .site_texts import published_map
    over = published_map(db, locale)
    sk, bk = f"notifications.{template}.subject", f"notifications.{template}.body"
    subject = format_message(over[sk], locale, params) if sk in over else t(sk, locale, **params)
    body = format_message(over[bk], locale, params) if bk in over else t(bk, locale, **params)
    stmt = insert(Notification).values(id=uuid.uuid4(), user_id=user_id, locale=locale, template=template,
                                       subject=subject, body=body, dedupe_key=dedupe_key)
    db.execute(stmt.on_conflict_do_nothing(index_elements=["dedupe_key"]))
    enqueue(db, "notification.email", {"user_id": str(user_id), "template": template, "dedupe_key": dedupe_key},
            f"mail:{dedupe_key}")


def enqueue(db: Session, topic: str, payload: dict, dedupe_key: str) -> None:
    stmt = insert(Outbox).values(id=uuid.uuid4(), topic=topic, payload=payload, dedupe_key=dedupe_key)
    db.execute(stmt.on_conflict_do_nothing(index_elements=["dedupe_key"]))


def get_translations(db: Session, entity_type: str, ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, Translation]]:
    out: dict[uuid.UUID, dict[str, Translation]] = {}
    if not ids:
        return out
    for tr in db.scalars(select(Translation).where(Translation.entity_type == entity_type,
                                                   Translation.entity_id.in_(ids))):
        out.setdefault(tr.entity_id, {})[tr.locale] = tr
    return out


def localized(trs: dict[str, Translation] | None, locale: str, public: bool = True) -> dict:
    """Pick the published translation for locale; otherwise show the original with an explicit language mark."""
    trs = trs or {}
    tr = trs.get(locale)
    if tr and (tr.status == "published" or not public):
        return {**tr.data, "lang": locale, "translation_status": tr.status, "fallback": False}
    for other in ("ru", "en"):
        o = trs.get(other)
        if o and (o.status == "published" or not public):
            return {**o.data, "lang": other, "translation_status": "missing", "fallback": True}
    return {"lang": None, "translation_status": "missing", "fallback": True}


def upsert_translation(db: Session, entity_type: str, entity_id: uuid.UUID, locale: str, data: dict,
                       status: str = "draft") -> None:
    stmt = insert(Translation).values(id=uuid.uuid4(), entity_type=entity_type, entity_id=entity_id,
                                      locale=locale, data=data, status=status)
    db.execute(stmt.on_conflict_do_update(index_elements=["entity_type", "entity_id", "locale"],
                                          set_={"data": data, "status": status}))


def page_params(limit: int | None, offset: int | None) -> tuple[int, int]:
    lim = max(1, min(int(limit or 20), 100))
    off = max(0, int(offset or 0))
    return lim, off


def user_locale(db: Session, user_id: uuid.UUID) -> str:
    u = db.get(User, user_id)
    return u.locale if u else "ru"
