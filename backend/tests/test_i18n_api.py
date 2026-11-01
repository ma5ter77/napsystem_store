"""Backend parts of criteria 34-38 and OpenAPI/ops checks."""
import json
from pathlib import Path

from app.i18n import catalog, format_message, missing_keys, negotiate, plural_category, t

from .conftest import Client, ok


def test_dictionaries_complete_and_plural_forms():
    assert missing_keys() == {"ru": [], "en": []}
    assert [plural_category("ru", n) for n in (1, 2, 5, 11, 21, 22, 25)] == ["one", "few", "many", "many", "one",
                                                                             "few", "many"]
    assert t("plural.sessions", "ru", count=3) == "3 сеанса"
    assert t("plural.sessions", "ru", count=5) == "5 сеансов"
    assert t("plural.sessions", "en", count=1) == "1 session"
    assert "{" not in t("errors.out_of_stock", "en", sku="X")


def test_every_error_code_used_in_code_is_translated():
    import re
    src = "\n".join(p.read_text() for p in Path(__file__).parent.parent.joinpath("app").rglob("*.py"))
    codes = set(re.findall(r'ApiError\("([a-z_]+)"', src)) | {"internal_error", "validation_failed"}
    for loc in ("ru", "en"):
        cat = catalog(loc)
        missing = sorted(c for c in codes if f"errors.{c}" not in cat)
        assert missing == [], (loc, missing)


def test_browser_language_negotiation_fallback_ru():
    assert negotiate("en-US,en;q=0.9") == "en"
    assert negotiate("de-DE,fr;q=0.8") == "ru"
    assert negotiate(None) == "ru"


def test_locale_changes_texts_not_prices_or_stock():
    ru, en = Client("ru"), Client("en")
    a_ru = ok(ru.get("/catalog/apps/quiet-harbor"))
    a_en = ok(en.get("/catalog/apps/quiet-harbor"))
    assert a_ru["id"] == a_en["id"] and a_ru["prices"] == a_en["prices"]
    assert a_ru["title"] == "Тихая гавань" and a_en["title"] == "Quiet Harbor"
    assert a_ru["content_language"] == a_en["content_language"] == "none"
    p_ru = ok(ru.get("/hardware/products/nap-studio-headphones"))
    p_en = ok(en.get("/hardware/products/nap-studio-headphones"))
    assert p_ru["skus"][0]["price_minor"] == p_en["skus"][0]["price_minor"]
    assert p_ru["skus"][0]["currency"] == p_en["skus"][0]["currency"] == "EUR"
    assert p_ru["skus"][0]["availability"] == p_en["skus"][0]["availability"]
    assert p_ru["title"] != p_en["title"]
    # audio language is independent of the UI language
    ns_ru = ok(ru.get("/catalog/apps/night-stories"))
    assert ns_ru["content_language"] == "en" and ns_ru["program"][0]["title_lang"] == "en"


def test_search_and_filters_both_languages():
    ru, en = Client("ru"), Client("en")
    assert [a["slug"] for a in ok(ru.get("/catalog/apps?q=гавань"))["items"]] == ["quiet-harbor"]
    assert [a["slug"] for a in ok(en.get("/catalog/apps?q=harbor"))["items"]] == ["quiet-harbor"]
    subs = ok(en.get("/catalog/apps?price=subscription"))["items"]
    assert all(any(p["kind"] == "subscription" for p in a["prices"]) for a in subs) and subs
    assert {a["slug"] for a in ok(ru.get("/catalog/apps?content_language=en"))["items"]} == {"night-stories"}
    assert ok(ru.get("/hardware/products?q=наушники"))["total"] >= 1
    assert ok(en.get("/hardware/products?q=headphones"))["total"] >= 1


def test_missing_translation_is_marked():
    from app.db import session_scope
    from app.models import App, Translation
    from sqlalchemy import select
    with session_scope() as db:
        app = db.scalar(select(App).where(App.slug == "night-stories"))
        tr = db.scalar(select(Translation).where(Translation.entity_id == app.id, Translation.locale == "en"))
        tr.status = "draft"
    try:
        a = ok(Client("en").get("/catalog/apps/night-stories"))
        assert a["fallback"] and a["lang"] == "ru" and a["translation_status"] == "missing"
    finally:
        with session_scope() as db:
            app = db.scalar(select(App).where(App.slug == "night-stories"))
            tr = db.scalar(select(Translation).where(Translation.entity_id == app.id, Translation.locale == "en"))
            tr.status = "published"


def test_order_locale_used_for_notifications():
    b = Client("en").register(locale="en")
    ok(b.patch("/me", {"locale": "ru"}))  # profile changes later, the order keeps its own language
    from .test_hardware import buy
    o = buy(b, [("NAP-CBL35", 1)])
    assert o["locale"] == "ru" or o["locale"] == "en"
    notes = ok(b.get("/me/notifications"))["items"]
    paid = next(n for n in notes if n["template"] == "hw_paid")
    assert paid["locale"] == o["locale"]
    assert ("оплачен" in paid["subject"]) == (o["locale"] == "ru")


def test_openapi_and_ops_endpoints():
    c = Client()
    spec = ok(c.get("/openapi.json"))
    paths = spec["paths"]
    for p in ("/api/v1/developer/conversions", "/api/v1/checkout/digital", "/api/v1/hardware/checkout",
              "/api/v1/sessions/{session_id}/commands", "/api/v1/webhooks/payments/{provider}"):
        assert p in paths
    assert ok(c.get("/health"))["status"] == "ok"
    r = c.get("/ready")
    assert r.json()["checks"]["database"] == "ok"
    m = c.raw().get("/api/v1/metrics").text
    assert "nap_webhooks_unprocessed" in m and "nap_conversion_jobs" in m
    # pagination limits
    assert c.get("/catalog/apps?limit=1000").status_code == 422


def test_version_endpoint_matches_repository_release():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    repo = json.loads((root / "release.json").read_text())
    c = Client()
    v = ok(c.get("/version"))
    assert v["version"] == repo["version"] and v["spec"]["version"] == repo["spec"]["version"]
    assert v == ok(c.get("/version")) and "spec-1.0" in v["documents"]
    # served documents are byte-identical to the versioned repository files (run ops/release.py after edits)
    assert c.get("/about/documents/spec-1.0").text == (root / "docs/spec/TZ-v1.0.md").read_text()
    assert c.get("/about/documents/changelog").text == (root / "CHANGELOG.md").read_text()
    assert c.get("/about/documents/project").text == (root / "docs/PROJECT.md").read_text()
    assert f"## [{repo['version']}]" in (root / "CHANGELOG.md").read_text()
    assert c.get("/about/documents/nope").status_code == 404
    assert ok(c.get("/openapi.json"))["info"]["version"] == repo["version"]


def test_plural_exact_number_wins_over_category():
    """NAP-045: ICU "=1 {...}" — 1 gets its own wording, 21 (also category "one") gets the plural form."""
    from app.i18n import format_message
    tpl = "{count, plural, =1 {один продавец} one {# заказ} few {# заказа} many {# заказов} other {# заказа}}"
    assert [format_message(tpl, "ru", {"count": n}) for n in (1, 2, 5, 21)] == \
        ["один продавец", "2 заказа", "5 заказов", "21 заказ"]
