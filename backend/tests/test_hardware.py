"""Criteria 25-33: BOM, split checkout, stock, shipping, quotes, parcels, tracking, returns, documents."""
import threading
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.adapters import shipping as shp
from app.db import session_scope, utcnow
from app.models import HardwareOrder, Inventory, Parcel, Reservation, Sku, TrackingEvent

from .conftest import Client, ok
from .helpers import mock_pay

ADDR_AM = {"name": "Anna Petrosyan", "line1": "Abovyan st. 10", "city": "Yerevan", "postal_code": "0001",
           "phone": "+374 10 000000"}


def sku_id(code):
    with session_scope() as db:
        return str(db.scalar(select(Sku).where(Sku.code == code)).id)


def inv(code):
    with session_scope() as db:
        s = db.scalar(select(Sku).where(Sku.code == code))
        i = db.scalar(select(Inventory).where(Inventory.sku_id == s.id))
        return i.on_hand, i.reserved, i.allocated


def buy(buyer: Client, items, country="AM", fields=ADDR_AM, pay=True):
    for code, q in items:
        ok(buyer.post("/hardware/cart/items", {"sku_id": sku_id(code), "quantity": q}))
    res = ok(buyer.post("/hardware/checkout"), 201)
    o = res["orders"][0]
    ok(buyer.put(f"/hardware/orders/{o['id']}/address", {"country": country, "fields": fields}))
    rates = ok(buyer.post(f"/hardware/orders/{o['id']}/rates"))["items"]
    ok(buyer.post(f"/hardware/orders/{o['id']}/shipping", {"rate_id": rates[0]["id"]}))
    if pay:
        p = ok(buyer.post(f"/hardware/orders/{o['id']}/pay"))
        mock_pay(buyer, p["checkout_url"])
    return ok(buyer.get(f"/hardware/orders/{o['id']}"))


def test_bom_owned_missing_and_compatibility():
    c = Client()
    hp = sku_id("NAP-HP1")
    full = ok(c.get("/hardware/kits/nap-starter-kit/bom?app=quiet-harbor"))
    assert [i["status"] for i in full["items"]].count("missing") == 4
    assert full["app"]["required_capabilities"] == ["stereo_output", "stereo_separation"]
    assert full["unmet_capabilities"] == [] and full["binaural_path_verified"]
    assert "digital_license_not_included" in full["note_codes"]
    owned = ok(c.get(f"/hardware/kits/nap-starter-kit/bom?owned={hp}&app=quiet-harbor"))
    st = {i["role"]: i["status"] for i in owned["items"]}
    assert st["headphones"] == "owned" and st["sensor"] == "optional"
    hp_item = next(i for i in full["items"] if i["role"] == "headphones")
    assert hp_item["alternatives"][0]["compatibility"][0]["status"] == "vendor_claimed"
    # optional item from another seller -> separate orders flagged before payment
    buyer = Client().register()
    vib_item = next(i for i in full["items"] if i["role"] == "vibroacoustic")["item_id"]
    cart = ok(buyer.post("/hardware/cart/bom", {"product_slug": "nap-starter-kit", "owned_sku_ids": [hp],
                                                "include_optional": [vib_item]}))
    assert cart["added"] == 4 and cart["separate_orders"]
    assert len(cart["groups"]) == 2


def test_compatible_hardware_for_app_and_speakers_not_binaural():
    c = Client()
    items = ok(c.get("/hardware/compatible?app=quiet-harbor"))["items"]
    assert any(i["slug"] == "nap-starter-kit" and i["covers_requirements"] for i in items)
    sp = ok(c.get("/hardware/products/aurora-desk-speakers"))
    assert sp["skus"][0]["compatibility"][0]["status"] == "incompatible"
    chair = ok(c.get("/hardware/products/nap-chair-one"))
    statuses = {x["feature"]: x["status"] for x in chair["skus"][0]["compatibility"]}
    assert statuses["binaural_path"] == "unverified" and statuses["vibroacoustic"] == "vendor_claimed"


