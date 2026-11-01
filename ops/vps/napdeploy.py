#!/usr/bin/env python3
"""NAP-087: release runner for the dedicated store server. Standard library only.

Installed root-owned at /opt/nap-deploy/napdeploy.py by bootstrap.sh and run as the unprivileged
`napdeploy` user (docker group). GitHub Actions reaches it over SSH with a key that is restricted to
`command="... napdeploy.py gate",restrict`, so the only thing that key can do is one of:

  deploy             stdin: one JSON line {sha, backend, web, token?, bundle} (see read_payload)
  rollback           switch back to the previous release (images are still local)
  status             current/previous release and container states, JSON
  logs <service>     last 200 lines of one service, lines that look like secrets removed
  backup             one database backup cycle now
  restart            apply edited shared/.env: recreate containers of the current release, check readiness

deploy: pull the two images by digest -> unpack the small release bundle (compose file, Caddyfile,
backup scripts) -> refuse dangerous compose settings -> pg_dump of the live database -> migrations
(`alembic upgrade head`, transactional) -> start the new containers -> readiness: /api/v1/version must
report this commit and /ru must answer -> on failure the previous release is started again and checked.
Migrations must stay backward compatible (add first, remove one release later): a rollback never
downgrades the schema; restoring a pre-deploy dump is a manual, explicit operation.
"""
from __future__ import annotations

import base64
import fcntl
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

CONFIG_PATH = Path(os.environ.get("NAPDEPLOY_CONFIG", "/opt/nap-deploy/config.json"))
PROJECT = "nap-store"
APP = ["api", "web", "worker-media", "worker-ops"]
LOG_SERVICES = APP + ["migrate", "caddy", "postgres", "redis", "backup"]
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
BUNDLE_ALLOWED = re.compile(r"ops/(vps/[A-Za-z0-9_.-]+|backup-loop\.sh|backup\.sh|restore_verify\.sh)\Z")
COMPOSE = "ops/vps/docker-compose.prod.yml"
MAX_PAYLOAD = 16 * 1024 * 1024
SECRETISH = re.compile(r"secret|password|passwd|token|authorization|api[_-]?key|cookie", re.I)


class Stop(RuntimeError):
    pass


def need(ok, msg):
    if not ok:
        raise Stop(msg)


def load_config():
    cfg = {"root": "/srv/nap-store", "registry": "ghcr.io", "image_prefix": "ghcr.io/brothersb/nap-store-",
           "keep": 5, "ready_seconds": 180}
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text()))
    return cfg


def run(cmd, *, env=None, input=None, timeout=600, check=True, capture=True):
    full = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "DOCKER_HOST", "XDG_RUNTIME_DIR")}
    full.update(env or {})
    r = subprocess.run(cmd, env=full, input=input, timeout=timeout, capture_output=capture, text=True)
    if check and r.returncode != 0:
        # stderr can contain configuration values: report only the command and a redacted tail
        tail = "\n".join(l for l in (r.stderr or "").splitlines()[-15:] if not SECRETISH.search(l))
        raise Stop(f"command failed: {' '.join(cmd[:4])} …\n{tail}")
    return r


