"""NAP-118: news -> draft post (model or template) -> edit -> publish to the Telegram channel, once."""
import json
import uuid

import httpx
import pytest

from app.config import get_settings
from app.db import session_scope
from app.services import news as svc

from .conftest import Client, ok


class FakeTelegram:
    """Telegram Bot API stand-in: records calls, answers like Telegram."""

    def __init__(self, fail: dict | None = None):
        self.calls, self.fail = [], fail

    def handler(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        self.calls.append((req.url.path, body))
        if self.fail:
            return httpx.Response(self.fail["status"], json={"ok": False, "description": self.fail["description"]})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 100 + len(self.calls),
                                                                "chat": {"username": "sdslab"}}})


@pytest.fixture
def tg(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "telegram_bot_token", "123:TEST")
    monkeypatch.setattr(s, "telegram_channel", "@sdslab")
    monkeypatch.setattr(s, "anthropic_api_key", None)
    fake = FakeTelegram()
    monkeypatch.setattr(svc, "TRANSPORT", httpx.MockTransport(fake.handler))   # every Telegram call goes to the fake
    return fake


def test_news_to_channel_once(tg):
    admin = Client().login("admin@nap.demo")
    assert Client().register().get("/admin/news").status_code == 403          # administrators only
    ok(admin.post("/admin/news", {"title": "Калибровка кресла", "body": "Теперь каждый канал можно выровнять по громкости."}), 201)
    feed = ok(admin.get("/admin/news?lang=ru"))
    assert feed["status"] == {"bot": True, "channel": "@sdslab", "model": False, "model_name": None, "ready": True}
    item = next(x for x in feed["items"] if x["kind"] == "announcement" and x["title"] == "Калибровка кресла")
    assert any(x["kind"] == "change" for x in feed["items"])                 # the change log is news too
    d = ok(admin.post("/admin/telegram-posts", {"key": item["key"], "language": "ru"}), 201)
    assert d["generated_by"] == "template" and d["status"] == "draft" and "Калибровка кресла" in d["text"]

    # editing: limits and links checked; the preview text is exactly what will be sent
    r = admin.put(f"/admin/telegram-posts/{d['id']}", {"text": "x" * 5000})
    assert r.status_code == 422 and r.json()["error"]["details"]["fields"] == ["text"]
    r = admin.put(f"/admin/telegram-posts/{d['id']}", {"link_url": "http://insecure"})
    assert r.status_code == 422
    d = ok(admin.put(f"/admin/telegram-posts/{d['id']}", {"text": "Новое в NAP Store: калибровка кресла.",
                                                          "link_url": "https://store.napsystem.com/ru/help/playback"}))
    assert d["generated_by"] == "manual"
    assert d["message"] == "Новое в NAP Store: калибровка кресла.\n\nhttps://store.napsystem.com/ru/help/playback"

    sent = ok(admin.post(f"/admin/telegram-posts/{d['id']}/publish"))
    again = ok(admin.post(f"/admin/telegram-posts/{d['id']}/publish"))       # double click: no second message
    assert sent["status"] == "sent" and again["status"] == "sent" and len(tg.calls) == 1
    path, body = tg.calls[0]
    assert path == "/bot123:TEST/sendMessage" and body["chat_id"] == "@sdslab" and body["text"] == d["message"]
    assert sent["message_url"] == "https://t.me/sdslab/101"
    assert admin.put(f"/admin/telegram-posts/{d['id']}", {"text": "late"}).status_code == 409
    feed = ok(admin.get("/admin/news?lang=ru"))
    assert next(x for x in feed["items"] if x["key"] == item["key"])["post"]["status"] == "sent"


def test_photo_schedule_errors_and_model(tg, monkeypatch):
    admin = Client().login("admin@nap.demo")
    key = ok(admin.post("/admin/news", {"title": "Фото", "body": "Текст."}), 201)["key"]
    d = ok(admin.post("/admin/telegram-posts", {"key": key}), 201)
    r = admin.put(f"/admin/telegram-posts/{d['id']}", {"photo_url": "https://x/p.jpg", "text": "y" * 1100})
    assert r.status_code == 422 and r.json()["error"]["details"]["max"] == 1024      # a caption is shorter
    ok(admin.put(f"/admin/telegram-posts/{d['id']}", {"photo_url": "https://example.org/p.jpg", "text": "Подпись"}))

    # a Telegram refusal is stored with a reason the administrator can act on
    tg.fail = {"status": 400, "description": "Bad Request: chat not found"}
    f = ok(admin.post(f"/admin/telegram-posts/{d['id']}/publish"))
    assert f["status"] == "failed" and f["error_code"] == "telegram_channel_not_found"
    tg.fail = None
    s = ok(admin.post(f"/admin/telegram-posts/{d['id']}/publish"))                 # retry after fixing
    assert s["status"] == "sent" and tg.calls[-1][0].endswith("/sendPhoto") and tg.calls[-1][1]["caption"] == "Подпись"

    # scheduled: publish does nothing early; the scheduler sends it when the time comes
    d2 = ok(admin.post("/admin/telegram-posts", {"key": key}), 201)
    assert admin.put(f"/admin/telegram-posts/{d2['id']}", {"scheduled_at": "2020-01-01T00:00:00+00:00"}).status_code == 422
    ok(admin.put(f"/admin/telegram-posts/{d2['id']}", {"scheduled_at": "2099-01-01T09:00:00+00:00"}))
    n = len(tg.calls)
    assert ok(admin.post(f"/admin/telegram-posts/{d2['id']}/publish"))["status"] == "scheduled" and len(tg.calls) == n
    with session_scope() as db:
        from datetime import timedelta
        from app.db import utcnow
        from app.models import TelegramPost
        db.get(TelegramPost, uuid.UUID(d2["id"])).scheduled_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert svc.send_due() == 1 and ok(admin.get(f"/admin/telegram-posts/{d2['id']}"))["status"] == "sent"

    # with a model key the draft is written by the model (the request carries the house rules)
    seen = []
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "sk-test")

    def h(req):
        seen.append(json.loads(req.content))
        assert req.url.path == "/v1/messages" and req.headers["x-api-key"] == "sk-test"
        return httpx.Response(200, json={"content": [{"type": "text", "text": "Короткий пост от модели."}]})
    monkeypatch.setattr(svc, "TRANSPORT", httpx.MockTransport(h))
    m = ok(admin.post("/admin/telegram-posts", {"key": key, "language": "ru"}), 201)
    assert m["generated_by"] == "model" and m["text"] == "Короткий пост от модели."
    assert "медицинских обещаний" in seen[0]["system"] and seen[0]["model"] == get_settings().anthropic_model


def test_not_configured_says_what_is_missing(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "telegram_bot_token", None)
    monkeypatch.setattr(s, "telegram_channel", None)
    admin = Client().login("admin@nap.demo")
    key = ok(admin.post("/admin/news", {"title": "Без бота"}), 201)["key"]
    d = ok(admin.post("/admin/telegram-posts", {"key": key}), 201)                # drafting works without Telegram
    r = admin.post(f"/admin/telegram-posts/{d['id']}/publish")
    assert r.status_code == 409 and r.json()["error"]["code"] == "telegram_not_configured"
    assert r.json()["error"]["details"]["missing"] == ["bot", "channel"]
