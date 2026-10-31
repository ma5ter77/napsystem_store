"""Storage operations (NAP-004).

    python -m app.tools.storage check
        Round trip against the configured storage: write, size, signed API link, ranged read, hash, delete.
        For S3 also proves the bucket is private: an anonymous request for the test object must be refused.

    python -m app.tools.storage migrate [--from /app/var/storage] [--dry-run]
        Copies every file of the local volume into the configured S3 bucket under the same key, so the keys
        stored in the database stay valid. Idempotent: objects already in the bucket with the same size are
        skipped. Files that belong to an asset are verified by SHA-256 after the copy. Exit code 1 on any problem.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import uuid


def _out(**kw) -> None:
    print(json.dumps(kw, ensure_ascii=False))


def check() -> int:
    from ..config import get_settings
    from ..storage import get_storage, verify_signed
    st = get_storage()
    st.ping()
    key = f"healthcheck/{uuid.uuid4()}.bin"
    data = bytes(range(256)) * 64
    with tempfile.NamedTemporaryFile() as f:
        f.write(data)
        f.flush()
        st.put_file(key, f.name, "application/octet-stream")
    problems = []
    try:
        if not st.exists(key) or st.size(key) != len(data):
            problems.append("size_mismatch")
        import hashlib
        if st.sha256(key) != hashlib.sha256(data).hexdigest():
            problems.append("hash_mismatch")
        url = st.signed_get_url(key, "x.bin", "application/octet-stream")
        if not url.startswith("/api/v1/files/") or verify_signed(url.rsplit("/", 1)[1]) is None:
            problems.append("signed_link_invalid")
        if hasattr(st, "open_range"):
            body, rng, n = st.open_range(key, "bytes=10-19")
            if body.read() != data[10:20]:
                problems.append("range_read_failed")
            s = get_settings()
            import urllib.error
            import urllib.request
            try:  # anonymous, unsigned request straight to the bucket must not return the object
                urllib.request.urlopen(f"{s.s3_endpoint_url.rstrip('/')}/{s.s3_bucket}/{key}", timeout=5)
                problems.append("bucket_is_public")
            except urllib.error.HTTPError as e:
                if e.code not in (401, 403):
                    problems.append(f"anonymous_status_{e.code}")
            if st.public_policy():
                problems.append("bucket_policy_public")
    finally:
        st.delete(key)
    if st.exists(key):
        problems.append("delete_failed")
    _out(backend=get_settings().storage_backend, ok=not problems, problems=problems)
    return 0 if not problems else 1


def migrate(src_root: str, dry_run: bool) -> int:
    from sqlalchemy import select

    from ..config import get_settings
    from ..db import SessionLocal
    from ..models import Asset
    from ..storage import LocalStorage, get_storage
    if get_settings().storage_backend != "s3":
        _out(ok=False, error="set NAP_STORAGE_BACKEND=s3 and NAP_S3_* first; the target is the configured bucket")
        return 1
    src, dst = LocalStorage(src_root), get_storage()
    with SessionLocal() as db:
        hashes = dict(db.execute(select(Asset.storage_key, Asset.sha256)).all())
    stats = {"copied": 0, "skipped": 0, "verified": 0, "bytes": 0}
    problems: list[str] = []
    for key, size in src.iter_keys():
        if dst.exists(key) and dst.size(key) == size:
            stats["skipped"] += 1
            continue
        if dry_run:
            stats["copied"] += 1
            stats["bytes"] += size
            continue
        try:
            ctype = "application/pdf" if key.endswith(".pdf") else "application/octet-stream"
            dst.put_file(key, src.local_path(key), ctype)
            if dst.size(key) != size:
                problems.append(f"size:{key}")
                continue
            if hashes.get(key):
                if dst.sha256(key) != hashes[key]:
                    problems.append(f"sha256:{key}")
                    continue
                stats["verified"] += 1
            stats["copied"] += 1
            stats["bytes"] += size
        except Exception as e:  # keep going; report every failed key
            problems.append(f"{type(e).__name__}:{key}")
    missing = [k for k in hashes if not dst.exists(k) and not src.exists(k)] if not dry_run else []
    _out(ok=not problems, dry_run=dry_run, **stats, problems=problems[:50], problems_total=len(problems),
         assets_missing_everywhere=missing[:50])
    return 0 if not problems else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.tools.storage")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    m = sub.add_parser("migrate")
    m.add_argument("--from", dest="src", default="/app/var/storage")
    m.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    return check() if a.cmd == "check" else migrate(a.src, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