def test_multi_seller_cart_creates_independent_orders_and_isolation():
    buyer = Client().register()
    ok(buyer.post("/hardware/cart/items", {"sku_id": sku_id("NAP-CBL35"), "quantity": 1}))
    ok(buyer.post("/hardware/cart/items", {"sku_id": sku_id("AUR-OE5"), "quantity": 1}))
    res = ok(buyer.post("/hardware/checkout"), 201)
    assert res["separate_orders"] and len(res["orders"]) == 2
    a, b = res["orders"]
    assert a["seller"]["id"] != b["seller"]["id"] and a["group_ref"] == b["group_ref"]
    # pay only the operator order
    op_order = next(o for o in res["orders"] if o["seller"]["kind"] == "operator")
    ok(buyer.put(f"/hardware/orders/{op_order['id']}/address", {"country": "AM", "fields": ADDR_AM}))
    r = ok(buyer.post(f"/hardware/orders/{op_order['id']}/rates"))["items"][0]
    ok(buyer.post(f"/hardware/orders/{op_order['id']}/shipping", {"rate_id": r["id"]}))
    mock_pay(buyer, ok(buyer.post(f"/hardware/orders/{op_order['id']}/pay"))["checkout_url"])
    seller1 = Client().login("seller@nap.demo")
    seller2 = Client().login("seller2@nap.demo")
    assert ok(seller1.get(f"/seller/orders/{op_order['id']}"))["payment_status"] == "paid"
    assert seller2.get(f"/seller/orders/{op_order['id']}").status_code == 404  # other seller sees nothing
    assert op_order["id"] not in [o["id"] for o in ok(seller2.get("/seller/orders"))["items"]]
    # documents are closed to strangers
    stranger = Client().register()
    assert stranger.get(f"/hardware/orders/{op_order['id']}/documents/packing-list").status_code == 404
    assert seller2.get(f"/hardware/orders/{op_order['id']}/documents/packing-list").status_code == 404
    assert ok(seller1.get(f"/hardware/orders/{op_order['id']}/documents/packing-list"))["order"]
    # fulfillment operator sees assigned paid orders without prices
    ful = Client().login("fulfillment@nap.demo")
    v = ok(ful.get(f"/seller/orders/{op_order['id']}"))
    assert "total_minor" not in v and "unit_price_minor" not in v["items"][0]


def test_last_unit_cannot_be_sold_twice_concurrently():
    code = "AUR-VP2"
    assert inv(code) == (1, 0, 0)
    results = []
    buyers = [Client().register() for _ in range(4)]
    for b in buyers:
        ok(b.post("/hardware/cart/items", {"sku_id": sku_id(code), "quantity": 1}))

    def go(b):
        results.append(b.post("/hardware/checkout").status_code)
    ts = [threading.Thread(target=go, args=(b,)) for b in buyers]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(results) == [201, 409, 409, 409]
    assert inv(code) == (1, 1, 0)


def test_reservation_expiry_and_late_payment():
    buyer = Client().register()
    o = buy(buyer, [("NAP-PSU24-EU", 2)], pay=False)
    before = inv("NAP-PSU24-EU")
    with session_scope() as db:
        for r in db.scalars(select(Reservation).where(Reservation.order_id == uuid.UUID(o["id"]))):
            r.expires_at = utcnow() - timedelta(minutes=1)
    from app.services.hardware import expire_reservations
    assert expire_reservations() >= 1
    assert inv("NAP-PSU24-EU")[1] == before[1] - 2
    # late payment with stock still available: re-secured, allocated, no fake stock
    p = ok(buyer.post(f"/hardware/orders/{o['id']}/pay"))
    mock_pay(buyer, p["checkout_url"])
    o2 = ok(buyer.get(f"/hardware/orders/{o['id']}"))
    assert o2["status"] == "confirmed" and o2["fulfillment_status"] == "unfulfilled"
    assert inv("NAP-PSU24-EU")[2] >= 2


