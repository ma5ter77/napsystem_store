"""Supplier product cabinet (NAP-022): a supplier creates a product card, variants (SKU), what is in the box and the
RU/EN texts, sends it to moderation; after approval the product and its texts appear in the catalog.

Rules
- The card (fields, texts, SKU physical data) is editable in draft and rejected states only.
- A published product keeps selling while the supplier changes prices, switches variants on/off and counts stock.
  Changing the card itself means "withdraw" (back to draft, hidden from the catalog) and send it again.
- Texts are stored as content translations with status draft; approval publishes them together with the product.
"""
from __future__ import annotations

import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..errors import ApiError
from ..models import (AuditLog, Category, Inventory, Product, Seller, SellerMember, Sku, Translation, User,
                      Warehouse)
from .common import audit, get_translations, notify, upsert_translation

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")
SKU_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{2,63}$")
EDITABLE = ("draft", "rejected")
TEXT_FIELDS = ("title", "summary", "description", "return_terms", "delivery_terms")
CURRENCIES = ("EUR", "USD", "RSD", "GBP")
FREIGHT = ("parcel", "custom_quote")
BATTERY = ("none", "li_ion_contained", "li_ion_packed", "li_metal")


def slugify(s: str) -> str:
    tr = str.maketrans({"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
                        "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
                        "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh",
                        "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya"})
    s = s.lower().translate(tr)
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", s)).strip("-")[:80]


def owned_product(db: Session, seller_ids: set[uuid.UUID], product_id: uuid.UUID, lock: bool = False) -> Product:
    p = db.get(Product, product_id, with_for_update=lock)
    if not p or p.seller_id not in seller_ids:
        raise ApiError("not_found", 404, entity="product")  # other suppliers' products do not exist for you
    return p


def _require_editable(p: Product) -> None:
    if p.status not in EDITABLE:
        raise ApiError("product_locked", 409, details={"status": p.status})


def _category(db: Session, slug: str) -> Category:
    c = db.scalar(select(Category).where(Category.kind == "hardware", Category.slug == slug))
    if not c:
        raise ApiError("validation_failed", 422, details={"fields": ["category"]})
    return c


def _warehouse(db: Session, seller_id: uuid.UUID, warehouse_id: str | None) -> Warehouse:
    q = select(Warehouse).where(Warehouse.seller_id == seller_id)
    if warehouse_id:
        q = q.where(Warehouse.id == uuid.UUID(warehouse_id))
    w = db.scalars(q.order_by(Warehouse.name)).first()
    if not w:
        raise ApiError("validation_failed", 422, details={"fields": ["warehouse_id"]})
    return w


def _apply_fields(db: Session, p: Product, d: dict) -> None:
    bad = []
    if "category" in d:
        p.category_id = _category(db, d["category"]).id
    for k in ("brand", "model"):
        if k in d:
            v = (d[k] or "").strip()
            if not v or len(v) > 80:
                bad.append(k)
            setattr(p, k, v)
    for k, lo, hi in (("warranty_months", 0, 120), ("lead_time_days", 0, 365), ("prep_days", 0, 60)):
        if k in d:
            if not isinstance(d[k], int) or not lo <= d[k] <= hi:
                bad.append(k)
            else:
                setattr(p, k, d[k])
    for k in ("assembly_included", "made_to_order"):
        if k in d:
            setattr(p, k, bool(d[k]))
    if "freight_mode" in d:
        if d["freight_mode"] not in FREIGHT:
            bad.append("freight_mode")
        p.freight_mode = d["freight_mode"]
    if "destinations" in d:
        dest = sorted({str(x).upper() for x in d["destinations"] or []})
        if any(not re.fullmatch(r"[A-Z]{2}", x) for x in dest) or len(dest) > 60:
            bad.append("destinations")
        p.destinations = dest
    if "specs" in d:
        specs = d["specs"] or {}
        if not isinstance(specs, dict) or len(specs) > 40 or any(
                not isinstance(k, str) or len(k) > 60 or len(str(v)) > 200 for k, v in specs.items()):
            bad.append("specs")
        else:
            p.specs = {k.strip(): v for k, v in specs.items() if k.strip()}
    if "hue" in d and isinstance(d["hue"], int):
        p.hue = d["hue"] % 360
    if bad:
        raise ApiError("validation_failed", 422, details={"fields": sorted(set(bad))})


