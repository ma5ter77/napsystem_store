"""NAP-052: rate limits on the whole API and on sensitive actions; 429 with a localized text and Retry-After."""
import pytest

from app import ratelimit
from app.config import get_settings

from .conftest import Client, ok


@pytest.fixture
def limits():
    s = get_settings()
    old = (s.rate_limits_enabled, s.rate_limits)
    s.rate_limits_enabled = True
    ratelimit.reset_local()
    yield s
    s.rate_limits_enabled, s.rate_limits = old
    ratelimit.reset_local()


def test_checkout_limit_429_localized_with_retry_after(limits):
    limits.rate_limits = "checkout=2/600"
    for loc, word in (("ru", "Слишком много запросов"), ("en", "Too many requests")):
        ratelimit.reset_local()
        b = Client(loc).register(locale=loc)
        app = ok(b.get("/catalog/apps/quiet-harbor"))
        for _ in range(2):
            assert b.post("/checkout/digital", {"app_id": app["id"], "kind": "one_time"}).status_code in (200, 201)
        r = b.post("/checkout/digital", {"app_id": app["id"], "kind": "one_time"})
        assert r.status_code == 429
        e = r.json()["error"]
        assert e["code"] == "rate_limited" and word in e["message"] and "{" not in e["message"]
        assert int(r.headers["retry-after"]) > 0 and e["details"]["bucket"] == "checkout"
        # limits are per user: another account is not affected
        other = Client(loc).register(locale=loc)
        assert other.post("/checkout/digital", {"app_id": app["id"], "kind": "one_time"}).status_code == 201


def test_global_limit_and_exempt_health(limits):
    limits.rate_limits = "global=5/60"
    c = Client()
    codes = [c.get("/catalog/apps?limit=1").status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]
    assert all(c.get("/health").status_code == 200 for _ in range(10))  # monitoring is never limited


def test_upload_tickets_and_webhooks_limited(limits):
    limits.rate_limits = "upload_ticket=1/3600,webhook=2/60"
    dev = Client().register(developer=True)
    a = ok(dev.post("/developer/apps", {"slug": "rl-" + dev.user_id[:8], "author_name": "A",
                                        "metadata": {"ru": {"title": "Лимиты"}}}), 201)
    assert dev.post(f"/developer/apps/{a['id']}/uploads", {"filename": "a.wav", "size_bytes": 10}).status_code == 201
    assert dev.post(f"/developer/apps/{a['id']}/uploads", {"filename": "a.wav", "size_bytes": 10}).status_code == 429
    c = Client()
    codes = [c.raw().post("/api/v1/webhooks/payments/mock", content=b"{}").status_code for _ in range(3)]
    assert codes[2] == 429 and 429 not in codes[:2]