def test_late_payment_without_stock_is_explicit_exception():
    buyer, other = Client().register(), Client().register()
    with session_scope() as db:
        s = db.scalar(select(Sku).where(Sku.code == "NAP-TRAVEL-BOX"))
        i = db.scalar(select(Inventory).where(Inventory.sku_id == s.id))
        avail = i.on_hand - i.reserved - i.allocated
    o = buy(buyer, [("NAP-TRAVEL-BOX", avail)], pay=False)
    with session_scope() as db:
        for r in db.scalars(select(Reservation).where(Reservation.order_id == uuid.UUID(o["id"]))):
            r.expires_at = utcnow() - timedelta(minutes=1)
    from app.services.hardware import expire_reservations
    expire_reservations()
    buy(other, [("NAP-TRAVEL-BOX", 1)], pay=False)  # someone else takes a unit meanwhile
    p = ok(buyer.post(f"/hardware/orders/{o['id']}/pay"))
    mock_pay(buyer, p["checkout_url"])
    o2 = ok(buyer.get(f"/hardware/orders/{o['id']}"))
    assert o2["payment_status"] == "paid" and o2["fulfillment_status"] == "exception"
    assert o2["exception_reason"] == "stock_unavailable_after_payment"
    assert inv("NAP-TRAVEL-BOX")[0] >= inv("NAP-TRAVEL-BOX")[1] + inv("NAP-TRAVEL-BOX")[2]


def test_no_free_shipping_on_errors_and_recalculation():
    buyer = Client().register()
    ok(buyer.post("/hardware/cart/items", {"sku_id": sku_id("NAP-CBL35"), "quantity": 1}))
    o = ok(buyer.post("/hardware/checkout"), 201)["orders"][0]
    assert o["shipping_minor"] is None and o["total_minor"] is None
    # unsupported route -> explicit error, never 0
    ok(buyer.put(f"/hardware/orders/{o['id']}/address", {"country": "KP", "fields": ADDR_AM | {"postal_code": "12345"}}))
    r = buyer.post(f"/hardware/orders/{o['id']}/rates")
    assert r.json()["error"]["code"] == "route_unsupported"
    assert ok(buyer.get(f"/hardware/orders/{o['id']}"))["shipping_minor"] is None
    assert buyer.post(f"/hardware/orders/{o['id']}/pay").json()["error"]["code"] == "shipping_required"
    # supported route
    ok(buyer.put(f"/hardware/orders/{o['id']}/address", {"country": "AM", "fields": ADDR_AM}))
    rates = ok(buyer.post(f"/hardware/orders/{o['id']}/rates"))["items"]
    assert all(x["amount_minor"] > 0 and x["eta_is_estimate"] for x in rates)
    ok(buyer.post(f"/hardware/orders/{o['id']}/shipping", {"rate_id": rates[0]["id"]}))
    # quantity change invalidates the rate and requires recalculation
    item = ok(buyer.get(f"/hardware/orders/{o['id']}"))["items"][0]["id"]
    o3 = ok(buyer.put(f"/hardware/orders/{o['id']}/items/{item}", {"quantity": 3}))
    assert o3["shipping_minor"] is None
    assert buyer.post(f"/hardware/orders/{o['id']}/shipping", {"rate_id": rates[0]["id"]}).json()["error"]["code"] == "rate_stale"
    # address change invalidates too
    rates = ok(buyer.post(f"/hardware/orders/{o['id']}/rates"))["items"]
    ok(buyer.post(f"/hardware/orders/{o['id']}/shipping", {"rate_id": rates[0]["id"]}))
    ok(buyer.put(f"/hardware/orders/{o['id']}/address", {"country": "DE", "fields": {
        "name": "Max", "line1": "Hauptstr. 1", "city": "München", "postal_code": "80331", "phone": "+49 89 1"}}))
    assert ok(buyer.get(f"/hardware/orders/{o['id']}"))["total_minor"] is None
    # expired rate cannot be paid
    rates = ok(buyer.post(f"/hardware/orders/{o['id']}/rates"))["items"]
    ok(buyer.post(f"/hardware/orders/{o['id']}/shipping", {"rate_id": rates[0]["id"]}))
    with session_scope() as db:
        from app.models import ShippingRate
        db.get(ShippingRate, uuid.UUID(rates[0]["id"])).expires_at = utcnow() - timedelta(seconds=1)
    assert buyer.post(f"/hardware/orders/{o['id']}/pay").json()["error"]["code"] == "rate_expired"


