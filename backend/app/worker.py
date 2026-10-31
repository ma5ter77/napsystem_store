"""Celery worker: conversions run here, never inside the HTTP request. PostgreSQL keeps durable job state."""
from __future__ import annotations

import os
import threading
import uuid

from celery import Celery

from .config import get_settings

s = get_settings()
celery = Celery("nap", broker=s.redis_url, backend=None)
# beat state lives in the writable var/ dir (the image runs as a non-root user; /app itself is read-only to it)
os.makedirs("var", exist_ok=True)
celery.conf.update(beat_schedule_filename=os.path.join("var", "celerybeat-schedule"), task_acks_late=True, worker_prefetch_multiplier=1, task_reject_on_worker_lost=True,
                   broker_connection_retry_on_startup=True, timezone="UTC",
                   task_routes={"nap.run_conversion": {"queue": "media"}}, task_default_queue="ops",
                   beat_schedule={
                       "process-inbox": {"task": "nap.process_inbox", "schedule": 30.0},
                       "dispatch-outbox": {"task": "nap.dispatch_outbox", "schedule": 15.0},
                       "recover-leases": {"task": "nap.recover_leases", "schedule": 60.0},
                       "expire-reservations": {"task": "nap.expire_reservations", "schedule": 60.0},
                       "reconcile-payments": {"task": "nap.reconcile", "schedule": 600.0},
                       "purge-measurements": {"task": "nap.purge_measurements", "schedule": 86400.0},
                   })


@celery.task(name="nap.run_conversion", bind=True, max_retries=5)
def run_conversion(self, job_id: str):
    from .services.conversion import run_job
    res = run_job(job_id)
    if res == "queued":  # bounded retry for transient errors (attempt counter lives in PostgreSQL)
        raise self.retry(countdown=min(300, 10 * (self.request.retries + 1)))
    return res


@celery.task(name="nap.process_inbox")
def process_inbox():
    from .services.billing import process_inbox as f
    return f()


@celery.task(name="nap.dispatch_outbox")
def dispatch_outbox():
    from .services.billing import dispatch_outbox as f
    return f()


@celery.task(name="nap.recover_leases")
def recover_leases():
    from .services.conversion import recover_expired_leases
    ids = recover_expired_leases()
    for i in ids:
        dispatch_job(i)
    return len(ids)


@celery.task(name="nap.expire_reservations")
def expire_reservations():
    from .services.hardware import expire_reservations as f
    return f()


@celery.task(name="nap.reconcile")
def reconcile():
    from .services.billing import reconcile_attempts
    return reconcile_attempts()


@celery.task(name="nap.purge_measurements")
def purge_measurements():
    from .db import session_scope
    from .services.sessions import purge_expired_measurements
    with session_scope() as db:
        return purge_expired_measurements(db)


def dispatch_job(job_id) -> None:
    """Hand the job to the queue (or, in single-process demo/test mode, to a background thread)."""
    if get_settings().run_jobs_inline:
        from .services.conversion import run_job

        def _run():
            for _ in range(get_settings().job_max_attempts):
                if run_job(job_id) != "queued":
                    break
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        _threads.append(t)
        return
    run_conversion.delay(str(job_id))


_threads: list[threading.Thread] = []


def wait_inline_jobs(timeout: float = 120) -> None:
    for t in list(_threads):
        t.join(timeout)
    _threads.clear()
