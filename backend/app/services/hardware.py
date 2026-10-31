"""Hardware catalog, compatibility, BOM/kit builder, inventory primitives and carts."""
from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import utcnow
from ..errors import ApiError
from ..models import (App, AppVersion, Bom, BomItem, Cart, CartItem, Category, Compatibility, DeviceModel,
                      HardwareOrder, Inventory, Product, Reservation, Seller, Sku)
from .common import get_translations, localized

COMPAT_RANK = {"verified": 0, "vendor_claimed": 1, "unverified": 2, "incompatible": 3}


# ------------------------------------------------------------------ availability

def sku_availability(db: Session, sku: Sku, product: Product) -> dict:
    if product.made_to_order:
        return {"type": "made_to_order", "lead_time_days": product.lead_time_days, "available": None}
    inv = db.scalar(select(Inventory).where(Inventory.sku_id == sku.id, Inventory.warehouse_id == product.warehouse_id))
    avail = (inv.on_hand - inv.reserved - inv.allocated) if inv else 0
    return {"type": "in_stock" if avail > 0 else "out_of_stock", "available": max(0, avail),
            "last_unit": avail == 1}


def serialize_product(db: Session, p: Product, locale: str, detail: bool = False, rating: dict | None = None) -> dict:
    tr = get_translations(db, "product", [p.id]).get(p.id)
    loc = localized(tr, locale)
    seller = db.get(Seller, p.seller_id)
    cat = db.get(Category, p.category_id)
    skus = []
    for s in p.skus:
        if not s.active:
            continue
        item = {"id": str(s.id), "code": s.code, "variant": s.variant, "price_minor": s.price_minor,
                "currency": s.currency, "availability": sku_availability(db, s, p), "provides": s.provides}
        if detail:
            item |= {"weight_g": s.weight_g, "dims_mm": [s.length_mm, s.width_mm, s.height_mm],
                     "package_count": s.package_count, "origin_country": s.origin_country,
                     "contains_battery": s.contains_battery,
                     "compatibility": compat_for_sku(db, s.id)}
        skus.append(item)
    out = {"id": str(p.id), "slug": p.slug, "brand": p.brand, "model": p.model, "kind": p.kind,
           "category": {"slug": cat.slug, "name": cat.names.get(locale) or cat.names.get("ru")},
           "seller": {"id": str(seller.id), "name": seller.name, "kind": seller.kind},
           "title": loc.get("title") or f"{p.brand} {p.model}", "summary": loc.get("summary"),
           "lang": loc["lang"], "translation_status": loc["translation_status"], "fallback": loc["fallback"],
           "made_to_order": p.made_to_order, "lead_time_days": p.lead_time_days, "prep_days": p.prep_days,
           "freight_mode": p.freight_mode, "hue": p.hue, "is_demo": p.is_demo, "skus": skus,
           "price_from_minor": min((s["price_minor"] for s in skus), default=None),
           "currency": skus[0]["currency"] if skus else None}
    from . import reviews  # NAP-079
    out["rating"] = rating if rating is not None else reviews.summaries(db, [p.id], "product")[p.id]
    if detail:
        out["rating_summary"] = reviews.summary(db, p)
        wh = p.warehouse_id
        from ..models import Warehouse
        w = db.get(Warehouse, wh)
        out |= {"description": loc.get("description"), "in_box": loc.get("in_box", []),
                "return_terms": loc.get("return_terms"), "delivery_terms": loc.get("delivery_terms"),
                "specs": p.specs, "warranty_months": p.warranty_months, "assembly_included": p.assembly_included,
                "ship_from_country": w.country, "destinations": p.destinations,
                "bom": bom_summary(db, p) if p.kind != "simple" else None}
    return out


