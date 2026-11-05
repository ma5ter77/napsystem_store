"""NAP-118: administrator — service news and posts to the Telegram channel."""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..errors import ApiError
from ..models import TelegramPost
from ..services import news as svc
from .deps import DB, P, as_uuid, locale_of

router = APIRouter(tags=["admin/news"])


def admin(p):
    return p.require_role("admin")


@router.get("/admin/news")
def news_feed(request: Request, lang: str | None = None, p=P, db=DB):
    admin(p)
    return {"status": svc.status(), "items": svc.feed(db, lang if lang in ("ru", "en") else locale_of(request))}


class AnnouncementIn(BaseModel):
    title: str = Field(max_length=200)
    body: str = Field(default="", max_length=5000)
    link: str | None = Field(default=None, max_length=500)


@router.post("/admin/news", status_code=201)
def add_news(body: AnnouncementIn, p=P, db=DB):
    u = admin(p)
    n = svc.add_announcement(db, u, body.title, body.body, body.link)
    db.commit()
    return {"key": f"news:{n.id}"}


class DraftIn(BaseModel):
    key: str = Field(max_length=120)
    language: str = "ru"


@router.post("/admin/telegram-posts", status_code=201)
def new_draft(body: DraftIn, p=P, db=DB):
    """One click: a draft post for a news item (model when configured, otherwise the template)."""
    u = admin(p)
    post = svc.draft(db, u, body.key, body.language)
    db.commit()
    return svc.post_view(post)


@router.get("/admin/telegram-posts")
def posts(p=P, db=DB):
    admin(p)
    rows = db.scalars(select(TelegramPost).order_by(TelegramPost.created_at.desc()).limit(200))
    return {"status": svc.status(), "items": [svc.post_view(x) for x in rows]}


def _post(db, post_id: str, lock: bool = False) -> TelegramPost:
    x = db.get(TelegramPost, as_uuid(post_id, "telegram_post"), with_for_update=lock)
    if not x:
        raise ApiError("not_found", 404, entity="telegram_post")
    return x


@router.get("/admin/telegram-posts/{post_id}")
def get_post(post_id: str, p=P, db=DB):
    admin(p)
    return svc.post_view(_post(db, post_id))


@router.put("/admin/telegram-posts/{post_id}")
def edit_post(post_id: str, body: dict, p=P, db=DB):
    u = admin(p)
    allowed = {k: body[k] for k in ("text", "photo_url", "link_url", "scheduled_at") if k in body}
    x = svc.update(db, u, _post(db, post_id, lock=True), allowed)
    db.commit()
    return svc.post_view(x)


@router.post("/admin/telegram-posts/{post_id}/publish")
def publish_post(post_id: str, p=P, db=DB):
    u = admin(p)
    x = svc.publish(db, u, _post(db, post_id).id)
    return svc.post_view(x)
