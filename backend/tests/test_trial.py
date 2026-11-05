"""NAP-064: a subscription with a free trial — the author sets the days, the buyer gets them once per app, access
starts at once without a charge, the first payment comes at the end of the trial."""
from .conftest import Client, ok
from .helpers import make_published_app, mock_pay


def test_trial_once_then_paid_periods():
    dev = Client().register(developer=True)
    mod = Client().login("moderator@nap.demo")
    pub = make_published_app(dev, mod, kind="subscription", amount=900, interval="month")
    for bad in (-1, 91):
        r = dev.put(f"/developer/apps/{pub['app_id']}/price", {"kind": "subscription", "amount_minor": 900,
                                                              "currency": "EUR", "interval": "month", "trial_days": bad})
        assert r.status_code == 422 and r.json()["error"]["details"]["field"] == "trial_days"
    ok(dev.put(f"/developer/apps/{pub['app_id']}/price", {"kind": "subscription", "amount_minor": 900, "currency": "EUR",
                                                         "interval": "month", "trial_days": 7}))
    assert ok(Client().get(f"/catalog/apps/{pub['slug']}"))["prices"][0]["trial_days"] == 7
    assert ok(dev.get(f"/developer/apps/{pub['app_id']}"))["prices"][0]["trial_days"] == 7

    buyer = Client().register()
    assert ok(buyer.get(f"/checkout/trial/{pub['app_id']}"))["trial_days"] == 7
    mock_pay(buyer, ok(buyer.post("/checkout/digital", {"app_id": pub["app_id"], "kind": "subscription"}), 201)["checkout_url"])
    sub = ok(buyer.get("/subscriptions"))["items"][0]
    assert sub["in_trial"] and sub["trial_end"] and sub["access"]["has_access"] and len(sub["periods"]) == 1
    notes = {n["template"] for n in ok(buyer.get("/me/notifications"))["items"]}
    assert "subscription_trial_started" in notes and "subscription_paid" not in notes
    # nothing was charged: no revenue for the author yet
    assert all(int(x.get("gross_minor") or 0) == 0 for x in ok(dev.get("/developer/finance"))["summary"])

    # a reminder a few days before the trial turns paid, once (terms 5.8)
    from datetime import datetime, timedelta
    from app.services.billing import remind_trials_ending
    soon = datetime.fromisoformat(sub["trial_end"]) - timedelta(days=1)
    assert remind_trials_ending(soon) >= 1
    remind_trials_ending(soon)
    assert [n["template"] for n in ok(buyer.get("/me/notifications"))["items"]].count("subscription_trial_ending") == 1

    # the renewal at the end of the trial charges the price
    ok(buyer.post(f"/test/subscriptions/{sub['id']}/renew", {"outcome": "success"}))
    sub = ok(buyer.get("/subscriptions"))["items"][0]
    assert len(sub["periods"]) == 2
    assert any(n["template"] == "subscription_paid" for n in ok(buyer.get("/me/notifications"))["items"])
    assert any(int(x.get("gross_minor") or 0) == 900 for x in ok(dev.get("/developer/finance"))["summary"])

    # one trial per buyer and app; a new buyer still gets it; one-time prices never have one
    assert ok(buyer.get(f"/checkout/trial/{pub['app_id']}"))["trial_days"] == 0
    assert ok(Client().register().get(f"/checkout/trial/{pub['app_id']}"))["trial_days"] == 7
    one = make_published_app(dev, mod)
    ok(dev.put(f"/developer/apps/{one['app_id']}/price", {"kind": "one_time", "amount_minor": 500, "currency": "EUR",
                                                         "trial_days": 7}))
    assert all(p["trial_days"] == 0 for p in ok(Client().get(f"/catalog/apps/{one['slug']}"))["prices"])
