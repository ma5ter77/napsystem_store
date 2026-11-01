from __future__ import annotations

import json
import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text

from . import ratelimit
from .config import get_settings
from .db import SessionLocal
from .errors import ApiError, api_error_handler, validation_handler
from .i18n import negotiate
from .services.common import request_id_var

REDACT_PARAMS = ("token", "sig", "key")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {"ts": self.formatTime(record), "level": record.levelname, "logger": record.name, "msg": record.getMessage(),
             "request_id": request_id_var.get()}
        for k in ("job_id", "path", "status", "duration_ms", "method", "code"):
            if hasattr(record, k):
                d[k] = getattr(record, k)
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)[-2000:]
        return json.dumps(d, ensure_ascii=False)


def setup_logging() -> None:
    h = logging.StreamHandler()
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [h]
    root.setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").disabled = True


METRICS = {"requests": 0, "errors_5xx": 0, "errors_4xx": 0, "duration_ms_sum": 0.0}


def backup_status() -> dict | None:
    """Last backup cycle as reported by ops/backup-loop.sh (None when the file is not mounted)."""
    import datetime as dt
    try:
        with open(get_settings().backup_status_file, encoding="utf-8") as fh:
            d = json.load(fh)
        t = dt.datetime.fromisoformat(d["finished_at"].replace("Z", "+00:00"))
        d["age_seconds"] = int((dt.datetime.now(dt.timezone.utc) - t).total_seconds())
        return d
    except (OSError, ValueError, KeyError):
        return None


def create_app() -> FastAPI:
    setup_logging()
    s = get_settings()
    from .api.about import release
    app = FastAPI(title="NAP Store API", version=release()["version"], openapi_url="/api/v1/openapi.json",
                  docs_url="/api/v1/docs", redoc_url=None,
                  description="NAP Store platform API. Machine error codes are stable; messages are localized "
                              "(ru/en). Mutating requests with duplicate risk accept an Idempotency-Key header.")
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_handler)

    @app.middleware("http")
    async def context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        if len(rid) > 64 or not rid.replace("-", "").isalnum():
            rid = uuid.uuid4().hex
        token = request_id_var.set(rid)
        request.state.request_id = rid
        loc = request.headers.get("x-locale") or request.cookies.get("nap_locale")
        request.state.locale = loc if loc in ("ru", "en") else negotiate(request.headers.get("accept-language"))
        from .services import currency as _cur  # NAP-012: buyer currency for catalog prices and checkout
        request.state.currency = _cur.resolve(request.cookies.get("nap_currency"), request.headers.get("accept-language"))
        cur_token = _cur.buyer_currency_var.set(request.state.currency)
        t0 = time.perf_counter()
        try:
            if request.url.path.startswith("/api/v1/") and not request.url.path.startswith(ratelimit.EXEMPT_PATHS):
                ratelimit.check(request, "global")  # NAP-052
            response = await call_next(request)
        except ApiError as e:
            response = await api_error_handler(request, e)
        except Exception:
            logging.getLogger("nap.http").exception("unhandled", extra={"path": request.url.path})
            from .i18n import t
            response = JSONResponse(status_code=500, content={"error": {
                "code": "internal_error", "message": t("errors.internal_error", request.state.locale),
                "request_id": rid, "details": {}}})
        dur = (time.perf_counter() - t0) * 1000
        METRICS["requests"] += 1
        METRICS["duration_ms_sum"] += dur
        if response.status_code >= 500:
            METRICS["errors_5xx"] += 1
        elif response.status_code >= 400:
            METRICS["errors_4xx"] += 1
        response.headers["x-request-id"] = rid
        response.headers["x-content-type-options"] = "nosniff"
        path = request.url.path
        if path.startswith("/api/v1/files/") or path.startswith("/api/v1/uploads/"):
            path = path.rsplit("/", 1)[0] + "/[redacted]"  # never log signed URLs
        logging.getLogger("nap.http").info("request", extra={"path": path, "method": request.method,
                                                              "status": response.status_code,
                                                              "duration_ms": round(dur, 1)})
        request_id_var.reset(token)
        _cur.buyer_currency_var.reset(cur_token)
        return response

    @app.get("/api/v1/health", tags=["ops"])
    def health():
        return {"status": "ok", "env": s.env}

    @app.get("/api/v1/ready", tags=["ops"])
    def ready():
        checks = {}
        try:
            with SessionLocal() as db:
                db.execute(text("SELECT 1"))
                rev = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
            checks["database"] = "ok"
            checks["migration"] = rev
        except Exception as e:
            checks["database"] = f"error: {type(e).__name__}"
        try:
            import redis
            redis.Redis.from_url(s.redis_url, socket_timeout=1).ping()
            checks["redis"] = "ok"
        except Exception:
            checks["redis"] = "unavailable" if not s.run_jobs_inline else "not_required_inline_mode"
        try:
            from .storage import get_storage
            get_storage().ping()
            checks["storage"] = "ok"
        except Exception as e:
            checks["storage"] = f"error: {type(e).__name__}"
        ok = checks["database"] == "ok" and checks["storage"] == "ok" and checks["redis"] in ("ok", "not_required_inline_mode")
        return JSONResponse(status_code=200 if ok else 503, content={"ready": ok, "checks": checks})

    @app.get("/api/v1/metrics", tags=["ops"])
    def metrics():
        from .models import ConversionJob, Outbox, WebhookInbox
        with SessionLocal() as db:
            q = dict(db.execute(select(ConversionJob.state, func.count()).group_by(ConversionJob.state)).all())
            dur = db.execute(text("SELECT avg(extract(epoch from finished_at-started_at)) FROM conversion_jobs "
                                  "WHERE state='completed'")).scalar()
            inbox = db.scalar(select(func.count()).select_from(WebhookInbox).where(WebhookInbox.processed_at.is_(None)))
            outbox = db.scalar(select(func.count()).select_from(Outbox).where(Outbox.sent_at.is_(None)))
            from .models import Notification
            mail_failed = db.scalar(select(func.count()).select_from(Notification).where(
                Notification.emailed_at.is_(None), Notification.email_error.isnot(None)))
            outbox_stuck = db.scalar(select(func.count()).select_from(Outbox).where(
                Outbox.sent_at.is_(None), Outbox.attempts >= 5))
        lines = [f"nap_http_requests_total {METRICS['requests']}", f"nap_http_errors_5xx_total {METRICS['errors_5xx']}",
                 f"nap_http_errors_4xx_total {METRICS['errors_4xx']}",
                 f"nap_http_duration_ms_sum {METRICS['duration_ms_sum']:.1f}",
                 f"nap_webhooks_unprocessed {inbox}", f"nap_outbox_pending {outbox}",
                 f"nap_outbox_stuck {outbox_stuck}", f"nap_emails_failed {mail_failed}",
                 f"nap_conversion_avg_seconds {float(dur or 0):.2f}"]
        lines += [f'nap_conversion_jobs{{state="{k}"}} {v}' for k, v in q.items()]
        b = backup_status()
        if b:  # NAP-006
            lines += [f"nap_backup_age_seconds {b['age_seconds']}", f"nap_backup_ok {1 if b['status'] == 'ok' else 0}",
                      f"nap_backup_size_bytes {b.get('size_bytes', 0)}"]
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse("\n".join(lines) + "\n")

    from .api import routers
    for r in routers:
        app.include_router(r, prefix="/api/v1")
    return app


app = create_app()
