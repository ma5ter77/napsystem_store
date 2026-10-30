"""Explicitly marked simulator endpoints. Not mounted in production."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from ..errors import ApiError
from ..models import HardwareOrder, Parcel, Subscription
from ..security import seller_ids_for
from ..services import billing
from ..services.hardware_orders import simulate_tracking
from .deps import DB, P, as_uuid

router = APIRouter(prefix="/test", tags=["test-mode simulators"])


class RenewIn(BaseModel):
    outcome: str = "success"  # success / fail


@router.post("/subscriptions/{sub_id}/renew")
def renew(sub_id: str, body: RenewIn, p=P, db=DB):
    u = p.require_user()
    sub = db.get(Subscription, as_uuid(sub_id, "subscription"))
    if not sub or (sub.user_id != u.id and not p.has("admin")):
        raise ApiError("not_found", 404, entity="subscription")
    if sub.cancel_at_period_end:
        raise ApiError("autorenew_disabled", 409)
    ev = billing.mock_invoice_event(db, sub.id, fail=body.outcome == "fail")
    return billing.deliver_mock_webhook(ev) | {"simulated": True}


class TrackIn(BaseModel):
    status: str
    occurred_at: datetime | None = None
    event_key: str | None = None


@router.post("/parcels/{parcel_id}/tracking")
def track(parcel_id: str, body: TrackIn, p=P, db=DB):
    u = p.require_user()
    pc = db.get(Parcel, as_uuid(parcel_id, "parcel"))
    if not pc:
        raise ApiError("not_found", 404, entity="parcel")
    o = db.get(HardwareOrder, pc.order_id)
    if not (p.has("admin") or o.seller_id in seller_ids_for(db, u.id)):
        raise ApiError("not_found", 404, entity="parcel")
    if not pc.tracking_number:
        raise ApiError("invalid_state", 409)
    return simulate_tracking(pc, body.status, body.occurred_at, body.event_key) | {"simulated": True}
