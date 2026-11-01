"""Offline safety tests: temporary files and fake engine only; never Docker/HTTP/SSH."""
import argparse
import hashlib
import importlib.util
import io
import json
import os
import socket
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("release_control", Path(__file__).with_name("release_control.py"))
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
A, B = "a" * 40, "b" * 40

class FakeEngine:
    def __init__(self, failures=()):
        self.calls, self.failures = [], set(failures)
    def activate(self, meta):
        self.calls.append(("activate", meta["sha"]))
    def ready(self, meta, seconds=120):
        self.calls.append(("ready", meta["sha"]))
        if meta["sha"] in self.failures:
            raise r.Refusal("synthetic readiness failure")
    def verify_images(self, meta):
        self.calls.append(("images", meta["sha"]))
    def verify_running(self, meta):
        self.calls.append(("running", meta["sha"]))
    def compose(self, meta, *args, **kwargs):
        self.calls.append(("compose", args))
        return ""
    def run(self, *args, **kwargs):
        self.calls.append(("run", args))
        return ""

class Safety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        for name in ["releases", "shared", "control"]:
            (self.root / name).mkdir()
        (self.root / "shared/.env").write_text("PRIVATE_TEST_ENV=synthetic")
        self.envhash = r.digest(self.root / "shared/.env")
        self.engine = FakeEngine()
        self.controller = r.Controller(self.root, self.engine)
    def tearDown(self):
        self.tmp.cleanup()
    def archive(self, extra=None, commit=A):
        path = self.root / "source.tar.gz"
        files = {"docker-compose.yml": b"services: {}", "ops/cohost/docker-compose.cohost.yml": b"services: {}",
                 "release.json": b"{}"}
        with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT, pax_headers={"comment": commit}) as tar:
            for name, content in files.items():
                member = tarfile.TarInfo(name)
                member.size = len(content)
                tar.addfile(member, io.BytesIO(content))
            if extra is not None:
                tar.addfile(extra, io.BytesIO(b"x" * extra.size) if extra.isfile() else None)
        return path
    def args(self, **kw):
        archive = self.archive()
        data = dict(operation="deploy", expected_current="none", env_sha256=self.envhash, sha=A,
                    archive=str(archive), archive_sha256=r.digest(archive), apply=False,
                    closed_demo=False, bootstrap_demo=False)
        data.update(kw)
        return argparse.Namespace(**data)
    def test_plan_has_no_engine_calls_or_extraction(self):
        self.controller.execute(self.args())
        self.assertEqual(self.engine.calls, [])
        self.assertEqual(list((self.root / "releases").iterdir()), [])
        self.assertFalse(self.controller.state_path.exists())
    def test_wrong_archive_digest(self):
        with self.assertRaises(r.Refusal): self.controller.execute(self.args(archive_sha256="0" * 64))
        self.assertEqual(self.engine.calls, [])
    def test_wrong_commit(self):
        args = self.args(sha=B)
        with self.assertRaises(r.Refusal): self.controller.execute(args)
    def test_short_commit_rejected(self):
        with self.assertRaises(r.Refusal): self.controller.execute(self.args(sha="abcdef"))
    def test_wrong_env_hash(self):
        with self.assertRaises(r.Refusal): self.controller.execute(self.args(env_sha256="0" * 64))
    def test_wrong_current(self):
        with self.assertRaises(r.Refusal): self.controller.execute(self.args(expected_current=B))
    def test_no_rollback_target(self):
        with self.assertRaises(r.Refusal): self.controller.execute(self.args(operation="rollback"))
    def test_archive_attacks(self):
        for name in ["../escape", "/absolute", ".env", ".env.production", "a/../escape", "./release.json", "a\\b"]:
            with self.subTest(name=name):
                member = tarfile.TarInfo(name)
                path = self.archive(member)
                with self.assertRaises(r.Refusal): r.archive_members(path, A, r.digest(path))
    def test_archive_link(self):
        for kind in [tarfile.SYMTYPE, tarfile.LNKTYPE]:
            member = tarfile.TarInfo("linked")
            member.type, member.linkname = kind, "../escape"
            path = self.archive(member)
            with self.assertRaises(r.Refusal): r.archive_members(path, A, r.digest(path))
    def test_archive_duplicate(self):
        path = self.archive(tarfile.TarInfo("release.json"))
        with self.assertRaises(r.Refusal): r.archive_members(path, A, r.digest(path))
    def test_archive_budget(self):
        path = self.archive()
        with patch.object(r, "MAX_ARCHIVE_BYTES", 1):
            with self.assertRaises(r.Refusal): r.archive_members(path, A, r.digest(path))
    def test_unfinished_operation_blocks(self):
        r.atomic_json(self.controller.state_path, {"status": "activating", "current": None})
        with self.assertRaises(r.Refusal): self.controller.execute(self.args())
    def test_existing_lock_blocks_before_docker(self):
        self.controller.lock.mkdir()
        with patch.object(r.sys, "platform", "linux"), patch.object(r.os, "geteuid", return_value=1000, create=True):
            with self.assertRaises(r.Refusal):
                self.controller.execute(self.args(apply=True, closed_demo=True, bootstrap_demo=True))
        self.assertEqual(self.engine.calls, [])
    def test_apply_requires_demo_ack(self):
        with patch.object(r.sys, "platform", "linux"), patch.object(r.os, "geteuid", return_value=1000, create=True):
            with self.assertRaises(r.Refusal): self.controller.execute(self.args(apply=True))
        self.assertEqual(self.engine.calls, [])
    def test_initial_requires_explicit_bootstrap(self):
        with patch.object(r.sys, "platform", "linux"), patch.object(r.os, "geteuid", return_value=1000, create=True):
            with self.assertRaises(r.Refusal): self.controller.execute(self.args(apply=True, closed_demo=True))
    def test_root_apply_refused(self):
        with patch.object(r.sys, "platform", "linux"), patch.object(r.os, "geteuid", return_value=0, create=True):
            with self.assertRaises(r.Refusal): self.controller.execute(self.args(apply=True, closed_demo=True))
    def test_schema_and_topology_fingerprint(self):
        before = {"backend/migrations/a.py": "a", "docker-compose.yml": "b", "frontend/app.ts": "c"}
        after = dict(before, **{"frontend/app.ts": "d"})
        self.assertEqual(r.schema_hashes(before), r.schema_hashes(after))
        after["backend/migrations/a.py"] = "changed"
        self.assertNotEqual(r.schema_hashes(before), r.schema_hashes(after))
    def test_release_tamper(self):
        rel = self.root / "releases" / A
        rel.mkdir(); (rel / "app").write_text("good")
        meta = dict(sha=A, release=str(rel), files=r.tree_hashes(rel))
        (rel / "app").write_text("bad")
        with self.assertRaises(r.Refusal): self.controller.verify_meta(meta)
    def test_success_changes_state_after_ready(self):
        old, target = {"sha": A}, {"sha": B}
        state = dict(current=old, previous=None, status="idle")
        pointers = []
        with patch.object(self.controller, "pointer", side_effect=lambda m: pointers.append(m["sha"])):
            self.controller.transition(state, target)
        self.assertEqual(pointers, [B])
        self.assertEqual(r.read_json(self.controller.state_path)["current"], target)
        self.assertEqual(self.engine.calls[:2], [("activate", B), ("ready", B)])
    def test_failed_candidate_restores_prior_without_migration(self):
        self.engine.failures = {B}
        state = dict(current={"sha": A}, previous=None, status="idle")
        with patch.object(self.controller, "verify_meta"), patch.object(self.controller, "pointer"):
            with self.assertRaisesRegex(r.Refusal, "prior images/config restored"):
                self.controller.transition(state, {"sha": B})
        self.assertEqual(r.read_json(self.controller.state_path)["current"]["sha"], A)
        self.assertEqual(r.read_json(self.controller.state_path)["status"], "idle")
        self.assertEqual([c for c in self.engine.calls if c[0] == "activate"], [("activate", B), ("activate", A)])
        self.assertFalse(any(c[0] == "compose" for c in self.engine.calls))
    def test_failed_recovery_marks_manual_recovery(self):
        self.engine.failures = {A, B}
        state = dict(current={"sha": A}, previous=None, status="idle")
        with patch.object(self.controller, "verify_meta"):
            with self.assertRaisesRegex(r.Refusal, "recovery failed"):
                self.controller.transition(state, {"sha": B})
        self.assertEqual(r.read_json(self.controller.state_path)["status"], "recovery_required")
    def test_initial_failure_stops_without_deleting_data(self):
        self.engine.failures = {B}
        with self.assertRaises(r.Refusal):
            self.controller.transition(dict(current=None, previous=None, status="idle"), {"sha": B}, bootstrap=True)
        commands = [c[1] for c in self.engine.calls if c[0] == "compose"]
        self.assertTrue(any(c[0] == "stop" for c in commands))
        self.assertFalse(any(c[0] == "down" for c in commands))
        self.assertEqual(r.read_json(self.controller.state_path)["status"], "initial_recovery_required")
    @unittest.skipUnless(os.name == "posix", "Frozen environment mode checks require POSIX")
    def test_prepare_freezes_images_without_activation(self):
        args = self.args()
        (self.root / "shared/.env").chmod(0o600)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        config = {"services": {
            "api": {"environment": {"NAP_ENV": "demo", "NAP_COOKIE_SECURE": "true",
                "NAP_PUBLIC_BASE_URL": "https://store.napsystem.com", "NAP_SECRET_KEY": "s" * 40,
                "NAP_STORAGE_BACKEND": "local"}, "build": {"context": "backend"}},
            "web": {"ports": [{"host_ip": "127.0.0.1", "published": str(port)}], "build": {"context": "frontend"}},
            "worker-media": {}, "worker-ops": {}, "migrate": {},
            "postgres": {"image": "postgres:test", "environment": {"POSTGRES_PASSWORD": "p" * 40}},
            "redis": {"image": "redis:test"}, "backup": {"image": "postgres:test"}}, "volumes": {}}
        image_id = "sha256:" + "c" * 64
        def fake_run(command, **kwargs):
            self.engine.calls.append(("run", command))
            if "config" in command:
                return json.dumps(config)
            if command[1:3] == ["image", "inspect"]:
                return image_id
            return ""
        with patch.object(self.engine, "run", side_effect=fake_run):
            meta = self.controller.prepare(args, {"current": None})
        self.assertEqual(set(meta["images"].values()), {image_id})
        frozen = r.read_json(meta["config"])
        self.assertNotIn("build", frozen["services"]["api"])
        self.controller.verify_meta(meta)
        self.assertFalse(any(c[0] == "activate" for c in self.engine.calls))
        self.assertFalse(self.controller.state_path.exists())

    @unittest.skipUnless(os.name == "posix", "Frozen environment mode checks require POSIX")
    def test_prepare_schema_change_stops_before_build(self):
        args = self.args()
        (self.root / "shared/.env").chmod(0o600)
        old = {"files": {"docker-compose.yml": "old"}, "env_hash": self.envhash}
        with self.assertRaisesRegex(r.Refusal, "Schema/topology"):
            self.controller.prepare(args, {"current": old})
        self.assertEqual(self.engine.calls, [])

    def test_stale_release_document_check_does_not_write(self):
        script = Path(__file__).resolve().parents[1] / "release.py"
        module_spec = importlib.util.spec_from_file_location("release_docs", script)
        docs = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(docs)
        folder = self.root / "metadata"
        (folder / "docs/spec").mkdir(parents=True)
        (folder / "docs/spec/TZ-v1.0.md").write_text("spec", encoding="utf-8")
        (folder / "docs/PROJECT.md").write_text("project", encoding="utf-8")
        (folder / "release.json").write_text('{"version":"1","spec":{"version":"1.0","document":"spec/TZ-v1.0.md"}}')
        (folder / "CHANGELOG.md").write_text("## [1]", encoding="utf-8")
        docs.ROOT, docs.DST = folder, folder / "packaged"
        with patch.object(sys, "argv", ["release.py"]): docs.main()
        (folder / "CHANGELOG.md").write_text("## [1] updated", encoding="utf-8")
        before = r.tree_hashes(docs.DST)
        with patch.object(sys, "argv", ["release.py", "--check"]):
            with self.assertRaises(SystemExit): docs.main()
        self.assertEqual(r.tree_hashes(docs.DST), before)
        with patch.object(sys, "argv", ["release.py"]): docs.main()
        with patch.object(sys, "argv", ["release.py", "--check"]): docs.main()

    def test_frozen_compose_preserves_literal_dollars(self):
        original = {"environment": {"PASSWORD": "a${HOME}$x$$end"}, "command": ["echo $VALUE"], "port": 3}
        frozen = r.escape_compose(original)
        self.assertEqual(frozen["environment"]["PASSWORD"], "a$${HOME}$$x$$$$end")
        self.assertEqual(frozen["environment"]["PASSWORD"].replace("$$", "$"), original["environment"]["PASSWORD"])
        self.assertEqual(frozen["command"], ["echo $$VALUE"])
        self.assertEqual(frozen["port"], 3)

    def test_no_shell_environment_override(self):
        with patch.dict(r.os.environ, {"NAP_SECRET_KEY": "never", "COMPOSE_FILE": "other.yml"}):
            clean = r.clean_environment()
            self.assertNotIn("NAP_SECRET_KEY", clean)
            self.assertNotIn("COMPOSE_FILE", clean)

if __name__ == "__main__":
    unittest.main()