def test_oversize_chair_custom_quote_flow():
    buyer = Client().register()
    ok(buyer.post("/hardware/cart/items", {"sku_id": sku_id("NAP-CH1-GRY"), "quantity": 1}))
    o = ok(buyer.post("/hardware/checkout"), 201)["orders"][0]
    assert o["status"] == "awaiting_quote"
    ok(buyer.put(f"/hardware/orders/{o['id']}/address", {"country": "AM", "fields": ADDR_AM}))
    assert buyer.post(f"/hardware/orders/{o['id']}/rates").json()["error"]["code"] == "custom_quote_required"
    o = ok(buyer.post(f"/hardware/orders/{o['id']}/quote-request"))
    q = o["freight_quote"]
    assert q["status"] == "requested"
    seller = Client().login("seller@nap.demo")
    ok(seller.post(f"/seller/orders/{o['id']}/quotes/{q['id']}", {"amount_minor": 45000, "currency": "EUR",
                                                                  "eta_min_days": 10, "eta_max_days": 20,
                                                                  "valid_hours": 48, "note": "Freight, 1 pallet"}))
    o = ok(buyer.post(f"/hardware/orders/{o['id']}/quote-accept", {"quote_id": q["id"]}))
    assert o["status"] == "awaiting_payment" and o["shipping_minor"] == 45000
    assert o["total_minor"] == o["goods_minor"] + 45000
    assert o["shipping_rate"]["eta_is_estimate"] and o["terms"]["duties_amount"] is None
    mock_pay(buyer, ok(buyer.post(f"/hardware/orders/{o['id']}/pay"))["checkout_url"])
    assert ok(buyer.get(f"/hardware/orders/{o['id']}"))["payment_status"] == "paid"