def compat_for_sku(db: Session, sku_id: uuid.UUID) -> list[dict]:
    out = []
    for c in db.scalars(select(Compatibility).where(Compatibility.sku_id == sku_id)):
        dm = db.get(DeviceModel, c.device_model_id) if c.device_model_id else None
        out.append({"feature": c.feature, "status": c.status, "source": c.source, "tested_config": c.tested_config,
                    "verified_at": c.verified_at.isoformat() if c.verified_at else None, "details": c.details,
                    "device_model": dm.code if dm else None})
    return sorted(out, key=lambda x: COMPAT_RANK[x["status"]])


def list_products(db: Session, locale: str, category: str | None = None, q: str | None = None,
                  kind: str | None = None, limit: int = 50, offset: int = 0, min_rating: float | None = None,
                  sort: str | None = None) -> tuple[list[dict], int]:
    stmt = select(Product).where(Product.status == "published")
    if category:
        stmt = stmt.join(Category, Category.id == Product.category_id).where(Category.slug == category)
    if kind == "kits":
        stmt = stmt.where(Product.kind.in_(("kit_component", "kit_stock")))
    elif kind == "devices":
        stmt = stmt.where(Product.kind == "simple")
    rows = list(db.scalars(stmt.order_by(Product.created_at)))
    from . import reviews
    ratings = reviews.summaries(db, [p.id for p in rows], "product")
    items = [serialize_product(db, p, locale, rating=ratings[p.id]) for p in rows]
    if min_rating:
        items = [i for i in items if i["rating"]["average"] is not None and i["rating"]["average"] >= min_rating]
    if sort == "rating":
        items.sort(key=lambda i: (i["rating"]["average"] is not None, i["rating"]["average"] or 0,
                                  i["rating"]["count"]), reverse=True)
    if q:
        ql = q.lower()
        trs = get_translations(db, "product", [p.id for p in rows])
        keep = set()
        for p in rows:
            hay = " ".join([p.brand, p.model, p.slug] + [" ".join(str(v) for v in t.data.values())
                                                         for t in trs.get(p.id, {}).values()]).lower()
            if ql in hay:
                keep.add(str(p.id))
        items = [i for i in items if i["id"] in keep]
    return items[offset:offset + limit], len(items)


# ------------------------------------------------------------------ BOM / kit builder

def active_bom(db: Session, product: Product) -> Bom | None:
    return db.scalar(select(Bom).where(Bom.product_id == product.id, Bom.active.is_(True)).order_by(Bom.version.desc()))


def bom_summary(db: Session, product: Product) -> dict | None:
    b = active_bom(db, product)
    if not b:
        return None
    return {"bom_id": str(b.id), "version": b.version, "items": len(b.items)}


