"""NAP-104 (spec v2.1, M5): Secure Player — protected .napx packages, device keys and signed offline leases.

T13: a package plays only with a lease for this device; any change to the file is detected.
T14: offline leases are bound to account, device and term; revoked devices and lost access stop playback.
"""
import base64
import io
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from app.secure import napx

from .conftest import Client, ok
from .helpers import mock_pay


def _device_key():
    priv = ec.generate_private_key(ec.SECP256R1())
    spki = priv.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return priv, base64.b64encode(spki).decode()


def _buyer_free():
    b = Client().register()
    app = ok(b.get("/catalog/apps/night-stories"))
    ok(b.post("/checkout/digital", {"app_id": app["id"], "kind": "free"}), 201)
    return b, app


def _register(c, name="Laptop"):
    priv, pub = _device_key()
    return priv, ok(c.post("/me/license-devices", {"public_key": pub, "name": name}), 201)


def _lease(c, dev, app, key="harbor-night", code=201):
    r = c.post("/me/leases", {"device_id": dev["id"], "app_id": app["id"], "session_key": key})
    return ok(r, code) if code < 400 else r


def _open(c, priv, out, data=None):
    """What the player does offline: check signatures, unseal the key, decrypt the package."""
    pub = Ed25519PublicKey.from_public_bytes(napx.unb64u(ok(c.get("/licenses/public-key"))["x"]))
    payload = napx.verify_lease(pub, out["lease"], now=int(datetime.now(timezone.utc).timestamp()))
    cek = napx.open_sealed(priv, payload["key"], payload["lid"])
    data = data if data is not None else c.raw().get(out["package"]["url"]).content
    f = io.BytesIO(data)
    pkg = napx.read_header(f)
    napx.verify_manifest(pkg, pub)
    assert pkg.manifest_sha256 == payload["manifest_sha256"]
    return b"".join(napx.iter_plaintext(f, pkg, cek)), payload, data


def test_t13_package_plays_with_lease_and_equals_the_delivery_file():
    b, app = _buyer_free()
    priv, dev = _register(b)
    out = _lease(b, dev, app)
    plain, payload, data = _open(b, priv, out)
    s = ok(b.post("/devices", {"adapter": "web_headphones"}), 201)
    sess = ok(b.post("/sessions", {"app_id": app["id"], "session_key": "harbor-night", "device_id": s["id"]}), 201)
    grant = ok(b.post(f"/sessions/{sess['id']}/grant"))
    original = b.raw().get(grant["asset"]["url"]).content
    assert plain == original and payload["sha256"] == grant["asset"]["sha256"]
    # the package is not the audio: no FLAC header, the plaintext does not appear in it
    assert data[:4] == b"NAPX" and b"fLaC" not in data and original[1000:1064] not in data
    assert out["package"]["sha256"] and out["package"]["size_bytes"] == len(data)
    assert payload["dev"] == dev["id"] and payload["sub"] == b.user_id and payload["exp"] > payload["iat"]
    # one package per delivery file, each listener / lease gets the same encrypted file
    b2, _ = _buyer_free()
    priv2, dev2 = _register(b2)
    out2 = _lease(b2, dev2, app)
    assert out2["package"]["sha256"] == out["package"]["sha256"]
    assert _open(b2, priv2, out2)[0] == original


def test_t13_any_change_to_the_package_is_detected():
    signer = Ed25519PrivateKey.generate()
    cek = bytes(range(32))
    src = bytes((i * 7) % 251 for i in range(10_000))
    import hashlib
    buf = io.BytesIO()
    napx.write_package(io.BytesIO(src), buf, cek=cek, signer=signer, content_id="c1",
                       sha256=hashlib.sha256(src).hexdigest(), size=len(src), content_type="audio/flac",
                       key_id="k", segment_size=4096)
    good = buf.getvalue()

    def play(data, key=cek):
        f = io.BytesIO(data)
        pkg = napx.read_header(f)
        napx.verify_manifest(pkg, signer.public_key())
        return b"".join(napx.iter_plaintext(f, pkg, key))

    assert play(good) == src
    head = napx.read_header(io.BytesIO(good)).data_offset
    seg = 4096 + 16
    flipped = bytearray(good)
    flipped[head + 5000] ^= 1
    swapped = good[:head] + good[head + seg:head + 2 * seg] + good[head:head + seg] + good[head + 2 * seg:]
    cases = {"tampered": [bytes(flipped), swapped], "truncated": [good[:-seg], good[:-1]],
             "trailing_data": [good + b"x"]}
    for code, variants in cases.items():
        for data in variants:
            with pytest.raises(napx.NapxError) as e:
                play(data)
            assert e.value.code == code
    # a changed manifest (e.g. bigger size) fails the signature, and without the check every segment fails
    edited = good.replace(b'"size":10000', b'"size":10001')
    with pytest.raises(napx.NapxError) as e:
        play(edited)
    assert e.value.code == "bad_signature"
    f = io.BytesIO(edited)
    pkg = napx.read_header(f)
    with pytest.raises(napx.NapxError):
        b"".join(napx.iter_plaintext(f, pkg, cek))
    with pytest.raises(napx.NapxError):
        play(good, key=bytes(32))
    with pytest.raises(napx.NapxError) as e:
        play(b"fLaC" + good[4:])
    assert e.value.code == "not_napx"


