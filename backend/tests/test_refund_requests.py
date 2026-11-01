"""NAP-024: the buyer requests a refund from the profile, the admin decides, the money goes through the provider."""
from .conftest import Client, ok
from .helpers import mock_pay
from .test_billing import _paid_order, _share


def _purchase(buyer, order_id):
    return next(x for x in ok(buyer.get("/purchases"))["items"] if x["id"] == order_id)


def test_request_approved_partially_goes_through_provider_and_shows_in_profile():
    dev, buyer, pub, co = _paid_order(10000)
    oid = co["order_id"]
    # not paid yet: no refund request
    assert buyer.post(f"/purchases/{oid}/refund-request", {"reason": "Не подошло звучание"}).status_code == 409
    mock_pay(buyer, co["checkout_url"])
    assert _purchase(buyer, oid)["can_request_refund"]
    # a reason is required
    assert buyer.post(f"/purchases/{oid}/refund-request", {"reason": "нет"}).status_code == 422
    # other users cannot request on this order
    assert Client().register().post(f"/purchases/{oid}/refund-request",
                                    {"reason": "чужой заказ, пробую вернуть"}).status_code == 404
    req = ok(buyer.post(f"/purchases/{oid}/refund-request", {"reason": "Программа не работает на моих наушниках"}), 201)
    assert req["status"] == "requested" and req["amount_minor"] == 10000
    assert buyer.post(f"/purchases/{oid}/refund-request", {"reason": "ещё раз, на всякий случай"}).json()["error"]["code"] \
        == "refund_request_exists"
    p = _purchase(buyer, oid)
    assert p["refund_request"]["status"] == "requested" and not p["can_request_refund"]
    # requested money is not moved yet
    assert _share(oid) == (3000, 7000)

    admin = Client().login("admin@nap.demo")
    assert buyer.get("/admin/refund-requests").status_code == 403
    queue = ok(admin.get("/admin/refund-requests"))["items"]
    item = next(x for x in queue if x["id"] == req["id"])
    assert item["buyer"]["email"] and item["order"]["amount_minor"] == 10000
    assert "Программа" in item["reason"]
    # approve 40.00 of 100.00 — the mock provider confirms right away
    done = ok(admin.post(f"/admin/refund-requests/{req['id']}/decision",
                         {"approve": True, "amount_minor": 4000, "note": "Частичный возврат"}))
    assert done["status"] in ("pending", "succeeded")
    assert admin.post(f"/admin/refund-requests/{req['id']}/decision", {"approve": False, "note": "x" * 5}).status_code == 409
    p = _purchase(buyer, oid)
    assert p["refund_request"]["status"] == "succeeded" and p["refunded_minor"] == 4000
    assert p["refund_request"]["note"] == "Частичный возврат" and p["status"] == "partially_refunded"
    assert _share(oid) == (1800, 4200)
    subjects = [n["subject"] for n in ok(buyer.get("/me/notifications"))["items"]]
    assert any("одобрен" in s or "approved" in s for s in subjects)
    # the rest can be requested again
    assert p["can_request_refund"]
    assert ok(admin.get("/admin/refund-requests?status=succeeded"))["items"]


def test_request_rejected_with_reason_keeps_access():
    dev, buyer, pub, co = _paid_order(5000)
    mock_pay(buyer, co["checkout_url"])
    oid = co["order_id"]
    req = ok(buyer.post(f"/purchases/{oid}/refund-request", {"reason": "Передумал, хочу вернуть деньги"}), 201)
    admin = Client().login("admin@nap.demo")
    # rejecting needs a reason the buyer will read
    assert admin.post(f"/admin/refund-requests/{req['id']}/decision", {"approve": False}).status_code == 422
    ok(admin.post(f"/admin/refund-requests/{req['id']}/decision",
                  {"approve": False, "note": "Сеанс прослушан полностью трижды"}))
    p = _purchase(buyer, oid)
    assert p["refund_request"]["status"] == "rejected"
    assert p["refund_request"]["note"] == "Сеанс прослушан полностью трижды"
    assert p["refunded_minor"] == 0 and p["status"] == "paid"
    assert ok(buyer.get(f"/catalog/apps/{pub['slug']}"))["access"]["has_access"]
    # after a rejection the buyer may write again (e.g. with more details)
    assert p["can_request_refund"]


def test_full_refund_request_revokes_one_time_access():
    dev, buyer, pub, co = _paid_order(3000)
    mock_pay(buyer, co["checkout_url"])
    req = ok(buyer.post(f"/purchases/{co['order_id']}/refund-request", {"reason": "Купил по ошибке два раза"}), 201)
    admin = Client().login("admin@nap.demo")
    # cannot approve more than requested
    assert admin.post(f"/admin/refund-requests/{req['id']}/decision",
                      {"approve": True, "amount_minor": 3001}).status_code == 422
    ok(admin.post(f"/admin/refund-requests/{req['id']}/decision", {"approve": True}))
    p = _purchase(buyer, co["order_id"])
    assert p["status"] == "refunded" and not p["can_request_refund"]
    assert not ok(buyer.get(f"/catalog/apps/{pub['slug']}"))["access"]["has_access"]