def create(db: Session, user: User, seller_ids: set[uuid.UUID], d: dict) -> Product:
    if not seller_ids:
        raise ApiError("forbidden", 403)
    seller_id = uuid.UUID(d["seller_id"]) if d.get("seller_id") else sorted(seller_ids)[0]
    if seller_id not in seller_ids:
        raise ApiError("forbidden", 403)
    brand, model = (d.get("brand") or "").strip(), (d.get("model") or "").strip()
    slug = (d.get("slug") or slugify(f"{brand} {model}")).strip()
    if not SLUG_RE.match(slug):
        raise ApiError("validation_failed", 422, details={"fields": ["slug"]})
    if db.scalar(select(Product.id).where(Product.slug == slug)):
        raise ApiError("slug_taken", 409, details={"fields": ["slug"]})
    p = Product(seller_id=seller_id, slug=slug, brand=brand, model=model, kind="simple", status="draft",
                category_id=_category(db, d.get("category", "")).id,
                warehouse_id=_warehouse(db, seller_id, d.get("warehouse_id")).id, destinations=[], specs={},
                is_demo=False, hue=int(uuid.uuid4().int % 360))
    _apply_fields(db, p, {k: v for k, v in d.items() if k not in ("category", "slug", "seller_id", "warehouse_id")})
    db.add(p)
    db.flush()
    audit(db, user.id, "product.created", "product", p.id, {"slug": slug})
    return p


def update(db: Session, user: User, p: Product, d: dict) -> Product:
    _require_editable(p)
    if "warehouse_id" in d and d["warehouse_id"]:
        p.warehouse_id = _warehouse(db, p.seller_id, d["warehouse_id"]).id
    _apply_fields(db, p, d)
    audit(db, user.id, "product.updated", "product", p.id, {"fields": sorted(d.keys())[:20]})
    return p


def save_texts(db: Session, user: User, p: Product, locale: str, d: dict) -> None:
    _require_editable(p)
    if locale not in ("ru", "en"):
        raise ApiError("validation_failed", 422, details={"fields": ["locale"]})
    data, bad = {}, []
    for k, limit in (("title", 120), ("summary", 300), ("description", 5000), ("return_terms", 1000),
                     ("delivery_terms", 1000)):
        v = (d.get(k) or "").strip()
        if len(v) > limit:
            bad.append(k)
        if v:
            data[k] = v
    box = [str(x).strip() for x in (d.get("in_box") or []) if str(x).strip()]
    if len(box) > 40 or any(len(x) > 160 for x in box):
        bad.append("in_box")
    data["in_box"] = box
    if bad:
        raise ApiError("validation_failed", 422, details={"fields": bad})
    if not any(data.get(k) for k in TEXT_FIELDS) and not box:  # empty form = remove this language
        db.query(Translation).filter_by(entity_type="product", entity_id=p.id, locale=locale).delete()
    else:
        upsert_translation(db, "product", p.id, locale, data, "draft")
    audit(db, user.id, "product.texts_saved", "product", p.id, {"locale": locale})


def _sku_fields(sku: Sku, d: dict, physical: bool) -> list[str]:
    bad = []
    if "price_minor" in d:
        if not isinstance(d["price_minor"], int) or not 0 < d["price_minor"] <= 100_000_000:
            bad.append("price_minor")
        else:
            sku.price_minor = d["price_minor"]
    if "active" in d:
        sku.active = bool(d["active"])
    if not physical:
        return bad
    if "currency" in d:
        if d["currency"] not in CURRENCIES:
            bad.append("currency")
        sku.currency = d["currency"]
    for k, hi in (("weight_g", 500_000), ("length_mm", 4000), ("width_mm", 4000), ("height_mm", 4000),
                  ("package_count", 20)):
        if k in d:
            if not isinstance(d[k], int) or not 0 < d[k] <= hi:
                bad.append(k)
            else:
                setattr(sku, k, d[k])
    if "origin_country" in d:
        c = str(d["origin_country"] or "").upper()
        if not re.fullmatch(r"[A-Z]{2}", c):
            bad.append("origin_country")
        sku.origin_country = c
    if "hs_code" in d:
        h = re.sub(r"[^0-9]", "", str(d["hs_code"] or ""))
        if h and not 6 <= len(h) <= 10:
            bad.append("hs_code")
        sku.hs_code = h or None
    if "customs_description" in d:
        sku.customs_description = (d["customs_description"] or "").strip()[:255] or None
    if "contains_battery" in d:
        if d["contains_battery"] not in BATTERY:
            bad.append("contains_battery")
        sku.contains_battery = d["contains_battery"]
    if "variant" in d:
        v = d["variant"] or {}
        if not isinstance(v, dict) or len(v) > 6 or any(len(str(k)) > 40 or len(str(x)) > 80 for k, x in v.items()):
            bad.append("variant")
        else:
            sku.variant = {str(k).strip(): str(x).strip() for k, x in v.items() if str(k).strip()}
    return bad


