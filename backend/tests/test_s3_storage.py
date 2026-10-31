"""S3 storage backend against a local S3-compatible emulator (moto). MinIO itself runs only in docker compose."""
import os
import socket
import threading

import pytest


@pytest.fixture(scope="module")
def s3_server():
    from moto.server import ThreadedMotoServer
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = ThreadedMotoServer(ip_address="127.0.0.1", port=port)
    srv.start()
    yield f"http://127.0.0.1:{port}"
    srv.stop()


def test_s3_roundtrip_signed_stream_and_range(s3_server, monkeypatch, tmp_path):
    from app.config import get_settings
    from app import storage
    st = get_settings()
    for k, v in {"storage_backend": "s3", "s3_endpoint_url": s3_server, "s3_access_key": "x", "s3_secret_key": "y",
                 "s3_bucket": "nap-private"}.items():
        monkeypatch.setattr(st, k, v)
    storage.reset_storage()
    try:
        s3 = storage.get_storage()
        s3.client.create_bucket(Bucket="nap-private")
        f = tmp_path / "a.bin"
        f.write_bytes(bytes(range(256)) * 100)
        s3.put_file("source/x/a.bin", str(f), "application/octet-stream")
        assert s3.exists("source/x/a.bin") and s3.size("source/x/a.bin") == 25600
        url = s3.signed_get_url("source/x/a.bin", "a.bin", "application/octet-stream")
        assert url.startswith("/api/v1/files/")  # never a public bucket URL
        from fastapi.testclient import TestClient
        from app.main import app
        c = TestClient(app)
        r = c.get(url)
        assert r.status_code == 200 and r.content == f.read_bytes()
        r = c.get(url, headers={"range": "bytes=10-19"})
        assert r.status_code == 206 and r.content == f.read_bytes()[10:20]
        out = tmp_path / "b.bin"
        s3.get_to_file("source/x/a.bin", str(out))
        assert out.read_bytes() == f.read_bytes()
    finally:
        storage.reset_storage()


@pytest.fixture
def s3_settings(s3_server, monkeypatch):
    from app.config import get_settings
    from app import storage
    st = get_settings()
    bucket = "nap-" + os.urandom(4).hex()
    for k, v in {"storage_backend": "s3", "s3_endpoint_url": s3_server, "s3_access_key": "x", "s3_secret_key": "y",
                 "s3_bucket": bucket}.items():
        monkeypatch.setattr(st, k, v)
    storage.reset_storage()
    yield st
    storage.reset_storage()


def test_bucket_created_on_first_use_and_public_policy_refused(s3_settings):
    """NAP-004: first start creates the private bucket; a bucket open to everyone stops the service."""
    import json
    from app import storage
    s3 = storage.get_storage()
    s3.client.head_bucket(Bucket=s3_settings.s3_bucket)  # created by get_storage()
    assert s3.public_policy() is False
    s3.client.put_bucket_policy(Bucket=s3_settings.s3_bucket, Policy=json.dumps({"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Principal": "*", "Action": "s3:GetObject",
         "Resource": f"arn:aws:s3:::{s3_settings.s3_bucket}/*"}]}))
    storage.reset_storage()
    with pytest.raises(RuntimeError, match="public"):
        storage.get_storage()


def test_check_tool_passes_on_private_bucket(s3_settings, capsys):
    """The emulator refuses unsigned requests like a correctly configured bucket: check passes."""
    from app.tools import storage as tool
    assert tool.main(["check"]) == 0, capsys.readouterr().out


def test_check_tool_detects_anonymous_access(s3_settings, capsys, monkeypatch):
    """If the bucket answers an unsigned request, check fails loudly."""
    import io
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=None: io.BytesIO(b"leak"))
    from app.tools import storage as tool
    assert tool.main(["check"]) == 1
    out = capsys.readouterr().out
    assert "bucket_is_public" in out and "hash_mismatch" not in out and "range_read_failed" not in out


def test_migrate_local_volume_to_bucket(s3_settings, capsys):
    """Seeded demo files move to the bucket under the same keys, hashes verified; rerun copies nothing;
    published versions pass the deep integrity check reading from the bucket."""
    import json
    from app.tools import storage as tool
    root = os.environ["NAP_STORAGE_ROOT"]
    assert tool.main(["migrate", "--from", root, "--dry-run"]) == 0
    dry = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert dry["copied"] > 0 and dry["dry_run"]
    assert tool.main(["migrate", "--from", root]) == 0
    first = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert first["copied"] == dry["copied"] and first["verified"] > 0 and first["problems_total"] == 0
    assert tool.main(["migrate", "--from", root]) == 0
    again = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert again["copied"] == 0 and again["skipped"] == first["copied"]

    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models import App, AppVersion
    from app.services.apps import verify_integrity
    with SessionLocal() as db:
        v = db.scalars(select(AppVersion).where(AppVersion.state == "published")).first()
        assert v is not None
        assert verify_integrity(db, db.get(App, v.app_id), v, deep=True) == []


def test_local_storage_hash_and_listing(tmp_path):
    from app.storage import LocalStorage
    st = LocalStorage(str(tmp_path))
    f = tmp_path / "in.bin"
    f.write_bytes(b"abc")
    st.put_file("a/b/c.bin", str(f), "application/octet-stream")
    import hashlib
    assert st.sha256("a/b/c.bin") == hashlib.sha256(b"abc").hexdigest()
    assert ("a/b/c.bin", 3) in list(st.iter_keys("a/"))
    st.ping()