def evaluate_bom(db: Session, product: Product, locale: str, owned: set[str], app: App | None = None) -> dict:
    """Full BOM with owned/missing status, alternatives, availability, compatibility and per-seller grouping."""
    b = active_bom(db, product)
    if not b:
        raise ApiError("not_found", 404, entity="bom")
    required_caps: set[str] = set()
    if app and app.published_version_id:
        v = db.get(AppVersion, app.published_version_id)
        required_caps = set(v.requirements.get("required_capabilities", []))
    items, provided = [], set()
    missing_by_seller: dict[str, dict] = {}
    for it in b.items:
        sku = db.get(Sku, it.sku_id)
        prod = db.get(Product, sku.product_id)
        alts = [db.get(Sku, uuid.UUID(a)) for a in it.alternative_sku_ids]
        options = [sku] + [a for a in alts if a]
        owned_sku = next((o for o in options if str(o.id) in owned), None)
        tr = get_translations(db, "product", [prod.id]).get(prod.id)
        title = localized(tr, locale).get("title") or f"{prod.brand} {prod.model}"
        seller = db.get(Seller, prod.seller_id)
        status = "owned" if owned_sku else ("missing" if it.required else "optional")
        if owned_sku or it.required:
            provided |= set((owned_sku or sku).provides)
        entry = {"item_id": str(it.id), "role": it.role, "required": it.required, "quantity": it.quantity,
                 "status": status, "sku": {"id": str(sku.id), "code": sku.code, "title": title,
                                           "price_minor": sku.price_minor, "currency": sku.currency,
                                           "seller": {"id": str(seller.id), "name": seller.name},
                                           "availability": sku_availability(db, sku, prod),
                                           "compatibility": compat_for_sku(db, sku.id), "provides": sku.provides},
                 "alternatives": [{"id": str(a.id), "code": a.code, "price_minor": a.price_minor,
                                   "currency": a.currency, "compatibility": compat_for_sku(db, a.id)}
                                  for a in alts if a],
                 "owned_sku_id": str(owned_sku.id) if owned_sku else None}
        items.append(entry)
        if status == "missing":
            g = missing_by_seller.setdefault(f"{seller.id}:{sku.currency}", {
                "seller": {"id": str(seller.id), "name": seller.name}, "currency": sku.currency, "total_minor": 0,
                "items": 0})
            g["total_minor"] += sku.price_minor * it.quantity
            g["items"] += 1
    unmet = sorted(required_caps - provided)
    binaural_verified = any(c["feature"] == "binaural_path" and c["status"] == "verified"
                            for e in items if e["status"] != "optional" for c in e["sku"]["compatibility"])
    return {"product": {"id": str(product.id), "slug": product.slug, "kind": product.kind},
            "bom_id": str(b.id), "bom_version": b.version, "items": items,
            "missing_groups": list(missing_by_seller.values()),
            "separate_orders": len(missing_by_seller) > 1,
            "app": {"slug": app.slug, "required_capabilities": sorted(required_caps)} if app else None,
            "unmet_capabilities": unmet, "binaural_path_verified": binaural_verified,
            "note_codes": (["multi_seller_separate_orders"] if len(missing_by_seller) > 1 else []) +
                          (["digital_license_not_included"] if app else [])}


def compatible_for_app(db: Session, app: App, locale: str) -> list[dict]:
    v = db.get(AppVersion, app.published_version_id) if app.published_version_id else None
    req = set(v.requirements.get("required_capabilities", [])) if v else set()
    out = []
    for p in db.scalars(select(Product).where(Product.status == "published",
                                              Product.kind.in_(("kit_component", "kit_stock")))):
        b = active_bom(db, p)
        provided = set()
        if p.kind == "kit_stock":
            for s in p.skus:
                provided |= set(s.provides)
        if b:
            for it in b.items:
                if it.required:
                    provided |= set(db.get(Sku, it.sku_id).provides)
        out.append({**serialize_product(db, p, locale), "covers_requirements": req <= provided,
                    "unmet_capabilities": sorted(req - provided)})
    out.sort(key=lambda x: (not x["covers_requirements"], x["slug"]))
    return out


# ------------------------------------------------------------------ inventory primitives

class OutOfStock(Exception):
    def __init__(self, sku_code: str):
        super().__init__(sku_code)
        self.sku_code = sku_code


def reserve_order(db: Session, order: HardwareOrder) -> None:
    """Reserve all stock lines of one order atomically (deterministic lock order prevents deadlocks)."""
    ttl = timedelta(minutes=get_settings().reservation_ttl_minutes)
    need: dict[uuid.UUID, int] = {}
    for it in order.items:
        sku = db.get(Sku, it.sku_id)
        prod = db.get(Product, sku.product_id)
        if prod.made_to_order:
            continue
        need[sku.id] = need.get(sku.id, 0) + it.quantity
    for sku_id in sorted(need, key=str):
        inv = db.scalar(select(Inventory).where(Inventory.sku_id == sku_id, Inventory.warehouse_id == order.warehouse_id)
                        .with_for_update())
        if inv is None or inv.on_hand - inv.reserved - inv.allocated < need[sku_id]:
            raise OutOfStock(db.get(Sku, sku_id).code)
        inv.reserved += need[sku_id]
        db.add(Reservation(order_id=order.id, sku_id=sku_id, warehouse_id=order.warehouse_id, quantity=need[sku_id],
                           status="reserved", expires_at=utcnow() + ttl))
    db.flush()


