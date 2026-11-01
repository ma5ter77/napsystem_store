"""NAP-087: offline tests of the release runner input checks (no Docker needed)."""
import base64
import io
import json
import os
import pathlib
import subprocess
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import napdeploy  # noqa: E402

D = "sha256:" + "a" * 64


def bundle(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            if data is None:
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                tar.addfile(info)
                continue
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return base64.b64encode(buf.getvalue()).decode()


GOOD = {"ops/vps/docker-compose.prod.yml": b"services: {}\n", "ops/backup-loop.sh": b"#!/bin/sh\n"}


class Inputs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        for d in ("releases", "state", "backups", "shared"):
            (root / d).mkdir()
        self.srv = napdeploy.Server({"root": str(root), "registry": "r", "image_prefix": "r/nap-store-",
                                     "keep": 3, "ready_seconds": 1})
        self.root = root

    def tearDown(self):
        self.tmp.cleanup()

    def payload(self, **kw):
        p = {"sha": "b" * 40, "backend": D, "web": D, "bundle": bundle(GOOD)}
        p.update(kw)
        return json.dumps(p)

    def test_valid_payload_unpacks_with_release_env(self):
        p = self.srv.read_payload(self.payload())
        rel = self.srv.unpack(p)
        env = (self.root / "releases" / ("b" * 40) / "release.env").read_text()
        self.assertIn("IMAGE_BACKEND=r/nap-store-backend@" + D, env)
        self.assertIn("NAP_BUILD=" + "b" * 40, env)
        self.assertEqual(rel["sha"], "b" * 40)

    def test_rejects_bad_sha_and_digest(self):
        for kw in ({"sha": "../x"}, {"sha": "B" * 40}, {"backend": "latest"}, {"web": "sha256:zz"}):
            with self.assertRaises(napdeploy.Stop):
                self.srv.read_payload(self.payload(**kw))

    def test_rejects_unexpected_bundle_files(self):
        for extra in ({"etc/passwd": b"x"}, {"ops/vps/../../x": b"x"}, {"ops/vps/link": None},
                      {"ops/other.sh": b"x"}, {"/abs": b"x"}):
            p = self.srv.read_payload(self.payload(bundle=bundle({**GOOD, **extra})))
            with self.assertRaises(napdeploy.Stop):
                self.srv.unpack(p)
            self.assertFalse((self.root / "releases" / ("b" * 40)).exists())
            self.assertEqual([x for x in (self.root / "releases").iterdir()], [])

    def test_bundle_must_contain_compose(self):
        p = self.srv.read_payload(self.payload(bundle=bundle({"ops/backup-loop.sh": b"x"})))
        with self.assertRaises(napdeploy.Stop):
            self.srv.unpack(p)

    def test_gate_refuses_other_commands(self):
        cfg = pathlib.Path(self.tmp.name, "cfg.json")
        cfg.write_text(json.dumps({"root": str(self.root)}))
        for cmd in ("rm -rf /", "deploy now", "logs ../../etc", "bash", ""):
            r = subprocess.run([sys.executable, napdeploy.__file__, "gate"], input="", text=True, capture_output=True,
                               env={**os.environ, "SSH_ORIGINAL_COMMAND": cmd, "NAPDEPLOY_CONFIG": str(cfg)})
            self.assertEqual(r.returncode, 2, cmd)
            self.assertEqual(json.loads(r.stdout)["status"], "error")

    def test_status_without_release(self):
        cfg = pathlib.Path(self.tmp.name, "cfg.json")
        cfg.write_text(json.dumps({"root": str(self.root)}))
        r = subprocess.run([sys.executable, napdeploy.__file__, "gate"], text=True, capture_output=True,
                           env={**os.environ, "SSH_ORIGINAL_COMMAND": "status", "NAPDEPLOY_CONFIG": str(cfg)})
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIsNone(json.loads(r.stdout)["current"])


if __name__ == "__main__":
    unittest.main()