def add_sku(db: Session, user: User, p: Product, d: dict) -> Sku:
    _require_editable(p)
    code = (d.get("code") or "").strip().upper()
    if not code:
        n = db.scalar(select(func.count()).select_from(Sku).where(Sku.product_id == p.id)) + 1
        code = f"{p.slug.upper()[:56]}-{n:02d}"
    if not SKU_RE.match(code):
        raise ApiError("validation_failed", 422, details={"fields": ["code"]})
    if db.scalar(select(Sku.id).where(Sku.code == code)):
        raise ApiError("sku_taken", 409, details={"fields": ["code"]})
    sku = Sku(product_id=p.id, code=code, variant={}, currency="EUR", price_minor=0, weight_g=0, length_mm=0,
              width_mm=0, height_mm=0, package_count=1, origin_country="", provides=[], active=True)
    bad = _sku_fields(sku, d, physical=True)
    if bad:
        raise ApiError("validation_failed", 422, details={"fields": bad})
    db.add(sku)
    db.flush()
    if not db.scalar(select(Inventory.id).where(Inventory.sku_id == sku.id, Inventory.warehouse_id == p.warehouse_id)):
        db.add(Inventory(sku_id=sku.id, warehouse_id=p.warehouse_id, on_hand=0, reserved=0, allocated=0))
    audit(db, user.id, "sku.created", "sku", sku.id, {"code": code})
    return sku


def update_sku(db: Session, user: User, p: Product, sku_id: uuid.UUID, d: dict) -> Sku:
    sku = db.get(Sku, sku_id)
    if not sku or sku.product_id != p.id:
        raise ApiError("not_found", 404, entity="sku")
    physical = p.status in EDITABLE
    if not physical and set(d) - {"price_minor", "active"}:
        raise ApiError("product_locked", 409, details={"status": p.status})  # a published card changes via withdraw/resubmit
    old = sku.price_minor
    bad = _sku_fields(sku, d, physical)
    if bad:
        raise ApiError("validation_failed", 422, details={"fields": bad})
    audit(db, user.id, "sku.updated", "sku", sku.id,
          {"fields": sorted(d.keys()), **({"price": [old, sku.price_minor]} if old != sku.price_minor else {})})
    return sku


def checks(db: Session, p: Product) -> list[dict]:
    """What a moderator needs; the same list drives the supplier's checklist and the submit validation."""
    trs = get_translations(db, "product", [p.id]).get(p.id, {})
    has_text = any(tr.data.get("title") and tr.data.get("description") for tr in trs.values())
    skus = [s for s in p.skus if s.active]
    priced = bool(skus) and all(s.price_minor > 0 for s in skus)
    physical = bool(skus) and all(s.weight_g > 0 and s.length_mm > 0 and s.width_mm > 0 and s.height_mm > 0
                                  and s.origin_country for s in skus)
    box = any(tr.data.get("in_box") for tr in trs.values())
    return [
        {"key": "basics", "ok": bool(p.brand and p.model and p.category_id), "tab": "basics"},
        {"key": "destinations", "ok": bool(p.destinations), "tab": "basics"},
        {"key": "sku", "ok": priced, "tab": "skus"},
        {"key": "package", "ok": physical, "tab": "skus"},
        {"key": "texts", "ok": has_text, "tab": "texts"},
        {"key": "in_box", "ok": box, "tab": "texts"},
        {"key": "both_languages", "ok": {"ru", "en"} <= set(trs), "tab": "texts", "optional": True},
    ]


def submit(db: Session, user: User, p: Product) -> Product:
    _require_editable(p)
    missing = [c["key"] for c in checks(db, p) if not c["ok"] and not c.get("optional")]
    if missing:
        raise ApiError("product_incomplete", 422, details={"missing": missing})
    p.status = "submitted"
    audit(db, user.id, "product.submitted", "product", p.id, {})
    return p


def withdraw(db: Session, user: User, p: Product) -> Product:
    if p.status not in ("submitted", "published"):
        raise ApiError("invalid_state", 409)
    was = p.status
    p.status = "draft"
    if was == "published":  # back to draft texts: edits are re-moderated before they reach buyers
        for tr in db.scalars(select(Translation).where(Translation.entity_type == "product",
                                                       Translation.entity_id == p.id)):
            tr.status = "draft"
    audit(db, user.id, "product.withdrawn", "product", p.id, {"from": was})
    return p


def history(db: Session, p: Product) -> list[dict]:
    rows = db.scalars(select(AuditLog).where(AuditLog.entity_type == "product", AuditLog.entity_id == str(p.id),
                                             AuditLog.action.in_(("product.submitted", "product.published",
                                                                  "product.rejected", "product.withdrawn")))
                      .order_by(AuditLog.created_at.desc()).limit(20))
    return [{"action": r.action.split(".", 1)[1], "comment": (r.data or {}).get("comment") or None,
             "at": r.created_at.isoformat()} for r in rows]


