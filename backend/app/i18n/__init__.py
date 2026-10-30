"""Server-side dictionaries (errors, statuses, notifications) with parameters and plural forms.

Message syntax: "Осталось {count, plural, one {# место} few {# места} many {# мест} other {# места}}"
and simple "{name}" substitution. Machine codes never change with the locale.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

LOCALES = ("ru", "en")
DEFAULT_LOCALE = "ru"
_DIR = Path(__file__).parent


@lru_cache
def catalog(locale: str) -> dict[str, str]:
    with open(_DIR / f"{locale}.json", encoding="utf-8") as fh:
        return _flatten(json.load(fh))


def _flatten(d: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def plural_category(locale: str, n: int | float) -> str:
    if locale == "ru":
        if not float(n).is_integer():
            return "other"
        n = int(abs(n))
        if n % 10 == 1 and n % 100 != 11:
            return "one"
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            return "few"
        return "many"
    return "one" if n == 1 else "other"


_PLURAL = re.compile(r"\{(\w+), plural,((?:\s*\w+\s*\{[^{}]*\})+)\s*\}")
_FORM = re.compile(r"(\w+)\s*\{([^{}]*)\}")


def format_message(template: str, locale: str, params: dict | None = None) -> str:
    params = params or {}

    def _pl(m: re.Match) -> str:
        name = m.group(1)
        n = params.get(name, 0)
        forms = dict(_FORM.findall(m.group(2)))
        cat = plural_category(locale, n)
        text = forms.get(cat) or forms.get("other") or ""
        return text.replace("#", str(n))

    out = _PLURAL.sub(_pl, template)
    for k, v in params.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def t(key: str, locale: str, **params) -> str:
    loc = locale if locale in LOCALES else DEFAULT_LOCALE
    template = catalog(loc).get(key)
    if template is None:
        template = catalog(DEFAULT_LOCALE).get(key, key)
    return format_message(template, loc, params)


def negotiate(accept_language: str | None) -> str:
    """First visit: browser language; fallback Russian."""
    if not accept_language:
        return DEFAULT_LOCALE
    for part in accept_language.split(","):
        tag = part.split(";")[0].strip().lower()[:2]
        if tag in LOCALES:
            return tag
    return DEFAULT_LOCALE


def missing_keys() -> dict[str, list[str]]:
    keys = {loc: set(catalog(loc)) for loc in LOCALES}
    allk = set().union(*keys.values())
    return {loc: sorted(allk - k) for loc, k in keys.items()}
