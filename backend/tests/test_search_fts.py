"""NAP-028: full-text search with RU/EN morphology."""
from .conftest import Client, ok


def _slugs(c, q, **kw):
    params = "&".join([f"q={q}"] + [f"{k}={v}" for k, v in kw.items()])
    return [x["slug"] for x in ok(c.get(f"/catalog/apps?{params}&limit=100"))["items"]]


def test_russian_and_english_morphology():
    c = Client()
    # «медитации» (plural/genitive) finds the subtitle «Медитация с бинауральным слоем»
    assert "quiet-harbor" in _slugs(c, "медитации")
    # case and English inflection: «course», «COURSES» find «Focus Course»
    assert "focus-course" in _slugs(c, "course")
    assert "focus-course" in _slugs(c, "COURSES")
    # Russian stem across forms: «курсы» → «курс»
    assert "focus-course" in _slugs(c, "курсы")


def test_words_are_and_prefix_and_mixed_languages():
    c = Client()
    assert "quiet-harbor" in _slugs(c, "медит")  # beginning of a word
    assert "quiet-harbor" in _slugs(c, "медитация бинауральный")  # every word must match
    assert "quiet-harbor" not in _slugs(c, "медитация космолёт")
    assert "focus-course" in _slugs(c, "курс focus")  # one word per language
    # author and slug are searchable; punctuation is ignored
    assert "quiet-harbor" in _slugs(c, "aurora")
    assert "quiet-harbor" in _slugs(c, "quiet-harbor!")
    # no words → no filtering
    assert len(_slugs(c, "%20%21")) == ok(c.get("/catalog/apps?limit=100"))["total"]


def test_best_match_first_and_explicit_sort_kept():
    c = Client()
    res = _slugs(c, "медитация")
    assert res and res[0] == "quiet-harbor"  # the word is in its subtitle (weighted), others at most in text
    assert set(_slugs(c, "медитация", sort="new")) == set(res)


def test_products_morphology():
    c = Client()
    items = ok(c.get("/hardware/products?q=наушников&limit=100"))["items"]
    assert any("headphones" in i["slug"] for i in items)
    items = ok(c.get("/hardware/products?q=HEADPHONE&limit=100"))["items"]
    assert any("headphones" in i["slug"] for i in items)
    assert ok(c.get("/hardware/products?q=несуществующийтовар&limit=100"))["items"] == []