def _inv(db: Session, r: Reservation) -> Inventory:
    return db.scalar(select(Inventory).where(Inventory.sku_id == r.sku_id, Inventory.warehouse_id == r.warehouse_id)
                     .with_for_update())


def active_reservations(db: Session, order_id: uuid.UUID) -> list[Reservation]:
    return list(db.scalars(select(Reservation).where(Reservation.order_id == order_id,
                                                     Reservation.status.in_(("reserved", "allocated")))
                           .order_by(Reservation.sku_id).with_for_update()))


def release_order(db: Session, order_id: uuid.UUID) -> None:
    """Release reserved or allocated (unshipped) quantities exactly once."""
    for r in active_reservations(db, order_id):
        inv = _inv(db, r)
        remaining = r.quantity - r.shipped_qty
        if r.status == "reserved":
            inv.reserved -= r.quantity
        else:
            inv.allocated -= remaining
        r.status = "released"
    db.flush()


def allocate_order(db: Session, order: HardwareOrder) -> bool:
    """Payment confirmed: reserved -> allocated. Late payment after expiry re-checks stock; never fakes it."""
    lines = active_reservations(db, order.id)
    expired = [r for r in lines if r.status == "reserved" and r.expires_at and r.expires_at < utcnow()]
    has_stock_lines = any(not db.get(Product, db.get(Sku, it.sku_id).product_id).made_to_order for it in order.items)
    if expired or (has_stock_lines and not lines):
        release_order(db, order.id)
        try:
            with db.begin_nested():
                reserve_order(db, order)
        except OutOfStock:
            return False
        lines = active_reservations(db, order.id)
    for r in lines:
        if r.status == "reserved":
            inv = _inv(db, r)
            inv.reserved -= r.quantity
            inv.allocated += r.quantity
            r.status, r.expires_at = "allocated", None
    db.flush()
    return True


def ship_quantity(db: Session, order_id: uuid.UUID, sku_id: uuid.UUID, qty: int) -> None:
    """Hand-over to carrier: on_hand and allocated decrease together."""
    for r in active_reservations(db, order_id):
        if r.sku_id != sku_id or r.status != "allocated":
            continue
        take = min(qty, r.quantity - r.shipped_qty)
        if take <= 0:
            continue
        inv = _inv(db, r)
        inv.on_hand -= take
        inv.allocated -= take
        r.shipped_qty += take
        if r.shipped_qty == r.quantity:
            r.status = "shipped"
        qty -= take
        if qty == 0:
            break
    db.flush()


def expire_reservations() -> int:
    from ..db import session_scope
    n = 0
    with session_scope() as db:
        rows = db.scalars(select(Reservation).where(Reservation.status == "reserved",
                                                    Reservation.expires_at < utcnow())
                          .order_by(Reservation.sku_id).with_for_update(skip_locked=True)).all()
        for r in rows:
            inv = _inv(db, r)
            inv.reserved -= r.quantity
            r.status = "released"
            n += 1
    return n


def restock(db: Session, sku_id: uuid.UUID, warehouse_id: uuid.UUID, qty: int) -> None:
    db.execute(text("UPDATE inventory SET on_hand = on_hand + :q WHERE sku_id=:s AND warehouse_id=:w"),
               {"q": qty, "s": sku_id, "w": warehouse_id})


# ------------------------------------------------------------------ carts

def get_cart(db: Session, user_id: uuid.UUID | None, anon_token: str | None, create: bool = True) -> Cart | None:
    cart = None
    if user_id:
        cart = db.scalar(select(Cart).where(Cart.user_id == user_id))
        if anon_token:  # merge the anonymous cart on login
            anon = db.scalar(select(Cart).where(Cart.anon_token_hash == hashlib.sha256(anon_token.encode()).hexdigest()))
            if anon and anon.items:
                if cart is None:
                    anon.user_id, anon.anon_token_hash = user_id, None
                    return anon
                for it in list(anon.items):
                    _add_item(db, cart, it.sku_id, it.quantity, it.source_bom_id)
                db.delete(anon)
        if cart is None and create:
            cart = Cart(user_id=user_id, owned_sku_ids=[])
            db.add(cart)
            db.flush()
    elif anon_token:
        h = hashlib.sha256(anon_token.encode()).hexdigest()
        cart = db.scalar(select(Cart).where(Cart.anon_token_hash == h))
        if cart is None and create:
            cart = Cart(anon_token_hash=h, owned_sku_ids=[])
            db.add(cart)
            db.flush()
    return cart


