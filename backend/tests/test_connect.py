"""NAP-011: author payout onboarding — Stripe Connect Express (offline recorder) and the mock flow via the API."""
from app.adapters.payments import StripeProvider
from app.config import get_settings

from .conftest import Client, ok


class _Obj(dict):
    __getattr__ = dict.get


class _Fake:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return _Path(self, [name])


class _Path:
    def __init__(self, f, path):
        self.f, self.path = f, path

    def __getattr__(self, name):
        return _Path(self.f, self.path + [name])

    def __call__(self, *args, params=None, options=None):
        name = ".".join(self.path)
        self.f.calls.append((name, args, params or {}, options or {}))
        if name.endswith("accounts.create"):
            return _Obj(id="acct_test_1")
        if name.endswith("account_links.create"):
            return _Obj(url="https://connect.stripe.com/setup/e/acct_test_1/x")
        if name.endswith("accounts.retrieve"):
            return _Obj(details_submitted=True, payouts_enabled=False,
                        requirements=_Obj(currently_due=["external_account"]))
        return _Obj()


def test_stripe_express_onboarding_and_status(monkeypatch):
    monkeypatch.setattr(get_settings(), "stripe_secret_key", "sk_test_offline")
    p = StripeProvider()
    f = _Fake()
    monkeypatch.setattr(p, "_client", lambda: f)
    acct, url = p.connect_onboarding(user_id="u1", email="a@b.c", country="us", existing=None,
                                     refresh_url="https://s/r", return_url="https://s/d")
    assert (acct, url) == ("acct_test_1", "https://connect.stripe.com/setup/e/acct_test_1/x")
    name, _, params, opts = f.calls[0]
    assert name == "v1.accounts.create" and params["type"] == "express" and params["country"] == "US"
    assert params["capabilities"] == {"transfers": {"requested": True}} and opts["idempotency_key"] == "connect:u1"
    assert f.calls[1][2]["type"] == "account_onboarding" and f.calls[1][2]["return_url"] == "https://s/d"
    p.connect_onboarding(user_id="u1", email="a@b.c", country=None, existing="acct_test_1",
                         refresh_url="r", return_url="d")
    assert f.calls[2][0] == "v1.account_links.create"  # an existing account only gets a fresh link
    st = p.connect_status("acct_test_1")
    assert st == {"details_submitted": True, "payouts_enabled": False, "requirements_due": ["external_account"],
                  "test_mode": True}


def test_mock_connect_flow_via_api():
    dev = Client().register(developer=True)
    assert ok(dev.get("/developer/payouts/account")) == {"connected": False}
    r = ok(dev.post("/developer/payouts/connect", {"country": "US"}))
    assert "/developer?tab=finance&connect=done" in r["url"]
    st = ok(dev.get("/developer/payouts/account"))
    assert st["connected"] and st["payouts_enabled"]
    again = ok(dev.post("/developer/payouts/connect", {}))  # same account, no duplicate
    assert again["url"] == r["url"]


def test_connect_requires_developer_and_valid_country():
    buyer = Client().register()
    assert buyer.post("/developer/payouts/connect", {}).status_code in (401, 403)
    dev = Client().register(developer=True)
    assert dev.post("/developer/payouts/connect", {"country": "USA"}).status_code == 422
