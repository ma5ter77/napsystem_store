"""NAP-104: Secure Player API — listener devices with keys, offline leases for protected .napx packages."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..services import licenses as svc
from .deps import DB, P, as_uuid

router = APIRouter(tags=["secure player"])


class LicenseDeviceIn(BaseModel):
    public_key: str = Field(min_length=40, max_length=400)   # SPKI DER, base64 — P-256 key made by the device
    name: str = Field(default="", max_length=120)
    platform: str = Field(default="web", max_length=40)


class LeaseIn(BaseModel):
    device_id: str
    app_id: str
    session_key: str = Field(min_length=1, max_length=64)
    layout: str = "headphones"


@router.get("/licenses/public-key")
def signing_key():
    """Ed25519 key that signs package manifests and leases (the player checks signatures with it)."""
    return svc.public_key()


@router.get("/me/license-devices")
def devices(p=P, db=DB):
    return {"items": svc.list_devices(db, db.merge(p.require_user()))}


@router.post("/me/license-devices", status_code=201)
def register(body: LicenseDeviceIn, p=P, db=DB):
    d, _ = svc.register_device(db, db.merge(p.require_user()), body.public_key, body.name, body.platform)
    db.commit()
    return svc.device_view(d)


@router.delete("/me/license-devices/{device_id}")
def revoke(device_id: str, p=P, db=DB):
    d = svc.revoke_device(db, db.merge(p.require_user()), as_uuid(device_id, "device"))
    db.commit()
    return svc.device_view(d)


@router.post("/me/leases", status_code=201)
def lease(body: LeaseIn, p=P, db=DB):
    out = svc.issue_lease(db, db.merge(p.require_user()), as_uuid(body.device_id, "device"),
                          as_uuid(body.app_id, "app"), body.session_key, body.layout)
    db.commit()
    return out


@router.get("/me/leases/{lease_id}")
def lease_status(lease_id: str, p=P, db=DB):
    out = svc.lease_status(db, db.merge(p.require_user()), as_uuid(lease_id, "lease"))
    db.commit()
    return out