def view(db: Session, p: Product, locale: str) -> dict:
    trs = get_translations(db, "product", [p.id]).get(p.id, {})
    cat = db.get(Category, p.category_id)
    inv = {i.sku_id: i for i in db.scalars(select(Inventory).where(Inventory.warehouse_id == p.warehouse_id,
                                                                    Inventory.sku_id.in_([s.id for s in p.skus])))}
    cs = checks(db, p)
    return {
        "id": str(p.id), "slug": p.slug, "status": p.status, "brand": p.brand, "model": p.model, "kind": p.kind,
        "category": cat.slug, "category_name": cat.names.get(locale) or cat.names.get("ru"),
        "warehouse_id": str(p.warehouse_id), "warranty_months": p.warranty_months,
        "assembly_included": p.assembly_included, "made_to_order": p.made_to_order,
        "lead_time_days": p.lead_time_days, "prep_days": p.prep_days, "freight_mode": p.freight_mode,
        "destinations": p.destinations, "specs": p.specs, "hue": p.hue, "editable": p.status in EDITABLE,
        "texts": {loc: {**tr.data, "status": tr.status} for loc, tr in trs.items()},
        "skus": [{"id": str(s.id), "code": s.code, "variant": s.variant, "price_minor": s.price_minor,
                  "currency": s.currency, "weight_g": s.weight_g, "length_mm": s.length_mm, "width_mm": s.width_mm,
                  "height_mm": s.height_mm, "package_count": s.package_count, "origin_country": s.origin_country,
                  "hs_code": s.hs_code, "customs_description": s.customs_description,
                  "contains_battery": s.contains_battery, "active": s.active,
                  "on_hand": inv[s.id].on_hand if s.id in inv else 0} for s in p.skus],
        "checks": cs, "ready": all(c["ok"] for c in cs if not c.get("optional")),
        "history": history(db, p), "created_at": p.created_at.isoformat(),
    }


def list_for_seller(db: Session, seller_ids: set[uuid.UUID], locale: str) -> list[dict]:
    rows = db.scalars(select(Product).where(Product.seller_id.in_(seller_ids)).order_by(Product.created_at.desc()))
    rows = list(rows)
    trs = get_translations(db, "product", [p.id for p in rows])
    out = []
    for p in rows:
        t = trs.get(p.id, {})
        loc = t.get(locale) or t.get("ru") or t.get("en")
        last = history(db, p)[:1]
        out.append({"id": str(p.id), "slug": p.slug, "status": p.status,
                    "title": (loc.data.get("title") if loc else None) or f"{p.brand} {p.model}",
                    "skus": len(p.skus), "languages": sorted(t), "is_demo": p.is_demo,
                    "last": last[0] if last else None})
    return out


# ------------------------------------------------------------------ moderation

def moderation_queue(db: Session, locale: str) -> list[dict]:
    rows = db.scalars(select(Product).where(Product.status == "submitted").order_by(Product.created_at))
    out = []
    for p in rows:
        v = view(db, p, locale)
        sub = next((h for h in v["history"] if h["action"] == "submitted"), None)
        out.append({"id": v["id"], "slug": p.slug, "brand": p.brand, "model": p.model,
                    "seller": db.get(Seller, p.seller_id).name, "skus": len(p.skus),
                    "languages": sorted(v["texts"]), "submitted_at": sub["at"] if sub else None,
                    "title": (v["texts"].get(locale) or v["texts"].get("ru") or v["texts"].get("en") or {}).get("title")})
    return out


def decide(db: Session, moderator: User, p: Product, decision: str, comment: str) -> Product:
    if p.status != "submitted":
        raise ApiError("invalid_state", 409)
    if decision not in ("approve", "reject"):
        raise ApiError("validation_failed", 422, details={"fields": ["decision"]})
    if decision == "reject" and not comment.strip():
        raise ApiError("comment_required", 422, details={"fields": ["comment"]})
    p.status = "published" if decision == "approve" else "rejected"
    if decision == "approve":
        for tr in db.scalars(select(Translation).where(Translation.entity_type == "product",
                                                       Translation.entity_id == p.id)):
            tr.status = "published"
    audit(db, moderator.id, f"product.{p.status}", "product", p.id, {"comment": comment.strip()[:500]})
    n = db.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.entity_type == "product", AuditLog.entity_id == str(p.id), AuditLog.action == "product.submitted"))
    for uid, loc in db.execute(select(User.id, User.locale).join(SellerMember, SellerMember.user_id == User.id)
                               .where(SellerMember.seller_id == p.seller_id, SellerMember.role == "seller")):
        notify(db, uid, loc, f"product_{p.status}", f"product:{p.id}:{p.status}:{n}",
               product=f"{p.brand} {p.model}", comment=comment.strip() or "—")
    return p
