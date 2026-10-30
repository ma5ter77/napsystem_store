"""ShippingAdapter contract + MockShippingAdapter (reproducible), ManualShippingAdapter, EasyPostAdapter (test).

The platform never invents HS codes, duty rates or allowed routes: unsupported routes and oversize cargo raise
explicit errors and are handled by a manual freight quote, never by a zero shipping price.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol

from ..config import get_settings


class RouteUnsupported(Exception):
    pass


class PackageTooLarge(Exception):
    pass


class ManualQuoteRequired(Exception):
    pass


class CarrierTimeout(Exception):
    pass


class ShippingSignatureError(Exception):
    pass


@dataclass
class Package:
    weight_g: int
    length_mm: int
    width_mm: int
    height_mm: int


@dataclass
class Rate:
    service_code: str
    carrier: str
    amount_minor: int
    currency: str
    eta_min_days: int
    eta_max_days: int
    external_ref: str | None = None
    insurance_minor: int | None = None


@dataclass
class ShipmentResult:
    external_id: str
    tracking_number: str
    carrier: str
    label_pdf: bytes
    label_purchased: bool = True


@dataclass
class TrackEvent:
    tracking_number: str
    external_id: str
    status: str
    occurred_at: datetime
    description: str = ""
    location: str | None = None
    source: str = "carrier"


class ShippingAdapter(Protocol):
    name: str
    test_mode: bool

    def services(self, origin_country: str, dest_country: str) -> list[str]: ...
    def requires_latin(self, origin_country: str, dest_country: str) -> bool: ...
    def rates(self, *, origin: dict, destination: dict, packages: list[Package], currency: str) -> list[Rate]: ...
    def create_shipment(self, *, idempotency_key: str, service_code: str, origin: dict, destination: dict,
                        package: Package, customs: list[dict], existing_external_id: str | None) -> ShipmentResult: ...
    def cancel_shipment(self, external_id: str) -> None: ...
    def track(self, tracking_number: str) -> list[TrackEvent]: ...
    def verify_webhook(self, raw: bytes, headers: dict[str, str]) -> dict: ...
    def event_id(self, event: dict) -> str: ...
    def normalize(self, event: dict) -> list[TrackEvent]: ...


def minimal_pdf(lines: list[str]) -> bytes:
    """Tiny valid one-page PDF (Helvetica, ASCII) used for test labels and documents."""
    def esc(s: str) -> str:
        s = unicodedata.normalize("NFKD", s).encode("ascii", "replace").decode()
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = "BT /F1 11 Tf 40 800 Td 14 TL " + " ".join(f"({esc(line)}) Tj T*" for line in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


# ------------------------------------------------------------------ mock

ZONES = {  # deterministic demo zones (origin DE). Clearly TEST tariffs.
    "z1": {"DE", "AT", "NL", "BE", "LU", "FR", "DK", "PL", "CZ"},
    "z2": {"IT", "ES", "PT", "SE", "FI", "IE", "GB", "CH", "NO", "EE", "LV", "LT", "SK", "SI", "HU", "GR", "RS", "AM",
           "GE", "KZ", "RU", "TR", "CY", "MT", "HR", "RO", "BG"},
    "z3": {"US", "CA", "AE", "IL", "JP", "KR", "SG", "HK", "AU", "NZ", "CN", "TW", "IN", "BR", "MX"},
}
ZONE_PRICE = {"z1": (990, 150, 2, 4), "z2": (1890, 260, 4, 9), "z3": (3490, 520, 6, 14)}  # base, per kg, eta
MAX_PKG_KG = 30
MAX_LEN_MM = 1200
MAX_GIRTH_MM = 3000

_mock_shipments: dict[str, ShipmentResult] = {}
MOCK_LABEL_PURCHASES: dict[str, int] = {}
MOCK_TIMEOUT_ONCE: set[str] = set()


def zone_of(country: str) -> str | None:
    for z, cs in ZONES.items():
        if country in cs:
            return z
    return None


def chargeable_kg(p: Package) -> int:
    vol = p.length_mm * p.width_mm * p.height_mm / 5_000_000  # cm^3/5000 -> kg
    return max(1, math.ceil(max(p.weight_g / 1000, vol)))


def check_package(p: Package) -> None:
    dims = sorted([p.length_mm, p.width_mm, p.height_mm], reverse=True)
    girth = dims[0] + 2 * (dims[1] + dims[2])
    if p.weight_g > MAX_PKG_KG * 1000 or dims[0] > MAX_LEN_MM or girth > MAX_GIRTH_MM:
        raise PackageTooLarge(f"{p.weight_g}g {dims}")


class MockShippingAdapter:
    name = "mock"
    test_mode = True

    def __init__(self):
        if get_settings().env == "production":
            raise RuntimeError("MockShippingAdapter is not available in production")

    def services(self, origin_country, dest_country):
        return ["standard", "express"] if zone_of(dest_country) else []

    def requires_latin(self, origin_country, dest_country):
        return origin_country != dest_country  # documented mock-carrier rule: international labels in Latin

    def rates(self, *, origin, destination, packages, currency):
        z = zone_of(destination["country"])
        if z is None or origin["country"] != "DE":
            raise RouteUnsupported(destination["country"])
        for p in packages:
            check_package(p)
        base, per_kg, emin, emax = ZONE_PRICE[z]
        std = sum(base + per_kg * chargeable_kg(p) for p in packages)
        if currency != "EUR":
            raise RouteUnsupported("currency")
        return [Rate("standard", "MockPost", std, currency, emin, emax, f"mock_rate_std_{z}"),
                Rate("express", "MockExpress", int(round(std * 1.8)), currency, max(1, emin // 2), max(2, emax // 2),
                     f"mock_rate_exp_{z}", insurance_minor=0)]

    def create_shipment(self, *, idempotency_key, service_code, origin, destination, package, customs,
                        existing_external_id):
        if idempotency_key in _mock_shipments:
            return _mock_shipments[idempotency_key]
        h = hashlib.sha256(idempotency_key.encode()).hexdigest()
        res = ShipmentResult(external_id=f"mock_shp_{h[:16]}", tracking_number=f"NAPMOCK{int(h[:10], 16) % 10**10:010d}",
                             carrier="MockExpress" if service_code == "express" else "MockPost",
                             label_pdf=minimal_pdf(["TEST LABEL - NOT VALID FOR SHIPPING", f"Service: {service_code}",
                                                    f"To: {destination.get('city', '')}, {destination['country']}",
                                                    f"Weight: {package.weight_g} g", f"Ref: {h[:16]}"]))
        _mock_shipments[idempotency_key] = res
        MOCK_LABEL_PURCHASES[idempotency_key] = MOCK_LABEL_PURCHASES.get(idempotency_key, 0) + 1
        if idempotency_key in MOCK_TIMEOUT_ONCE:  # carrier bought the label but the response was lost
            MOCK_TIMEOUT_ONCE.discard(idempotency_key)
            raise CarrierTimeout()
        return res

    def cancel_shipment(self, external_id):
        return None

    def track(self, tracking_number):
        return []

    def verify_webhook(self, raw, headers):
        key = hmac.new(get_settings().secret_key.encode(), b"mock-shipping-webhook", hashlib.sha256).digest()
        sig = headers.get("x-mock-signature", "")
        if not hmac.compare_digest(hmac.new(key, raw, hashlib.sha256).hexdigest(), sig):
            raise ShippingSignatureError()
        return json.loads(raw)

    @staticmethod
    def sign(raw: bytes) -> str:
        key = hmac.new(get_settings().secret_key.encode(), b"mock-shipping-webhook", hashlib.sha256).digest()
        return hmac.new(key, raw, hashlib.sha256).hexdigest()

    def event_id(self, event):
        return event["id"]

    def normalize(self, event):
        d = event["data"]
        return [TrackEvent(d["tracking_number"], event["id"], d["status"], datetime.fromisoformat(d["occurred_at"]),
                           d.get("description", ""), d.get("location"), "simulator")]


# ------------------------------------------------------------------ manual

class ManualShippingAdapter:
    """Supplier-confirmed quote and externally arranged shipment; the supplier uploads documents and the real
    tracking number. Status events entered by the supplier are marked source=supplier."""
    name = "manual"
    test_mode = False

    def services(self, origin_country, dest_country):
        return ["manual"]

    def requires_latin(self, origin_country, dest_country):
        return False

    def rates(self, *, origin, destination, packages, currency):
        raise ManualQuoteRequired()

    def create_shipment(self, **kw):
        raise ManualQuoteRequired()

    def cancel_shipment(self, external_id):
        return None

    def track(self, tracking_number):
        return []

    def verify_webhook(self, raw, headers):
        raise ShippingSignatureError("manual adapter has no webhooks")

    def event_id(self, event):
        return event["id"]

    def normalize(self, event):
        return []


# ------------------------------------------------------------------ EasyPost (test)

EASYPOST_STATUS = {"pre_transit": "label_created", "in_transit": "in_transit", "out_for_delivery": "out_for_delivery",
                   "delivered": "delivered", "available_for_pickup": "out_for_delivery",
                   "return_to_sender": "returned_to_sender", "failure": "exception", "error": "exception",
                   "cancelled": "cancelled"}


class EasyPostAdapter:
    """Reference adapter using the EasyPost REST API (test keys). Applicability to every country or to large
    chairs is NOT assumed — oversize cargo goes to a manual freight quote."""
    name = "easypost"
    base = "https://api.easypost.com/v2"

    def __init__(self):
        s = get_settings()
        self.key = s.easypost_api_key
        self.webhook_secret = s.easypost_webhook_secret
        self.test_mode = bool(self.key and self.key.startswith("EZTK")) or not self.key

    def _http(self):
        import httpx
        if not self.key:
            raise RouteUnsupported("easypost_not_configured")
        return httpx.Client(base_url=self.base, auth=(self.key, ""), timeout=20)

    @staticmethod
    def _addr(a: dict) -> dict:
        f = a.get("fields", a)
        return {"name": f.get("name_latin") or f.get("name"), "street1": f.get("line1_latin") or f.get("line1"),
                "street2": f.get("line2"), "city": f.get("city_latin") or f.get("city"), "state": f.get("region"),
                "zip": f.get("postal_code"), "country": a["country"], "phone": f.get("phone")}

    @staticmethod
    def _parcel(p: Package) -> dict:
        return {"length": round(p.length_mm / 25.4, 1), "width": round(p.width_mm / 25.4, 1),
                "height": round(p.height_mm / 25.4, 1), "weight": round(p.weight_g / 28.3495, 1)}

    def services(self, origin_country, dest_country):
        return ["carrier_rates"]

    def requires_latin(self, origin_country, dest_country):
        return True  # most carriers behind EasyPost accept ASCII address lines only

    def rates(self, *, origin, destination, packages, currency):
        if len(packages) != 1:
            raise ManualQuoteRequired("multi-package rating via EasyPost is not implemented in MVP")
        check_package(packages[0])
        with self._http() as c:
            r = c.post("/shipments", json={"shipment": {"from_address": self._addr(origin), "to_address":
                                                       self._addr(destination), "parcel": self._parcel(packages[0])}})
            r.raise_for_status()
            sh = r.json()
        out = []
        for rt in sh.get("rates", []):
            if rt.get("currency") != currency:
                continue
            days = rt.get("delivery_days") or rt.get("est_delivery_days")
            if days is None:
                continue  # no ETA -> do not present as a dated option
            out.append(Rate(rt["service"], rt["carrier"], int(round(float(rt["rate"]) * 100)), currency, int(days),
                            int(days) + 2, f"{sh['id']}:{rt['id']}"))
        if not out:
            raise RouteUnsupported("no rates in order currency")
        return out

    def create_shipment(self, *, idempotency_key, service_code, origin, destination, package, customs,
                        existing_external_id):
        with self._http() as c:
            if existing_external_id:  # retry after timeout: never buy twice
                sh = c.get(f"/shipments/{existing_external_id}").json()
                if sh.get("postage_label"):
                    return self._result(c, sh, purchased=False)
            else:
                body = {"shipment": {"reference": idempotency_key, "from_address": self._addr(origin),
                                     "to_address": self._addr(destination), "parcel": self._parcel(package)}}
                if customs:
                    body["shipment"]["customs_info"] = {"contents_type": "merchandise", "customs_items": customs,
                                                        "eel_pfc": "NOEEI 30.37(a)", "restriction_type": "none"}
                sh = c.post("/shipments", json=body).json()
            rate = next((r for r in sh.get("rates", []) if r["service"] == service_code), None)
            if not rate:
                raise RouteUnsupported("service no longer available")
            sh = c.post(f"/shipments/{sh['id']}/buy", json={"rate": {"id": rate["id"]}}).json()
            return self._result(c, sh, purchased=True)

    def _result(self, c, sh, purchased):
        label = c.get(sh["postage_label"]["label_url"]).content if sh.get("postage_label") else b""
        return ShipmentResult(sh["id"], sh.get("tracking_code") or "", sh.get("selected_rate", {}).get("carrier", ""),
                              label, purchased)

    def cancel_shipment(self, external_id):
        with self._http() as c:
            c.post(f"/shipments/{external_id}/refund")

    def track(self, tracking_number):
        return []

    def verify_webhook(self, raw, headers):
        if not self.webhook_secret:
            raise ShippingSignatureError("not configured")
        secret = unicodedata.normalize("NFKD", self.webhook_secret).encode()
        expected = "hmac-sha256-hex=" + hmac.new(secret, raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, headers.get("x-hmac-signature", "")):
            raise ShippingSignatureError()
        return json.loads(raw)

    def event_id(self, event):
        return event["id"]

    def normalize(self, event):
        if event.get("description") not in ("tracker.updated", "tracker.created"):
            return []
        tr = event.get("result", {})
        out = []
        for d in tr.get("tracking_details", []):
            st = EASYPOST_STATUS.get(d.get("status"))
            if not st:
                continue
            ts = datetime.fromisoformat(d["datetime"].replace("Z", "+00:00"))
            loc = d.get("tracking_location") or {}
            out.append(TrackEvent(tr["tracking_code"], f"{tr['id']}:{d['datetime']}:{d.get('status')}", st, ts,
                                  d.get("message", ""), ", ".join(x for x in [loc.get("city"), loc.get("country")] if x)))
        return out


_adapters: dict[str, ShippingAdapter] = {}


def get_shipping_adapter(name: str | None = None) -> ShippingAdapter:
    name = name or get_settings().shipping_adapter
    if name not in _adapters:
        _adapters[name] = {"mock": MockShippingAdapter, "manual": ManualShippingAdapter,
                           "easypost": EasyPostAdapter}[name]()
    return _adapters[name]


def reset_shipping() -> None:
    _adapters.clear()
    _mock_shipments.clear()
    MOCK_LABEL_PURCHASES.clear()