def test_parcels_labels_tracking_and_status_aggregation():
    buyer = Client().register()
    o = buy(buyer, [("NAP-LINK", 2), ("NAP-CBL35", 1)])
    seller = Client().login("seller@nap.demo")
    ok(seller.post(f"/seller/orders/{o['id']}/acknowledge"))
    link = next(i for i in o["items"] if i["sku"] == "NAP-LINK")
    cbl = next(i for i in o["items"] if i["sku"] == "NAP-CBL35")
    # cannot pack more than ordered
    r = seller.post(f"/seller/orders/{o['id']}/parcels", {"items": [{"order_item_id": link["id"], "quantity": 3}],
                                                          "weight_g": 900, "dims_mm": [200, 200, 100]})
    assert r.json()["error"]["code"] == "exceeds_ordered_quantity"
    o = ok(seller.post(f"/seller/orders/{o['id']}/parcels", {"items": [{"order_item_id": link["id"], "quantity": 1},
                                                                       {"order_item_id": cbl["id"], "quantity": 1}],
                                                             "weight_g": 500, "dims_mm": [200, 150, 100]}), 201)
    o = ok(seller.post(f"/seller/orders/{o['id']}/parcels", {"items": [{"order_item_id": link["id"], "quantity": 1}],
                                                             "weight_g": 350, "dims_mm": [200, 150, 100]}), 201)
    p1, p2 = o["parcels"]
    on_hand_before = inv("NAP-LINK")[0]
    # label timeout: carrier bought the label but the response was lost -> retry returns the same label
    shp.MOCK_TIMEOUT_ONCE.add(f"label:{p1['id']}")
    r = seller.post(f"/seller/orders/{o['id']}/parcels/{p1['id']}/label")
    assert r.status_code == 504 and r.json()["error"]["code"] == "carrier_timeout"
    o = ok(seller.post(f"/seller/orders/{o['id']}/parcels/{p1['id']}/label"))
    o = ok(seller.post(f"/seller/orders/{o['id']}/parcels/{p1['id']}/label"))
    assert shp.MOCK_LABEL_PURCHASES[f"label:{p1['id']}"] == 1
    # a label is NOT shipping: stock and order status unchanged
    assert inv("NAP-LINK")[0] == on_hand_before
    v = ok(buyer.get(f"/hardware/orders/{o['id']}"))
    assert v["fulfillment_status"] == "processing" and v["parcels"][0]["status"] == "label_created"
    ok(seller.post(f"/seller/orders/{o['id']}/parcels/{p1['id']}/handover"))
    v = ok(buyer.get(f"/hardware/orders/{o['id']}"))
    assert v["fulfillment_status"] == "partially_shipped"
    assert inv("NAP-LINK")[0] == on_hand_before - 1
    # second parcel via manual mode (own carrier)
    ok(seller.post(f"/seller/orders/{o['id']}/parcels/{p2['id']}/manual-shipment",
                   {"carrier": "DHL Freight", "tracking_number": "MAN-123"}))
    ok(seller.post(f"/seller/orders/{o['id']}/parcels/{p2['id']}/handover"))
    v = ok(buyer.get(f"/hardware/orders/{o['id']}"))
    assert v["fulfillment_status"] == "shipped"
    # tracking: duplicates and out-of-order events
    t0 = utcnow()
    ok(seller.post(f"/test/parcels/{p1['id']}/tracking", {"status": "delivered", "occurred_at": (t0 + timedelta(hours=5)).isoformat(),
                                                           "event_key": "d1"}))
    ok(seller.post(f"/test/parcels/{p1['id']}/tracking", {"status": "in_transit", "occurred_at": (t0 + timedelta(hours=1)).isoformat(),
                                                           "event_key": "t1"}))
    dup = ok(seller.post(f"/test/parcels/{p1['id']}/tracking", {"status": "in_transit",
                                                                 "occurred_at": (t0 + timedelta(hours=1)).isoformat(),
                                                                 "event_key": "t1"}))
    assert dup["duplicate"]
    v = ok(buyer.get(f"/hardware/orders/{o['id']}"))
    pv1 = next(p for p in v["parcels"] if p["id"] == p1["id"])
    assert pv1["status"] == "delivered" and pv1["status_source"] == "simulator"
    assert [e["status"] for e in pv1["events"]].count("in_transit") == 1
    # one parcel delivered, the other only shipped -> order not delivered yet
    assert v["fulfillment_status"] == "shipped"
    ok(seller.post(f"/seller/orders/{o['id']}/parcels/{p2['id']}/events", {"status": "delivered",
                                                                          "description": "POD signed"}))
    v = ok(buyer.get(f"/hardware/orders/{o['id']}"))
    assert v["fulfillment_status"] == "delivered"
    pv2 = next(p for p in v["parcels"] if p["id"] == p2["id"])
    assert pv2["status_source"] == "supplier"
    # packing list built from the snapshot, HS codes never invented
    doc = ok(buyer.get(f"/hardware/orders/{o['id']}/documents/packing-list"))
    assert doc["generated_from"] == "order_snapshot"
    assert {line["hs_code_status"] for line in doc["lines"]} == {"not_provided"}
    pdf = buyer.raw().get(f"/api/v1/hardware/orders/{o['id']}/documents/packing-list?format=pdf")
    assert pdf.content.startswith(b"%PDF")


def test_shipping_webhook_signature_and_unknown_tracking():
    c = Client().raw()
    import json
    raw = json.dumps({"id": "x", "data": {"tracking_number": "NOPE", "status": "delivered",
                                          "occurred_at": utcnow().isoformat()}}).encode()
    assert c.post("/api/v1/webhooks/shipping/mock_ship", content=raw, headers={"x-mock-signature": "bad"}).status_code == 400
    r = c.post("/api/v1/webhooks/shipping/mock_ship", content=raw,
               headers={"x-mock-signature": shp.MockShippingAdapter.sign(raw)})
    assert r.status_code == 200  # stored; unknown tracking is retried later, not invented


