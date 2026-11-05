"""NAP-118: service news and posts about them to the Telegram channel.

News come from four places: the administrator's announcements, the service's change log (every "### …" part of a
release or of an update already on the server), apps that went on sale and supplier products that went on sale.
For any news item the administrator gets a draft post in one click: a short plain explanation written by a language
model (Claude API) when a key is set on the server, otherwise from a template. The administrator edits it, sees it as
it will look in the channel, and publishes it now or at a set time. Sending is idempotent: a post is sent once.

Secrets (bot token, channel, model key) live only in the server's .env; the API reports whether they are set, never
their values.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import session_scope, utcnow
from ..errors import ApiError
from ..models import App, AppVersion, NewsItem, Product, TelegramPost, User
from .common import audit, get_translations

log = logging.getLogger("nap.news")

TRANSPORT: httpx.BaseTransport | None = None   # tests put a stand-in for Telegram / the model here
TEXT_MAX = 4096          # Telegram message
CAPTION_MAX = 1024       # Telegram photo caption
FEED_DAYS = 120


# ------------------------------------------------------------------ configuration (no secret values leave the server)

def status() -> dict:
    s = get_settings()
    return {"bot": bool(s.telegram_bot_token), "channel": s.telegram_channel or None,
            "model": bool(s.anthropic_api_key), "model_name": s.anthropic_model if s.anthropic_api_key else None,
            "ready": bool(s.telegram_bot_token and s.telegram_channel)}


# ------------------------------------------------------------------ feed

GENERIC = re.compile(r"^(Добавлено|Изменено|Исправлено|Удалено|Безопасность|Устарело|Added|Changed|Fixed|Removed|Security|Deprecated)\b", re.I)


def _changelog_items() -> list[dict]:
    from ..api.about import DATA
    path = DATA / "CHANGELOG.md"
    if not path.exists():
        return []
    items, section, date = [], None, None
    cur = None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^## \[([^\]]+)\](?:\s*—\s*(\S+))?", line)
        if m:
            section = m.group(1)
            d = (m.group(2) or "").strip()
            date = d if re.match(r"^\d{4}-\d{2}-\d{2}$", d) else None
            branch = re.search(r"ветка\s+(\S+)", line)
            section = section if section[0].isdigit() else (branch.group(1) if branch else section)
            cur = None
            continue
        h = re.match(r"^### (.+)$", line)
        if h and section and GENERIC.match(h.group(1)):
            cur = None                       # "Added / Fixed …" groups are for developers, not news
            continue
        if h and section:
            title = re.sub(r"\s*\((?:NAP-\d+[^)]*)\)\s*$", "", h.group(1)).strip()
            key = "change:" + hashlib.sha1(f"{section}|{h.group(1)}".encode()).hexdigest()[:16]
            cur = {"key": key, "kind": "change", "title": title, "body": "", "link": None, "at": date,
                   "version": section if section[0].isdigit() else None}
            items.append(cur)
            continue
        if cur is not None and line.strip():
            cur["body"] += (line.strip().lstrip("-• ").strip().replace("`", "") + "\n")
    return items


def _app_title(db: Session, app: App, v: AppVersion, locale: str) -> str:
    m = v.metadata_ or {}
    return (m.get(locale) or m.get("ru") or m.get("en") or {}).get("title") or app.slug


def feed(db: Session, locale: str) -> list[dict]:
    s = get_settings()
    since = utcnow() - timedelta(days=FEED_DAYS)
    out: list[dict] = []
    for n in db.scalars(select(NewsItem).order_by(NewsItem.created_at.desc()).limit(100)):
        out.append({"key": f"news:{n.id}", "kind": "announcement", "title": n.title, "body": n.body,
                    "link": n.link, "at": n.created_at.isoformat()})
    for app in db.scalars(select(App).where(App.published_version_id.is_not(None), App.deleted_at.is_(None))):
        v = db.get(AppVersion, app.published_version_id)
        if not v or not v.published_at or v.published_at < since:
            continue
        m = (v.metadata_ or {}).get(locale) or (v.metadata_ or {}).get("ru") or {}
        out.append({"key": f"app:{app.id}:{v.number}", "kind": "app" if v.number == 1 else "app_update",
                    "title": _app_title(db, app, v, locale), "body": "\n".join(x for x in (m.get("subtitle"), m.get("description")) if x),
                    "link": f"{s.public_base_url}/{locale}/apps/{app.slug}", "at": v.published_at.isoformat(),
                    "version": v.version_label})
    prods = list(db.scalars(select(Product).where(Product.status == "published", Product.approved_at.is_not(None),
                                                  Product.approved_at >= since)))
    trs = get_translations(db, "product", [p.id for p in prods])
    for p in prods:
        t = trs.get(p.id, {})
        d = (t.get(locale) or t.get("ru") or t.get("en"))
        data = d.data if d else {}
        out.append({"key": f"product:{p.id}", "kind": "product", "title": data.get("title") or f"{p.brand} {p.model}",
                    "body": "\n".join(x for x in (data.get("summary"), data.get("description")) if x),
                    "link": f"{s.public_base_url}/{locale}/devices/{p.slug}", "at": p.approved_at.isoformat()})
    out += _changelog_items()
    posts: dict[str, TelegramPost] = {}
    for x in db.scalars(select(TelegramPost).order_by(TelegramPost.created_at)):
        posts[x.source_key] = x               # the latest post per news item
    for it in out:
        p = posts.get(it["key"])
        it["post"] = post_view(p) if p else None
    # news with a date first (newest first), then the change log in its own order (newest first in the file)
    dated = sorted([x for x in out if x.get("at")], key=lambda x: x["at"], reverse=True)
    return dated + [x for x in out if not x.get("at")]


def source(db: Session, key: str, locale: str) -> dict:
    for it in feed(db, locale):
        if it["key"] == key:
            return it
    raise ApiError("not_found", 404, entity="news")


def add_announcement(db: Session, user: User, title: str, body: str, link: str | None) -> NewsItem:
    title, body = (title or "").strip(), (body or "").strip()
    if not title or len(title) > 200 or len(body) > 5000:
        raise ApiError("validation_failed", 422, details={"fields": ["title" if not title or len(title) > 200 else "body"]})
    if link and not re.match(r"^https://", link):
        raise ApiError("validation_failed", 422, details={"fields": ["link"]})
    n = NewsItem(title=title, body=body, link=link or None, created_by=user.id)
    db.add(n)
    db.flush()
    audit(db, user.id, "news.created", "news", n.id)
    return n


# ------------------------------------------------------------------ the explanation

PROMPT = {
    "ru": ("Ты пишешь короткие посты для Telegram-канала сервиса NAP Store — магазина аудиопрограмм для наушников, "
           "колонок и кресла NAP. По тексту новости напиши пост для обычных читателей: что изменилось и чем это "
           "полезно. 3–6 коротких предложений, простым языком, без технических терминов, без списков, хэштегов и "
           "эмодзи. Никаких медицинских обещаний: не обещай улучшения здоровья, сна, настроения или состояния "
           "мозга. Не выдумывай того, чего нет в новости. Верни только текст поста."),
    "en": ("You write short posts for the Telegram channel of NAP Store, a store of audio programs for headphones, "
           "speakers and the NAP chair. From the news text, write a post for ordinary readers: what changed and why "
           "it is useful. 3–6 short sentences, plain language, no technical terms, no lists, hashtags or emoji. No "
           "medical promises: do not promise better health, sleep, mood or brain states. Do not invent anything that "
           "is not in the news. Return only the post text."),
}

TEMPLATE = {
    "ru": {"announcement": "{title}\n\n{body}", "change": "Обновление NAP Store: {title}.\n\n{body}",
           "app": "Новое в магазине: «{title}».\n\n{body}", "app_update": "Обновлено приложение «{title}».\n\n{body}",
           "product": "Новое оборудование в магазине: {title}.\n\n{body}"},
    "en": {"announcement": "{title}\n\n{body}", "change": "NAP Store update: {title}.\n\n{body}",
           "app": "New in the store: “{title}”.\n\n{body}", "app_update": "Updated app: “{title}”.\n\n{body}",
           "product": "New equipment in the store: {title}.\n\n{body}"},
}


def _template(item: dict, lang: str) -> str:
    body = " ".join(item.get("body", "").split("\n")).strip()
    sentences = re.split(r"(?<=[.!?])\s+", body)
    body = " ".join(sentences[:4]).strip()
    return TEMPLATE[lang][item["kind"]].format(title=item["title"].rstrip("."), body=body).strip()


def _model_text(item: dict, lang: str, transport: httpx.BaseTransport | None = None) -> str:
    s = get_settings()
    news = f"{item['title']}\n\n{item.get('body', '')}".strip()[:6000]
    with httpx.Client(base_url=s.anthropic_api_base, timeout=40, transport=transport or TRANSPORT) as c:
        r = c.post("/v1/messages", headers={"x-api-key": s.anthropic_api_key or "", "anthropic-version": "2023-06-01",
                                             "content-type": "application/json"},
                   json={"model": s.anthropic_model, "max_tokens": 600, "system": PROMPT[lang],
                         "messages": [{"role": "user", "content": news}]})
    if r.status_code >= 400:
        raise ApiError("model_failed", 502, details={"status": r.status_code})
    parts = [b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text"]
    text = "\n".join(parts).strip()
    if not text:
        raise ApiError("model_failed", 502, details={"status": r.status_code})
    return text


def draft(db: Session, user: User, key: str, lang: str, transport: httpx.BaseTransport | None = None) -> TelegramPost:
    if lang not in ("ru", "en"):
        raise ApiError("validation_failed", 422, details={"fields": ["language"]})
    item = source(db, key, lang)
    by, note = "template", None
    if get_settings().anthropic_api_key:
        try:
            text, by = _model_text(item, lang, transport), "model"
        except (ApiError, httpx.HTTPError) as e:     # the template still gives a draft; the reason is shown
            log.warning("news %s: model failed: %s", key, e)
            text, note = _template(item, lang), "model_failed"
    else:
        text = _template(item, lang)
    p = TelegramPost(source_key=key, source_title=item["title"][:300], text=text[:TEXT_MAX], link_url=item.get("link"),
                     language=lang, generated_by=by, created_by=user.id, error_code=note)
    db.add(p)
    db.flush()
    audit(db, user.id, "telegram.draft", "telegram_post", p.id, {"source": key, "by": by})
    return p


# ------------------------------------------------------------------ editing and publishing

def _check(text: str, photo: str | None, link: str | None) -> None:
    bad = []
    full = _message(text, link)
    if not text.strip():
        bad.append("text")
    elif photo and len(full) > CAPTION_MAX:
        bad.append("text")
    elif len(full) > TEXT_MAX:
        bad.append("text")
    if photo and not re.match(r"^https://", photo):
        bad.append("photo_url")
    if link and not re.match(r"^https://", link):
        bad.append("link_url")
    if bad:
        raise ApiError("validation_failed", 422, details={"fields": bad, "max": CAPTION_MAX if photo else TEXT_MAX})


def _message(text: str, link: str | None) -> str:
    """Exactly what the channel shows: the text, then the link on its own line."""
    text = text.strip()
    return f"{text}\n\n{link}" if link and link not in text else text


def update(db: Session, user: User, p: TelegramPost, d: dict) -> TelegramPost:
    if p.status in ("sending", "sent"):
        raise ApiError("post_already_sent", 409)
    text = d.get("text", p.text) or ""
    photo = (d.get("photo_url", p.photo_url) or "").strip() or None
    link = (d.get("link_url", p.link_url) or "").strip() or None
    _check(text, photo, link)
    if d.get("text") is not None and text.strip() != (p.text or "").strip():
        p.generated_by = "manual"          # the administrator rewrote it
    p.text, p.photo_url, p.link_url = text.strip(), photo, link
    if "scheduled_at" in d:
        when = d.get("scheduled_at")
        if when:
            at = datetime.fromisoformat(str(when).replace("Z", "+00:00"))
            if at.tzinfo is None or at <= utcnow():
                raise ApiError("validation_failed", 422, details={"fields": ["scheduled_at"]})
            p.scheduled_at, p.status = at, "scheduled"
        else:
            p.scheduled_at, p.status = None, "draft"
    audit(db, user.id, "telegram.edited", "telegram_post", p.id)
    return p


TG_ERRORS = {  # Telegram's answers -> a reason the administrator can act on
    401: "telegram_bad_token", 403: "telegram_no_rights", 404: "telegram_bad_token", 429: "telegram_rate_limited",
}


def _tg_error(status: int, desc: str) -> str:
    d = (desc or "").lower()
    if "chat not found" in d or "chat_id is empty" in d:
        return "telegram_channel_not_found"
    if "not enough rights" in d or "bot is not a member" in d or "need administrator rights" in d:
        return "telegram_no_rights"
    if "wrong file identifier" in d or "failed to get http url content" in d or "wrong type of the web page content" in d:
        return "telegram_bad_photo"
    if "message is too long" in d or "caption is too long" in d:
        return "telegram_too_long"
    return TG_ERRORS.get(status, "telegram_failed")


def _send(p: TelegramPost, transport: httpx.BaseTransport | None = None) -> tuple[str, str | None]:
    s = get_settings()
    msg = _message(p.text, p.link_url)
    method, body = ("sendPhoto", {"chat_id": s.telegram_channel, "photo": p.photo_url, "caption": msg}) if p.photo_url \
        else ("sendMessage", {"chat_id": s.telegram_channel, "text": msg, "link_preview_options": {"is_disabled": False}})
    with httpx.Client(base_url=s.telegram_api_base, timeout=30, transport=transport or TRANSPORT) as c:
        r = c.post(f"/bot{s.telegram_bot_token}/{method}", json=body)
    try:
        data = r.json()
    except json.JSONDecodeError:
        data = {}
    if r.status_code >= 400 or not data.get("ok"):
        raise ApiError(_tg_error(r.status_code, data.get("description", "")), 502,
                       details={"status": r.status_code, "description": (data.get("description") or "")[:300]})
    res = data.get("result") or {}
    mid = str(res.get("message_id") or "")
    chat = res.get("chat") or {}
    url = f"https://t.me/{chat['username']}/{mid}" if chat.get("username") and mid else None
    return mid, url


def publish(db: Session, user: User | None, post_id: uuid.UUID, transport: httpx.BaseTransport | None = None,
            force_now: bool = False) -> TelegramPost:
    """Sends the post once. A second call (double click, retry, the scheduler) returns the post as it is."""
    st = status()
    if not st["ready"]:
        raise ApiError("telegram_not_configured", 409, details={"missing": [k for k in ("bot", "channel") if not st[k]]})
    p = db.get(TelegramPost, post_id, with_for_update=True)
    if not p:
        raise ApiError("not_found", 404, entity="telegram_post")
    if p.status in ("sending", "sent"):
        return p
    if p.status == "scheduled" and p.scheduled_at and p.scheduled_at > utcnow() and not force_now:
        return p
    _check(p.text, p.photo_url, p.link_url)
    p.status, p.error_code, p.error_detail = "sending", None, None
    if user is not None:
        p.sent_by = user.id
    db.commit()                                   # "sending" is visible to any second request before the call
    p = db.get(TelegramPost, post_id, with_for_update=True)
    try:
        mid, url = _send(p, transport)
    except ApiError as e:
        p.status, p.error_code = "failed", e.code
        p.error_detail = (e.details or {}).get("description") or None
        audit(db, user.id if user else None, "telegram.failed", "telegram_post", p.id, {"code": e.code})
        db.commit()
        return p
    except httpx.HTTPError as e:
        p.status, p.error_code, p.error_detail = "failed", "telegram_unreachable", type(e).__name__
        db.commit()
        return p
    p.status, p.sent_at, p.message_id, p.message_url = "sent", utcnow(), mid, url
    audit(db, user.id if user else None, "telegram.sent", "telegram_post", p.id, {"message_id": mid})
    db.commit()
    return p


def send_due(transport: httpx.BaseTransport | None = None) -> int:
    """Scheduler: posts whose time has come."""
    if not status()["ready"]:
        return 0
    with session_scope() as db:
        ids = [x for x in db.scalars(select(TelegramPost.id).where(TelegramPost.status == "scheduled",
                                                                   TelegramPost.scheduled_at <= utcnow()))]
    n = 0
    for pid in ids:
        with session_scope() as db:
            p = publish(db, None, pid, transport, force_now=True)
            n += p.status == "sent"
    return n


def post_view(p: TelegramPost) -> dict:
    return {"id": str(p.id), "source_key": p.source_key, "source_title": p.source_title, "text": p.text,
            "photo_url": p.photo_url, "link_url": p.link_url, "language": p.language, "generated_by": p.generated_by,
            "status": p.status, "scheduled_at": p.scheduled_at.isoformat() if p.scheduled_at else None,
            "sent_at": p.sent_at.isoformat() if p.sent_at else None, "message_url": p.message_url,
            "error_code": p.error_code, "error_detail": p.error_detail, "message": _message(p.text, p.link_url),
            "limit": CAPTION_MAX if p.photo_url else TEXT_MAX, "created_at": p.created_at.isoformat()}
