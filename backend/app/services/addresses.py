"""International addresses: Unicode, country-specific required fields; Latin only when the carrier requires it."""
from __future__ import annotations

import re

from ..errors import ApiError

# required fields beyond name/line1/city/phone; postal regex (None = country has no postal codes)
RULES: dict[str, dict] = {
    "RU": {"postal": r"^\d{6}$", "region": False},
    "US": {"postal": r"^\d{5}(-\d{4})?$", "region": True, "region_re": r"^[A-Z]{2}$"},
    "CA": {"postal": r"^[A-Za-z]\d[A-Za-z] ?\d[A-Za-z]\d$", "region": True},
    "GB": {"postal": r"^[A-Za-z]{1,2}\d[A-Za-z\d]? ?\d[A-Za-z]{2}$", "region": False},
    "DE": {"postal": r"^\d{5}$", "region": False},
    "FR": {"postal": r"^\d{5}$", "region": False},
    "NL": {"postal": r"^\d{4} ?[A-Za-z]{2}$", "region": False},
    "JP": {"postal": r"^\d{3}-?\d{4}$", "region": True},
    "CN": {"postal": r"^\d{6}$", "region": True},
    "AE": {"postal": None, "region": True},
    "HK": {"postal": None, "region": False},
    "IE": {"postal": r"^[A-Za-z\d]{3} ?[A-Za-z\d]{4}$", "region": False, "postal_optional": True},
}
DEFAULT = {"postal": r"^[\w\- ]{2,12}$", "region": False, "postal_optional": True}
LATIN = re.compile(r"^[\x20-\x7E]*$")
ALLOWED_FIELDS = {"name", "company", "line1", "line2", "city", "region", "postal_code", "phone",
                  "name_latin", "line1_latin", "city_latin"}


def country_rules(country: str) -> dict:
    r = RULES.get(country, DEFAULT)
    return {"country": country, "postal_required": r.get("postal") is not None and not r.get("postal_optional"),
            "postal_supported": r.get("postal") is not None, "region_required": bool(r.get("region"))}


def validate_address(country: str, fields: dict, requires_latin: bool) -> dict:
    country = (country or "").upper()
    if not re.match(r"^[A-Z]{2}$", country):
        raise ApiError("address_invalid", 422, details={"fields": ["country"]})
    rules = RULES.get(country, DEFAULT)
    clean = {k: str(v).strip() for k, v in (fields or {}).items() if k in ALLOWED_FIELDS and v is not None
             and str(v).strip()}
    errors = []
    for f in ("name", "line1", "city", "phone"):
        if not clean.get(f):
            errors.append(f)
    if clean.get("phone") and not re.match(r"^\+?[\d\s\-()]{6,20}$", clean["phone"]):
        errors.append("phone")
    pr = rules.get("postal")
    if pr is None:
        clean.pop("postal_code", None)
    elif clean.get("postal_code"):
        if not re.match(pr, clean["postal_code"]):
            errors.append("postal_code")
    elif not rules.get("postal_optional"):
        errors.append("postal_code")
    if rules.get("region") and not clean.get("region"):
        errors.append("region")
    if rules.get("region_re") and clean.get("region") and not re.match(rules["region_re"], clean["region"]):
        errors.append("region")
    if requires_latin:
        for f in ("name", "line1", "city"):
            v = clean.get(f, "")
            if not LATIN.match(v) and not (clean.get(f + "_latin") and LATIN.match(clean[f + "_latin"])):
                errors.append(f + "_latin")
    if errors:
        raise ApiError("address_invalid", 422, details={"fields": sorted(set(errors))})
    return clean


# ------------------------------------------------------------------ address book (NAP-023)

MAX_SAVED = 20


def _same(a: dict, b: dict) -> bool:
    keys = ALLOWED_FIELDS
    return all((a.get(k) or "") == (b.get(k) or "") for k in keys)


def save_to_book(db, user_id, country: str, fields: dict):
    """Remember an address the buyer used (duplicates are not stored twice; the latest used comes first)."""
    from sqlalchemy import select

    from ..db import utcnow
    from ..models import Address
    country = country.upper()
    rows = list(db.scalars(select(Address).where(Address.user_id == user_id).order_by(Address.created_at.desc())))
    for r in rows:
        if r.country == country and _same(r.fields, fields):
            r.created_at = utcnow()  # "last used" ordering
            return r
    a = Address(user_id=user_id, country=country, fields=dict(fields))
    db.add(a)
    for old in rows[MAX_SAVED - 1:]:
        db.delete(old)
    return a


def book_view(a) -> dict:
    return {"id": str(a.id), "country": a.country, "fields": a.fields, "last_used_at": a.created_at.isoformat()}