def test_t14_lease_is_bound_to_the_device_and_signed():
    b, app = _buyer_free()
    priv, dev = _register(b)
    out = _lease(b, dev, app)
    other, _ = _device_key()
    with pytest.raises(napx.NapxError) as e:
        _open(b, other, out)
    assert e.value.code == "wrong_device"
    # a lease edited to live longer loses its signature
    h, p, s = out["lease"].split(".")
    import json
    payload = json.loads(napx.unb64u(p))
    payload["exp"] += 10 ** 7
    forged = ".".join([h, napx.b64u(napx.canonical(payload)), s])
    with pytest.raises(napx.NapxError) as e:
        _open(b, priv, out | {"lease": forged})
    assert e.value.code == "bad_lease"
    # an expired lease does not open (the device clock decides offline — documented limit)
    pub = Ed25519PublicKey.from_public_bytes(napx.unb64u(out["signing_key"]["x"]))
    with pytest.raises(napx.NapxError) as e:
        napx.verify_lease(pub, out["lease"], now=json.loads(napx.unb64u(p))["exp"])
    assert e.value.code == "lease_expired"
    # another account cannot use this device or read the lease status
    stranger = Client().register()
    ok(stranger.post("/checkout/digital", {"app_id": app["id"], "kind": "free"}), 201)
    assert _lease(stranger, dev, app, code=404).status_code == 404
    assert stranger.get(f"/me/leases/{out['lease_id']}").status_code == 404
    assert ok(b.get(f"/me/leases/{out['lease_id']}"))["status"] == "active"


def test_t14_no_access_no_lease_and_revoked_device_stops():
    b = Client().register()
    priv, dev = _register(b)
    paid = ok(b.get("/catalog/apps/quiet-harbor"))
    r = _lease(b, dev, paid, key="arrival", code=403)
    assert r.json()["error"]["code"] == "no_entitlement"
    b, app = _buyer_free()
    priv, dev = _register(b)
    out = _lease(b, dev, app)
    assert ok(b.get("/me/license-devices"))["items"][0]["active_leases"] == 1
    ok(b.delete(f"/me/license-devices/{dev['id']}"))
    st = ok(b.get(f"/me/leases/{out['lease_id']}"))
    assert st["status"] == "revoked" and st["reason"] == "device_revoked"
    assert _lease(b, dev, app, code=409).json()["error"]["code"] == "license_device_revoked"
    # the same key cannot quietly come back
    pub = base64.b64encode(priv.public_key().public_bytes(serialization.Encoding.DER,
                                                          serialization.PublicFormat.SubjectPublicKeyInfo)).decode()
    assert b.post("/me/license-devices", {"public_key": pub}).json()["error"]["code"] == "license_device_revoked"
    # registering an active key again returns the same device
    priv2, dev2 = _register(b)
    pub2 = base64.b64encode(priv2.public_key().public_bytes(serialization.Encoding.DER,
                                                            serialization.PublicFormat.SubjectPublicKeyInfo)).decode()
    assert ok(b.post("/me/license-devices", {"public_key": pub2}), 201)["id"] == dev2["id"]
    bad = b.post("/me/license-devices", {"public_key": base64.b64encode(b"x" * 64).decode()})
    assert bad.status_code == 422


def test_device_limit(monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "license_max_devices", 2)
    b = Client().register()
    _register(b, "a")
    _, d2 = _register(b, "b")
    priv, pub = _device_key()
    r = b.post("/me/license-devices", {"public_key": pub})
    assert r.json()["error"]["code"] == "license_device_limit" and r.json()["error"]["details"]["limit"] == 2
    ok(b.delete(f"/me/license-devices/{d2['id']}"))
    ok(b.post("/me/license-devices", {"public_key": pub}), 201)


def test_subscription_lease_ends_with_the_paid_period():
    b = Client().register()
    app = ok(b.get("/catalog/apps/focus-course"))
    mock_pay(b, ok(b.post("/checkout/digital", {"app_id": app["id"], "kind": "subscription"}), 201)["checkout_url"])
    sub = ok(b.get("/subscriptions"))["items"][0]
    _, dev = _register(b)
    out = _lease(b, dev, app, key="lesson-1")
    paid_until = datetime.fromisoformat(sub["paid_until"])
    exp = datetime.fromisoformat(out["expires_at"])
    assert exp <= paid_until + timedelta(seconds=1) and exp < datetime.now(timezone.utc) + timedelta(days=31)
    assert out["playback"]["channels"] == 2 and out["playback"]["layout"] == "headphones"
    unknown = b.post("/me/leases", {"device_id": dev["id"], "app_id": app["id"], "session_key": "lesson-1",
                                    "layout": "surround_9_1"})
    assert unknown.status_code == 422


def test_browser_fixture_is_current_format():
    """frontend/scripts/fixtures/napx-v1.json (tested in Node against lib/napx.ts) must open with the server code."""
    import hashlib
    import json
    import os
    fx = json.load(open(os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "scripts", "fixtures",
                                     "napx-v1.json")))
    pub = Ed25519PublicKey.from_public_bytes(napx.unb64u(fx["signing_key"]["x"]))
    device = serialization.load_der_private_key(base64.b64decode(fx["device_pkcs8"]), None)
    payload = napx.verify_lease(pub, fx["lease"], now=int(datetime.now(timezone.utc).timestamp()))
    f = io.BytesIO(base64.b64decode(fx["package"]))
    pkg = napx.read_header(f)
    napx.verify_manifest(pkg, pub)
    plain = b"".join(napx.iter_plaintext(f, pkg, napx.open_sealed(device, payload["key"], payload["lid"])))
    assert hashlib.sha256(plain).hexdigest() == fx["plaintext_sha256"] == payload["sha256"]