class Server:
    def __init__(self, cfg):
        self.cfg = cfg
        self.root = Path(cfg["root"])
        self.state_file = self.root / "state" / "state.json"

    # ---------- state ----------
    def state(self):
        if self.state_file.exists():
            return json.loads(self.state_file.read_text())
        return {"current": None, "previous": None, "history": []}

    def save(self, st):
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=2, sort_keys=True))
        os.replace(tmp, self.state_file)

    def lock(self):
        f = open(self.root / "state" / "lock", "w")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Stop("another operation is running")
        return f

    # ---------- compose ----------
    def compose(self, rel, *args, timeout=600, check=True):
        d = self.root / "releases" / rel["sha"]
        cmd = ["docker", "compose", "-p", PROJECT, "--project-directory", str(d / "ops/vps"),
               "--env-file", str(self.root / "shared/.env"), "--env-file", str(d / "release.env"),
               "-f", str(d / COMPOSE), *args]
        return run(cmd, timeout=timeout, check=check)

    def image(self, name, digest):
        return f"{self.cfg['image_prefix']}{name}@{digest}"

    # ---------- deploy steps ----------
    def read_payload(self, raw):
        need(len(raw) <= MAX_PAYLOAD, "payload too large")
        p = json.loads(raw)
        need(isinstance(p, dict) and SHA.fullmatch(p.get("sha", "")), "bad commit sha")
        for k in ("backend", "web"):
            need(DIGEST.fullmatch(p.get(k, "")), f"bad {k} image digest")
        need(isinstance(p.get("bundle"), str), "bundle missing")
        return p

    def pull(self, p):
        images = [self.image("backend", p["backend"]), self.image("web", p["web"])]
        with tempfile.TemporaryDirectory() as dc:  # credentials live only for this pull
            env = {"DOCKER_CONFIG": dc}
            if p.get("token"):
                run(["docker", "login", self.cfg["registry"], "-u", p.get("user") or "github-actions",
                     "--password-stdin"], env=env, input=p["token"], timeout=60)
            for ref in images:
                run(["docker", "pull", "-q", ref], env=env, timeout=900)

    def unpack(self, p):
        dest = self.root / "releases" / p["sha"]
        tmp = Path(tempfile.mkdtemp(prefix=".unpack-", dir=self.root / "releases"))
        try:
            data = base64.b64decode(p["bundle"], validate=True)
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
                names = set()
                for m in tar.getmembers():
                    name = m.name[2:] if m.name.startswith("./") else m.name
                    if m.isdir():
                        continue
                    need(m.isfile() and BUNDLE_ALLOWED.fullmatch(name) and ".." not in PurePosixPath(name).parts,
                         f"unexpected file in bundle: {name[:80]}")
                    target = tmp / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(m) as src, open(target, "xb") as dst:
                        shutil.copyfileobj(src, dst)
                    target.chmod(0o755 if m.mode & 0o111 else 0o644)
                    names.add(name)
            need(COMPOSE in names and "ops/backup-loop.sh" in names, "bundle lacks compose file or backup script")
            (tmp / "release.env").write_text(
                f"IMAGE_BACKEND={self.image('backend', p['backend'])}\n"
                f"IMAGE_WEB={self.image('web', p['web'])}\n"
                f"NAP_BUILD={p['sha']}\nSTORE_ROOT={self.root}\n")
            if dest.exists():
                shutil.rmtree(dest)  # a re-delivery of the same commit replaces its own unpacked files only
            os.replace(tmp, dest)
        finally:
            if tmp.exists():
                shutil.rmtree(tmp)
        return {"sha": p["sha"], "backend": p["backend"], "web": p["web"]}

    def audit(self, rel):
        """Refuse compose settings that would hand the host to a container."""
        cfg = json.loads(self.compose(rel, "--profile", "tools", "config", "--format", "json").stdout)
        allowed_bind = [str(self.root / "releases" / rel["sha"]), str(self.root / "backups")]
        for name, s in cfg["services"].items():
            need(not s.get("privileged") and not s.get("cap_add") and not s.get("devices"), f"{name}: privileges")
            need(s.get("network_mode") != "host" and s.get("pid") != "host" and s.get("ipc") != "host", f"{name}: host namespace")
            for v in s.get("volumes", []):
                if v.get("type") == "bind":
                    src = os.path.realpath(v["source"])
                    need(any(src == a or src.startswith(a + "/") for a in allowed_bind), f"{name}: bind mount {src}")
            need(name == "caddy" or not s.get("ports"), f"{name}: only caddy may publish ports")
        need(cfg["services"]["api"]["image"] == self.image("backend", rel["backend"]), "api image mismatch")
        need(cfg["services"]["web"]["image"] == self.image("web", rel["web"]), "web image mismatch")

    def running(self, rel):
        r = self.compose(rel, "ps", "-q", "postgres", check=False)
        return bool(r.stdout.strip())

    def predeploy_backup(self, rel, sha):
        if not self.running(rel):
            return None
        out = self.root / "backups" / f"pre-deploy-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{sha[:12]}.dump"
        with open(out, "wb") as f:
            d = self.root / "releases" / rel["sha"]
            cmd = ["docker", "compose", "-p", PROJECT, "--project-directory", str(d / "ops/vps"),
                   "--env-file", str(self.root / "shared/.env"), "--env-file", str(d / "release.env"),
                   "-f", str(d / COMPOSE), "exec", "-T", "postgres", "pg_dump", "-U", "nap", "-Fc", "nap"]
            r = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, timeout=900)
        need(r.returncode == 0 and out.stat().st_size > 0, "pre-deploy database dump failed")
        dumps = sorted(self.root.glob("backups/pre-deploy-*.dump"))
        for old in dumps[:-self.cfg["keep"]]:
            old.unlink()
        return out.name

    def ready(self, rel, seconds=None):
        deadline = time.monotonic() + (seconds or self.cfg["ready_seconds"])
        probe_api = ("import json,urllib.request;d=json.load(urllib.request.urlopen('http://localhost:8000/api/v1/version',timeout=3));"
                     "d=d.get('data',d);print(d.get('build'))")
        probe_web = ("fetch('http://127.0.0.1:3000/ru').then(r=>process.exit(r.ok?0:1),()=>process.exit(1))")
        last = "not started"
        while time.monotonic() < deadline:
            a = self.compose(rel, "exec", "-T", "api", "python", "-c", probe_api, check=False, timeout=30)
            if a.returncode == 0 and a.stdout.strip() == rel["sha"]:
                w = self.compose(rel, "exec", "-T", "web", "node", "-e", probe_web, check=False, timeout=30)
                if w.returncode == 0:
                    return
                last = "web /ru not ready"
            else:
                last = "api not ready" if a.returncode else f"api reports build {a.stdout.strip()[:12]}"
            time.sleep(3)
        raise Stop("readiness failed: " + last)

    def activate(self, rel):
        self.compose(rel, "up", "-d", "--pull", "missing", "--remove-orphans", "--wait", "--wait-timeout", "180",
                     "postgres", "redis", timeout=300)
        self.compose(rel, "up", "-d", "--pull", "missing", "--remove-orphans", timeout=600)
        self.ready(rel)

    def prune(self, st):
        keep = {r["sha"] for r in st["history"][-self.cfg["keep"]:]}
        keep |= {r["sha"] for r in (st["current"], st["previous"]) if r}
        for d in (self.root / "releases").iterdir():
            if d.is_dir() and SHA.fullmatch(d.name) and d.name not in keep:
                shutil.rmtree(d)
        live = {self.image(n, r[k]) for r in st["history"] if r["sha"] in keep for n, k in (("backend", "backend"), ("web", "web"))}
        for r in st["history"]:
            if r["sha"] not in keep:
                for ref in (self.image("backend", r["backend"]), self.image("web", r["web"])):
                    if ref not in live:
                        run(["docker", "image", "rm", ref], check=False)
        st["history"] = [r for r in st["history"] if r["sha"] in keep]
        run(["docker", "image", "prune", "-f"], check=False)

    # ---------- operations ----------
    def deploy(self, raw):
        t0 = time.monotonic()
        p = self.read_payload(raw)
        st = self.state()
        cur = st["current"]
        if cur and cur["sha"] == p["sha"] and cur["backend"] == p["backend"] and cur["web"] == p["web"]:
            self.ready(cur, 30)
            return {"status": "already_current", "sha": p["sha"]}
        self.pull(p)
        rel = self.unpack(p)
        self.audit(rel)
        backup = self.predeploy_backup(cur, p["sha"]) if cur else None
        try:
            self.compose(rel, "up", "-d", "--pull", "missing", "--wait", "--wait-timeout", "180",
                         "postgres", "redis", timeout=300)
            self.compose(rel, "--profile", "tools", "run", "--rm", "--no-deps", "migrate", timeout=1200)
            self.activate(rel)
        except Stop as err:
            if not cur:
                raise Stop(f"first release failed, nothing to return to: {err}")
            try:
                self.activate(cur)
            except Stop as err2:
                raise Stop(f"release failed ({err}) AND the previous release did not come back ({err2}); "
                           "manual action needed")
            st.setdefault("failures", []).append({"sha": p["sha"], "at": int(time.time()), "error": str(err)[:300]})
            st["failures"] = st["failures"][-10:]
            self.save(st)
            return {"status": "failed_rolled_back", "failed": p["sha"], "current": cur["sha"], "error": str(err)[:500],
                    "backup": backup}
        rel["deployed_at"] = int(time.time())
        st["history"] = [r for r in st["history"] if r["sha"] != rel["sha"]] + [rel]
        st["previous"], st["current"] = cur, rel
        self.prune(st)
        self.save(st)
        return {"status": "deployed", "sha": rel["sha"], "previous": cur and cur["sha"], "backup": backup,
                "seconds": round(time.monotonic() - t0)}

    def rollback(self):
        st = self.state()
        need(st["previous"], "no previous release")
        cur, prev = st["current"], st["previous"]
        need((self.root / "releases" / prev["sha"]).is_dir(), "previous release files are gone")
        self.activate(prev)
        st["current"], st["previous"] = prev, cur
        self.save(st)
        return {"status": "rolled_back", "current": prev["sha"], "previous": cur["sha"]}

    def restart(self):
        st = self.state()
        need(st["current"], "nothing deployed yet")
        self.activate(st["current"])
        return {"status": "ok", "current": st["current"]["sha"]}

    def status(self):
        st = self.state()
        out = {"current": st["current"] and st["current"]["sha"], "previous": st["previous"] and st["previous"]["sha"],
               "failures": st.get("failures", [])[-3:]}
        if st["current"]:
            r = self.compose(st["current"], "ps", "--format", "json", check=False)
            rows = [json.loads(l) for l in r.stdout.splitlines() if l.strip().startswith("{")]
            if len(rows) == 1 and isinstance(rows[0], list):
                rows = rows[0]
            out["services"] = {x.get("Service"): x.get("State") + (f" ({x['Health']})" if x.get("Health") else "") for x in rows}
        du = shutil.disk_usage(self.root)
        mem = dict(l.split(":", 1) for l in Path("/proc/meminfo").read_text().splitlines())
        out["disk_free_gib"] = round(du.free / 1024**3, 1)
        out["mem_available_mib"] = int(mem["MemAvailable"].split()[0]) // 1024
        return out

    def logs(self, service):
        need(service in LOG_SERVICES, "unknown service")
        st = self.state()
        need(st["current"], "nothing deployed yet")
        r = self.compose(st["current"], "logs", "--no-color", "--tail", "200", service, check=False)
        lines = (r.stdout + r.stderr).splitlines()
        return "\n".join(l if not SECRETISH.search(l) else "[line removed: may contain a secret]" for l in lines)

    def backup(self):
        st = self.state()
        need(st["current"], "nothing deployed yet")
        self.compose(st["current"], "exec", "-T", "-e", "BACKUP_ONCE=1", "backup", "bash", "/ops/backup-loop.sh", timeout=1800)
        return json.loads((self.root / "backups" / "last-status.json").read_text())


