"""Request rate limits (NAP-052): fixed windows in Redis, in-process fallback when Redis is unavailable.

Buckets are named per action; the key is the signed-in user when known, otherwise the client address.
Exceeding a limit raises `rate_limited` (HTTP 429) with a localized message and a Retry-After header.
"""
from __future__ import annotations

import hashlib
import threading
import time

from fastapi import Request

from .config import get_settings
from .errors import ApiError

# bucket -> (limit, window seconds); overridable with NAP_RATE_LIMITS="bucket=limit/window,..."
DEFAULTS: dict[str, tuple[int, int]] = {
    "global": (1200, 60),        # any API request, per user or address
    "upload_ticket": (60, 3600),  # new upload links
    "upload_chunk": (900, 60),    # 8 MB chunks: ~7 GB/min, far above a real upload
    "checkout": (30, 600),        # digital and hardware checkouts, mock payments
    "webhook": (600, 60),         # payment/shipping provider callbacks, per address
    "review_write": (30, 3600),   # save/delete review, reply
    "review_report": (20, 3600),
    "conversion": (60, 3600),
    "app_view": (120, 3600),      # NAP-027: app page views reported by the browser
}
EXEMPT_PATHS = ("/api/v1/health", "/api/v1/ready", "/api/v1/metrics", "/api/v1/version")

_local: dict[str, tuple[int, float]] = {}
_lock = threading.Lock()
_redis = None
_redis_failed_at = 0.0


def limits() -> dict[str, tuple[int, int]]:
    out = dict(DEFAULTS)
    for part in (get_settings().rate_limits or "").split(","):
        if "=" in part and "/" in part:
            name, val = part.split("=", 1)
            n, w = val.split("/", 1)
            out[name.strip()] = (int(n), int(w))
    return out


def _client() -> object | None:
    global _redis, _redis_failed_at
    if _redis is not None:
        return _redis
    if time.monotonic() - _redis_failed_at < 30:
        return None
    try:
        import redis
        r = redis.Redis.from_url(get_settings().redis_url, socket_timeout=0.3, socket_connect_timeout=0.3)
        r.ping()
        _redis = r
        return r
    except Exception:
        _redis_failed_at = time.monotonic()
        return None


def _hit(key: str, window: int) -> tuple[int, int]:
    """Count one request in the current window; returns (count, seconds until the window ends)."""
    now = time.time()
    start = int(now // window) * window
    ttl = int(start + window - now) + 1
    r = _client()
    if r is not None:
        try:
            k = f"rl:{key}:{start}"
            pipe = r.pipeline()
            pipe.incr(k)
            pipe.expire(k, window + 1)
            n = pipe.execute()[0]
            return int(n), ttl
        except Exception:
            global _redis
            _redis = None
    with _lock:
        n, exp = _local.get(f"{key}:{start}", (0, start + window))
        _local[f"{key}:{start}"] = (n + 1, exp)
        if len(_local) > 50_000:  # drop finished windows
            for k in [k for k, (_, e) in _local.items() if e < now]:
                _local.pop(k, None)
        return n + 1, ttl


def who(request: Request, user_id=None) -> str:
    if user_id:
        return f"u:{user_id}"
    cookie = request.cookies.get("nap_session")
    if cookie:
        return "s:" + hashlib.sha256(cookie.encode()).hexdigest()[:24]
    # behind Next.js / Caddy the first X-Forwarded-For hop is the browser; direct calls use the socket address
    fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return "ip:" + (fwd or (request.client.host if request.client else "unknown"))


def check(request: Request, bucket: str, user_id=None, key: str | None = None) -> None:
    if not get_settings().rate_limits_enabled:
        return
    limit, window = limits().get(bucket, DEFAULTS["global"])
    n, retry = _hit(f"{bucket}:{key or who(request, user_id)}", window)
    if n > limit:
        raise ApiError("rate_limited", 429, details={"bucket": bucket, "retry_after": retry},
                       headers={"Retry-After": str(retry)}, seconds=retry)


def reset_local() -> None:
    """Tests: forget counters (in-process and the Redis rl:* keys)."""
    with _lock:
        _local.clear()
    r = _client()
    if r is not None:
        try:
            for k in r.scan_iter("rl:*", count=500):
                r.delete(k)
        except Exception:
            pass