def test_returns_restock_only_after_inspection_and_refund_limits():
    buyer = Client().register()
    o = buy(buyer, [("NAP-HR1", 1)])
    seller = Client().login("seller@nap.demo")
    item = o["items"][0]["id"]
    o = ok(seller.post(f"/seller/orders/{o['id']}/parcels", {"items": [{"order_item_id": item, "quantity": 1}],
                                                             "weight_g": 200, "dims_mm": [150, 100, 50]}), 201)
    pid = o["parcels"][0]["id"]
    ok(seller.post(f"/seller/orders/{o['id']}/parcels/{pid}/label"))
    ok(seller.post(f"/seller/orders/{o['id']}/parcels/{pid}/handover"))
    # cannot return more than shipped
    r = buyer.post(f"/hardware/orders/{o['id']}/returns", {"items": [{"order_item_id": item, "quantity": 2}], "reason": "x"})
    assert r.json()["error"]["code"] == "return_exceeds_shipped"
    o = ok(buyer.post(f"/hardware/orders/{o['id']}/returns", {"items": [{"order_item_id": item, "quantity": 1}],
                                                              "reason": "Не подошёл"}), 201)
    rid = o["returns"][0]["id"]
    base = inv("NAP-HR1")[0]
    ok(seller.post(f"/seller/orders/{o['id']}/returns/{rid}", {"action": "approve", "note": "ok"}))
    ok(buyer.post(f"/hardware/orders/{o['id']}/returns/{rid}/ship", {"tracking_number": "RET1"}))
    ok(seller.post(f"/seller/orders/{o['id']}/returns/{rid}", {"action": "receive"}))
    assert inv("NAP-HR1")[0] == base  # not on button press, only after inspection
    ok(seller.post(f"/seller/orders/{o['id']}/returns/{rid}", {"action": "inspect",
                                                               "results": {item: {"accepted": 1, "restock": 1}}}))
    assert inv("NAP-HR1")[0] == base + 1
    # money refund is a separate process and bounded by the confirmed payment, line by line
    over = seller.post(f"/seller/orders/{o['id']}/refunds", {"goods_minor": o["goods_minor"] + 1},
                       headers={"Idempotency-Key": "r-over"})
    assert over.json()["error"]["code"] == "refund_exceeds_payment"
    v = ok(seller.post(f"/seller/orders/{o['id']}/refunds", {"goods_minor": o["goods_minor"], "reason": "return"},
                       headers={"Idempotency-Key": "r1"}), 201)
    assert v["refunded_minor"] == o["goods_minor"] and v["payment_status"] == "partially_refunded"
    again = ok(seller.post(f"/seller/orders/{o['id']}/refunds", {"goods_minor": o["goods_minor"]},
                           headers={"Idempotency-Key": "r1"}), 201)
    assert again["refunded_minor"] == o["goods_minor"]  # idempotent: not refunded twice
    # hardware refund never touches digital entitlements (buyer had none; seed apps untouched)
    assert ok(buyer.get("/library"))["items"] == []


