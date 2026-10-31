"""NAP-005/006: backup status reaches /metrics and the admin Ops view; stale or failed backups are flagged."""
import json
from datetime import datetime, timedelta, timezone

from app.config import get_settings

from .conftest import Client, ok


def _status(path, hours_ago, status="ok"):
    t = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    path.write_text(json.dumps({"finished_at": t, "status": status, "file": "nap-x.dump", "size_bytes": 1234,
                                "message": "RESTORE OK"}))


def test_backup_status_in_metrics_and_admin(monkeypatch, tmp_path):
    f = tmp_path / "last-status.json"
    monkeypatch.setattr(get_settings(), "backup_status_file", str(f))
    admin = Client().login("admin@nap.demo")
    assert ok(admin.get("/admin/ops"))["backup"] is None  # not mounted
    _status(f, 1)
    m = Client().raw().get("/api/v1/metrics").text
    assert "nap_backup_ok 1" in m and "nap_backup_size_bytes 1234" in m and "nap_emails_failed" in m
    b = ok(admin.get("/admin/ops"))["backup"]
    assert b["status"] == "ok" and not b["stale"]
    _status(f, 30)
    assert ok(admin.get("/admin/ops"))["backup"]["stale"]
    _status(f, 1, "failed")
    assert "nap_backup_ok 0" in Client().raw().get("/api/v1/metrics").text
    assert ok(admin.get("/admin/ops"))["backup"]["stale"]
