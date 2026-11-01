"""NAP-023: saved addresses — picked with one action, the country rules are checked again for each order."""
from .conftest import Client, ok
from .test_hardware import ADDR_AM, sku_id

ADDR_RU = {"name": "Иван Петров", "line1": "ул. Ленина, 1", "city": "Москва", "postal_code": "101000",
           "phone": "+7 495 000 00 00"}


def _order(buyer):
    ok(buyer.post("/hardware/cart/items", {"sku_id": sku_id("NAP-CBL35"), "quantity": 1}))
    return ok(buyer.post("/hardware/checkout"), 201)["orders"][0]


def test_used_address_is_remembered_once_and_reused_with_one_action():
    buyer = Client().register()
    assert ok(buyer.get("/me/addresses"))["items"] == []
    o1 = _order(buyer)
    ok(buyer.put(f"/hardware/orders/{o1['id']}/address", {"country": "AM", "fields": ADDR_AM}))
    ok(buyer.put(f"/hardware/orders/{o1['id']}/address", {"country": "am", "fields": dict(ADDR_AM)}))
    book = ok(buyer.get("/me/addresses"))["items"]
    assert len(book) == 1 and book[0]["country"] == "AM" and book[0]["fields"]["city"] == "Yerevan"

    o2 = _order(buyer)
    got = ok(buyer.put(f"/hardware/orders/{o2['id']}/address", {"address_id": book[0]["id"]}))
    assert got["address"]["fields"]["line1"] == ADDR_AM["line1"]
    assert len(ok(buyer.get("/me/addresses"))["items"]) == 1


def test_saved_address_is_validated_again_for_the_order():
    buyer = Client().register()
    # stored without Latin fields: fine for the book, but the carrier needs Latin for this international label
    saved = ok(buyer.post("/me/addresses", {"country": "RU", "fields": ADDR_RU}), 201)
    o = _order(buyer)
    r = buyer.put(f"/hardware/orders/{o['id']}/address", {"address_id": saved["id"]})
    assert r.status_code == 422
    assert set(r.json()["error"]["details"]["fields"]) == {"name_latin", "line1_latin", "city_latin"}
    # invalid addresses never reach the book
    r = buyer.post("/me/addresses", {"country": "US", "fields": {"name": "J", "line1": "1 Main", "city": "Austin"}})
    assert r.status_code == 422
    assert len(ok(buyer.get("/me/addresses"))["items"]) == 1


def test_address_book_is_private_and_deletable():
    a, b = Client().register(), Client().register()
    saved = ok(a.post("/me/addresses", {"country": "AM", "fields": ADDR_AM}), 201)
    assert ok(b.get("/me/addresses"))["items"] == []
    o = _order(b)
    assert b.put(f"/hardware/orders/{o['id']}/address", {"address_id": saved["id"]}).status_code == 404
    assert b.delete(f"/me/addresses/{saved['id']}").status_code == 404
    assert a.delete(f"/me/addresses/{saved['id']}").status_code == 204
    assert ok(a.get("/me/addresses"))["items"] == []
    assert Client().get("/me/addresses").status_code == 401