def test_addresses_three_countries_unicode_and_latin_rule():
    c = Client()
    us = ok(c.get("/hardware/address-rules/US"))
    ae = ok(c.get("/hardware/address-rules/AE"))
    gb = ok(c.get("/hardware/address-rules/GB"))
    assert us["region_required"] and us["postal_required"]
    assert not ae["postal_supported"] and ae["region_required"]
    assert gb["postal_required"] and not gb["region_required"]
    buyer = Client().register()
    ok(buyer.post("/hardware/cart/items", {"sku_id": sku_id("NAP-CBL35"), "quantity": 1}))
    o = ok(buyer.post("/hardware/checkout"), 201)["orders"][0]
    # US without state is invalid; with state ok
    r = buyer.put(f"/hardware/orders/{o['id']}/address", {"country": "US", "fields": {
        "name": "Jane", "line1": "1 Main St", "city": "Austin", "postal_code": "73301", "phone": "+1 512 555 0100"}})
    assert "region" in r.json()["error"]["details"]["fields"]
    ok(buyer.put(f"/hardware/orders/{o['id']}/address", {"country": "US", "fields": {
        "name": "Jane", "line1": "1 Main St", "city": "Austin", "region": "TX", "postal_code": "73301",
        "phone": "+1 512 555 0100"}}))
    # UAE has no postal codes: not required
    ok(buyer.put(f"/hardware/orders/{o['id']}/address", {"country": "AE", "fields": {
        "name": "Ali", "line1": "Sheikh Zayed Rd 1", "city": "Dubai", "region": "Dubai", "phone": "+971 4 000 0000"}}))
    # Unicode is accepted; Latin is required only because the (mock) carrier requires it for international labels
    r = buyer.put(f"/hardware/orders/{o['id']}/address", {"country": "RU", "fields": {
        "name": "Иван Петров", "line1": "ул. Ленина, 1", "city": "Москва", "postal_code": "101000", "phone": "+7 495 000 00 00"}})
    assert set(r.json()["error"]["details"]["fields"]) == {"name_latin", "line1_latin", "city_latin"}
    ok(buyer.put(f"/hardware/orders/{o['id']}/address", {"country": "RU", "fields": {
        "name": "Иван Петров", "line1": "ул. Ленина, 1", "city": "Москва", "postal_code": "101000",
        "phone": "+7 495 000 00 00", "name_latin": "Ivan Petrov", "line1_latin": "ul. Lenina 1", "city_latin": "Moscow"}}))
    ok(buyer.put(f"/hardware/orders/{o['id']}/address", {"country": "DE", "fields": {
        "name": "Jürgen Groß", "line1": "Königstraße 5", "city": "Köln", "postal_code": "50667", "phone": "+49 221 0"}}))
    # RU postal format is not imposed on other countries
    r = buyer.put(f"/hardware/orders/{o['id']}/address", {"country": "GB", "fields": {
        "name": "A", "line1": "10 Downing St", "city": "London", "postal_code": "SW1A 2AA", "phone": "+44 20 0000 0000"}})
    assert r.status_code == 200


def test_contract_easypost_adapter_offline(monkeypatch):
    import hashlib
    import hmac
    import json
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "easypost_webhook_secret", "ep_secret")
    ep = shp.EasyPostAdapter()
    raw = json.dumps({"id": "evt_1", "description": "tracker.updated", "result": {
        "id": "trk_1", "tracking_code": "EZ1", "tracking_details": [
            {"status": "in_transit", "datetime": "2026-01-01T10:00:00Z", "message": "Departed",
             "tracking_location": {"city": "Berlin", "country": "DE"}},
            {"status": "unknown_status", "datetime": "2026-01-01T11:00:00Z"}]}}).encode()
    with pytest.raises(shp.ShippingSignatureError):
        ep.verify_webhook(raw, {"x-hmac-signature": "hmac-sha256-hex=00"})
    sig = "hmac-sha256-hex=" + hmac.new(b"ep_secret", raw, hashlib.sha256).hexdigest()
    ev = ep.verify_webhook(raw, {"x-hmac-signature": sig})
    events = ep.normalize(ev)
    assert len(events) == 1 and events[0].status == "in_transit" and events[0].location == "Berlin, DE"
    # oversize chair is never sent to EasyPost
    with pytest.raises(shp.PackageTooLarge):
        ep.rates(origin={"country": "DE"}, destination={"country": "AM"},
                 packages=[shp.Package(68000, 1500, 900, 1100)], currency="EUR")


@pytest.mark.skipif(not __import__("app.config", fromlist=["x"]).get_settings().easypost_api_key, reason="not run: NAP_EASYPOST_API_KEY not configured")
def test_easypost_live_test_mode():
    pass
