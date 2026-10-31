"""NAP-050: load test of the store API and the conversion queue.

    uv run --project backend python ops/loadtest.py [--base http://127.0.0.1:8000] [--scenario all|catalog|checkout|conversions]
        [--seconds 30] [--concurrency 1,4,16,32] [--conversions 4] [--json out.json]

Scenarios (all use only public/demo endpoints; payments go through the mock provider — run against a demo/test
stand, never against production with live payments):
  catalog      anonymous browsing: catalog list, search, app page, product page, reviews (weighted mix)
  checkout     signed-in buyers: register, open a paid app, create a checkout and pay it with the mock provider
  conversions  N parallel uploads + conversions of a 60 s track; measures queue wait and processing time

For each concurrency step: requests/s, error rate, p50/p95/p99 latency. The "limit" is the highest step where
p95 < 500 ms and errors < 1 %. Rate limiting must be off for the test (NAP_RATE_LIMITS_ENABLED=false) or the
test client counts as one user and is throttled by design.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import math
import random
import statistics
import struct
import time
import uuid

import httpx

PASSWORD = "load-test-password-1"


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


class Stats:
    def __init__(self):
        self.lat: list[float] = []
        self.errors = 0
        self.by_status: dict[int, int] = {}

    def add(self, dt: float, status: int):
        self.lat.append(dt)
        self.by_status[status] = self.by_status.get(status, 0) + 1
        if status >= 500 or status == 0:
            self.errors += 1

    def summary(self, seconds: float) -> dict:
        n = len(self.lat)
        return {"requests": n, "rps": round(n / seconds, 1), "errors": self.errors,
                "error_rate": round(self.errors / n, 4) if n else None,
                "p50_ms": round(pct(self.lat, .5) * 1000, 1), "p95_ms": round(pct(self.lat, .95) * 1000, 1),
                "p99_ms": round(pct(self.lat, .99) * 1000, 1), "status": dict(sorted(self.by_status.items()))}


async def timed(client: httpx.AsyncClient, st: Stats, method: str, url: str, **kw) -> httpx.Response | None:
    t0 = time.perf_counter()
    try:
        r = await client.request(method, url, **kw)
        st.add(time.perf_counter() - t0, r.status_code)
        return r
    except httpx.HTTPError:
        st.add(time.perf_counter() - t0, 0)
        return None


async def catalog_worker(base: str, until: float, st: Stats, slugs: list[str], products: list[str]):
    async with httpx.AsyncClient(base_url=base, timeout=30, headers={"x-locale": random.choice(["ru", "en"])}) as c:
        while time.perf_counter() < until:
            r = random.random()
            if r < 0.35:
                await timed(c, st, "GET", "/api/v1/catalog/apps?limit=20")
            elif r < 0.5:
                await timed(c, st, "GET", f"/api/v1/catalog/apps?q={random.choice(['мед', 'sleep', 'night', 'сон'])}")
            elif r < 0.75:
                await timed(c, st, "GET", f"/api/v1/catalog/apps/{random.choice(slugs)}")
            elif r < 0.9:
                await timed(c, st, "GET", f"/api/v1/hardware/products/{random.choice(products)}")
            else:
                await timed(c, st, "GET", f"/api/v1/catalog/apps/{random.choice(slugs)}/reviews?limit=10")


async def login_new_buyer(base: str, developer: bool = False) -> httpx.AsyncClient:
    c = httpx.AsyncClient(base_url=base, timeout=30)
    r = await c.post("/api/v1/auth/register", json={"email": f"load-{uuid.uuid4().hex[:12]}@example.com",
                                                    "password": PASSWORD, "display_name": "Load", "locale": "ru",
                                                    "as_developer": developer})
    r.raise_for_status()
    c.headers["x-csrf-token"] = r.json()["csrf_token"]
    return c


async def checkout_worker(base: str, until: float, st: Stats, paid: list[str]):
    while time.perf_counter() < until:
        c = await login_new_buyer(base)
        try:
            app_id = random.choice(paid)
            r = await timed(c, st, "POST", "/api/v1/checkout/digital", json={"app_id": app_id, "kind": "one_time"})
            if r is not None and r.status_code == 201:
                ref = r.json()["checkout_url"].rsplit("/", 1)[-1]
                await timed(c, st, "POST", f"/api/v1/mock-checkout/{ref}/complete", json={"outcome": "success"})
                await timed(c, st, "GET", "/api/v1/library")
        finally:
            await c.aclose()


def wav(seconds: float, fs: int = 44100) -> bytes:
    n = int(seconds * fs)
    buf = io.BytesIO()
    buf.write(b"RIFF" + struct.pack("<I", 36 + n * 4) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 2, fs, fs * 4, 4, 16))
    buf.write(b"data" + struct.pack("<I", n * 4))
    frame = bytearray()
    for i in range(n):
        v = int(0.2 * 32767 * math.sin(2 * math.pi * 220 * i / fs))
        frame += struct.pack("<hh", v, v)
    buf.write(frame)
    return buf.getvalue()


async def conversions(base: str, n: int, seconds: float) -> dict:
    """N authors upload a track and start a conversion at the same moment; queue wait = started - created."""
    data = wav(seconds)
    sha = hashlib.sha256(data).hexdigest()

    async def one(i: int) -> dict:
        c = await login_new_buyer(base, developer=True)
        try:
            slug = f"load-{uuid.uuid4().hex[:8]}"
            a = (await c.post("/api/v1/developer/apps", json={"slug": slug, "category": "meditation", "author_name": "Load",
                                                              "metadata": {"ru": {"title": slug}}, "content_language": "ru"})).json()
            t = (await c.post(f"/api/v1/developer/apps/{a['id']}/uploads",
                              json={"filename": "t.wav", "size_bytes": len(data), "sha256": sha})).json()
            up = await c.put(t["upload_url"], content=data, headers={"content-type": "application/octet-stream"})
            up.raise_for_status()
            prof = (await c.get("/api/v1/developer/profiles")).json()["items"][0]["id"]
            j = (await c.post("/api/v1/developer/conversions", json={"app_id": a["id"], "source_asset_id": t["asset_id"],
                                                                     "profile_id": prof})).json()
            # the app auto-starts a conversion after upload; either way we follow the job we asked for
            t0 = time.perf_counter()
            while time.perf_counter() - t0 < 900:
                s = (await c.get(f"/api/v1/developer/conversions/{j['id']}")).json()
                if s["state"] in ("completed", "failed", "cancelled"):
                    break
                await asyncio.sleep(1)
            from datetime import datetime
            ts = lambda x: datetime.fromisoformat(x).timestamp() if x else None  # noqa: E731
            return {"state": s["state"], "queue_s": round(ts(s["started_at"]) - ts(s["created_at"]), 1) if s["started_at"] else None,
                    "processing_s": round(ts(s["finished_at"]) - ts(s["started_at"]), 1) if s["finished_at"] and s["started_at"] else None}
        finally:
            await c.aclose()

    t0 = time.perf_counter()
    res = await asyncio.gather(*(one(i) for i in range(n)))
    wall = time.perf_counter() - t0
    q = [r["queue_s"] for r in res if r.get("queue_s") is not None]
    p = [r["processing_s"] for r in res if r.get("processing_s") is not None]
    return {"parallel": n, "track_seconds": seconds, "wall_seconds": round(wall, 1),
            "completed": sum(1 for r in res if r.get("state") == "completed"), "errors": [r for r in res if "error" in r],
            "queue_wait_s": {"max": max(q, default=None), "mean": round(statistics.mean(q), 1) if q else None},
            "processing_s": {"max": max(p, default=None), "mean": round(statistics.mean(p), 1) if p else None}}


async def run_steps(name, worker, args, base, extra):
    out = []
    for conc in [int(x) for x in args.concurrency.split(",")]:
        st = Stats()
        until = time.perf_counter() + args.seconds
        t0 = time.perf_counter()
        await asyncio.gather(*(worker(base, until, st, *extra) for _ in range(conc)))
        s = st.summary(time.perf_counter() - t0) | {"concurrency": conc}
        print(f"{name:9} c={conc:3}  {s['rps']:7.1f} rps  p50 {s['p50_ms']:7.1f}  p95 {s['p95_ms']:7.1f}  p99 {s['p99_ms']:7.1f} ms"
              f"  errors {s['errors']}/{s['requests']}", flush=True)
        out.append(s)
    ok = [s for s in out if s["p95_ms"] < 500 and (s["error_rate"] or 0) < 0.01]
    return {"steps": out, "limit": max(ok, key=lambda s: s["rps"]) if ok else None}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--scenario", default="all")
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--concurrency", default="1,4,16,32")
    ap.add_argument("--conversions", type=int, default=4)
    ap.add_argument("--track-seconds", type=float, default=60)
    ap.add_argument("--json")
    a = ap.parse_args()
    report: dict = {"base": a.base, "seconds_per_step": a.seconds, "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    async with httpx.AsyncClient(base_url=a.base, timeout=30) as c:
        apps = (await c.get("/api/v1/catalog/apps?limit=100")).json()["items"]
        prods = (await c.get("/api/v1/hardware/products?limit=100")).json()["items"]
        report["version"] = (await c.get("/api/v1/version")).json().get("version")
    slugs = [x["slug"] for x in apps] or ["quiet-harbor"]
    paid = [x["id"] for x in apps if any(p["kind"] == "one_time" for p in x.get("prices", []))]
    if a.scenario in ("all", "catalog"):
        report["catalog"] = await run_steps("catalog", catalog_worker, a, a.base, (slugs, [p["slug"] for p in prods]))
    if a.scenario in ("all", "checkout") and paid:
        report["checkout"] = await run_steps("checkout", checkout_worker, a, a.base, (paid,))
    if a.scenario in ("all", "conversions"):
        report["conversions"] = await conversions(a.base, a.conversions, a.track_seconds)
        print("conversions", json.dumps(report["conversions"], ensure_ascii=False), flush=True)
    if a.json:
        with open(a.json, "w") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    asyncio.run(main())
