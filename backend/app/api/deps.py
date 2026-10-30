from __future__ import annotations

import uuid

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import ApiError
from ..security import Principal, get_principal

DB = Depends(get_db)
P = Depends(get_principal)


def locale_of(request: Request) -> str:
    return getattr(request.state, "locale", "ru")


def idem(idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> str | None:
    if idempotency_key is not None and not (1 <= len(idempotency_key) <= 128):
        raise ApiError("validation_failed", 422, details={"field": "Idempotency-Key"})
    return idempotency_key


def as_uuid(v: str, entity: str = "resource") -> uuid.UUID:
    try:
        return uuid.UUID(str(v))
    except ValueError:
        raise ApiError("not_found", 404, entity=entity)


def page(items: list, total: int, limit: int, offset: int) -> dict:
    return {"items": items, "total": total, "limit": limit, "offset": offset}
