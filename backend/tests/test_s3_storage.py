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