def _add_item(db: Session, cart: Cart, sku_id: uuid.UUID, qty: int, bom_id: uuid.UUID | None = None) -> None:
    existing = next((i for i in cart.items if i.sku_id == sku_id), None)
    if existing:
        existing.quantity = min(existing.quantity + qty, 99)
    else:
        cart.items.append(CartItem(sku_id=sku_id, quantity=min(qty, 99), source_bom_id=bom_id))
    cart.updated_at = utcnow()


def cart_add(db: Session, cart: Cart, sku_id: uuid.UUID, qty: int) -> None:
    sku = db.get(Sku, sku_id)
    if not sku or not sku.active or db.get(Product, sku.product_id).status != "published":
        raise ApiError("not_found", 404, entity="sku")
    if not 1 <= qty <= 99:
        raise ApiError("validation_failed", 422, details={"field": "quantity"})
    _add_item(db, cart, sku_id, qty)


def cart_set(db: Session, cart: Cart, sku_id: uuid.UUID, qty: int) -> None:
    it = next((i for i in cart.items if i.sku_id == sku_id), None)
    if it is None:
        raise ApiError("not_found", 404, entity="cart_item")
    if qty <= 0:
        cart.items.remove(it)
    else:
        it.quantity = min(qty, 99)


def cart_add_bom(db: Session, cart: Cart, product: Product, owned: set[str], choices: dict[str, str],
                 include_optional: set[str]) -> int:
    b = active_bom(db, product)
    if not b:
        raise ApiError("not_found", 404, entity="bom")
    added = 0
    cart.owned_sku_ids = sorted(owned)
    for it in b.items:
        options = [str(it.sku_id)] + list(it.alternative_sku_ids)
        if any(o in owned for o in options):
            continue
        if not it.required and str(it.id) not in include_optional:
            continue
        chosen = choices.get(str(it.id), str(it.sku_id))
        if chosen not in options:
            raise ApiError("validation_failed", 422, details={"field": f"choices.{it.id}"})
        _add_item(db, cart, uuid.UUID(chosen), it.quantity, b.id)
        added += 1
    return added


def serialize_cart(db: Session, cart: Cart | None, locale: str) -> dict:
    if cart is None:
        return {"groups": [], "items": 0, "separate_orders": False}
    groups: dict[str, dict] = {}
    for it in cart.items:
        sku = db.get(Sku, it.sku_id)
        prod = db.get(Product, sku.product_id)
        seller = db.get(Seller, prod.seller_id)
        key = f"{seller.id}:{prod.warehouse_id}:{sku.currency}"
        tr = get_translations(db, "product", [prod.id]).get(prod.id)
        g = groups.setdefault(key, {"seller": {"id": str(seller.id), "name": seller.name, "kind": seller.kind},
                                    "currency": sku.currency, "items": [], "goods_minor": 0,
                                    "needs_custom_quote": False})
        g["items"].append({"sku_id": str(sku.id), "code": sku.code, "product_slug": prod.slug,
                           "title": localized(tr, locale).get("title") or f"{prod.brand} {prod.model}",
                           "quantity": it.quantity, "unit_price_minor": sku.price_minor,
                           "availability": sku_availability(db, sku, prod), "hue": prod.hue})
        g["goods_minor"] += sku.price_minor * it.quantity
        g["needs_custom_quote"] |= prod.freight_mode == "custom_quote"
    return {"groups": list(groups.values()), "items": sum(i.quantity for i in cart.items),
            "separate_orders": len(groups) > 1, "owned_sku_ids": cart.owned_sku_ids}