def main(argv):
    cfg = load_config()
    if argv[:1] == ["gate"]:
        argv = os.environ.get("SSH_ORIGINAL_COMMAND", "").split()
    need(argv, "usage: deploy | rollback | restart | status | logs <service> | backup")
    srv = Server(cfg)
    op = argv[0]
    if op == "status" and len(argv) == 1:
        print(json.dumps(srv.status(), indent=2))
        return 0
    if op == "logs" and len(argv) == 2:
        print(srv.logs(argv[1]))
        return 0
    held = srv.lock()
    try:
        if op == "deploy" and len(argv) == 1:
            res = srv.deploy(sys.stdin.read(MAX_PAYLOAD + 1))
        elif op == "rollback" and len(argv) == 1:
            res = srv.rollback()
        elif op == "restart" and len(argv) == 1:
            res = srv.restart()
        elif op == "backup" and len(argv) == 1:
            res = srv.backup()
        else:
            raise Stop("command not allowed")
    finally:
        held.close()
    print(json.dumps(res, indent=2))
    return 0 if res.get("status") in ("deployed", "already_current", "rolled_back", "ok") else 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Stop as e:
        print(json.dumps({"status": "error", "error": str(e)[:2000]}, indent=2))
        sys.exit(2)
    except (OSError, ValueError, subprocess.TimeoutExpired, tarfile.TarError) as e:
        print(json.dumps({"status": "error", "error": type(e).__name__ + ": " + str(e)[:300]}, indent=2))
        sys.exit(2)
