#!/usr/bin/env python3
"""Inactive, Linux-only release candidate. Defaults to read-only validation.
No nginx/DNS, package installation, production bootstrap, or database restore.
See docs/release-safety.md before use. Only standard library dependencies.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

APP_SERVICES = ["api", "web", "worker-media", "worker-ops"]
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024

class Refusal(RuntimeError):
    pass

def require(ok, message):
    if not ok:
        raise Refusal(message)

def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def atomic_json(path, data):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        os.chmod(tmp, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, sort_keys=True, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def sync_directory(path):
    if sys.platform == "linux":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

def escape_compose(value):
    # Compose parses the frozen JSON again on up/build. Preserve literal dollars
    # from the first render (passwords and shell commands), not host interpolation.
    if isinstance(value, str):
        return value.replace("$", "$$")
    if isinstance(value, list):
        return [escape_compose(v) for v in value]
    if isinstance(value, dict):
        return {k: escape_compose(v) for k, v in value.items()}
    return value

def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

def archive_members(path, sha, expected):
    require(SHA.fullmatch(sha), "Full lowercase commit SHA required")
    require(DIGEST.fullmatch(expected), "Approved archive SHA-256 required")
    require(Path(path).is_file() and digest(path) == expected, "Archive SHA-256 mismatch")
    with tarfile.open(path, "r:gz") as tar:
        require(tar.pax_headers.get("comment") == sha, "Use git archive of the approved commit")
        members = tar.getmembers()
        seen, total = set(), 0
        for m in members:
            p = PurePosixPath(m.name)
            require(not p.is_absolute() and ".." not in p.parts and "\\" not in m.name,
                    "Unsafe archive path")
            require(m.name.rstrip("/") == p.as_posix() and p.as_posix() != ".", "Noncanonical archive path")
            require(p.as_posix() not in seen, "Duplicate archive path")
            seen.add(p.as_posix())
            require(m.isfile() or m.isdir(), "Links/devices are not accepted in a release")
            require(not any(x in {".git", ".env", "node_modules", ".venv"} for x in p.parts),
                    "Local state or secrets directory in archive")
            require(not any(x.startswith(".env.") and x != ".env.example" for x in p.parts),
                    "Private environment file in archive")
            total += m.size
            require(total <= MAX_ARCHIVE_BYTES, "Archive exceeds source-only size budget")
        require({"docker-compose.yml", "ops/cohost/docker-compose.cohost.yml", "release.json"} <= seen,
                "Archive is not a Store release")
        return members

def tree_hashes(path):
    result = {}
    for p in Path(path).rglob("*"):
        require(not p.is_symlink(), "Symlink in immutable release")
        if p.is_file():
            result[p.relative_to(path).as_posix()] = digest(p)
    return result

def schema_hashes(files):
    # Upgrades may not implicitly change schema, DB/storage image or topology.
    return {k: v for k, v in files.items() if k.startswith("backend/migrations/") or
            k in {"docker-compose.yml", "ops/cohost/docker-compose.cohost.yml"}}

def clean_environment():
    # Do not allow shell NAP_*/COMPOSE_* variables to override the reviewed env file.
    keep = ["PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG",
            "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "XDG_RUNTIME_DIR"]
    return {k: os.environ[k] for k in keep if k in os.environ}

class Engine:
    def run(self, command, cwd=None, extra=None, timeout=180):
        env = clean_environment()
        env.update(extra or {})
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                                text=True, timeout=timeout)
        # Never echo compose config, stderr or stdout: they can contain secrets.
        require(result.returncode == 0, "Command failed: " + command[0] + " (output withheld)")
        return result.stdout

    def compose(self, meta, *args, timeout=180):
        return self.run(["docker", "compose", "-p", "nap-store", "-f", meta["config"],
                         *args], cwd=meta["release"], timeout=timeout)

    def verify_images(self, meta):
        for image_id in set(meta["images"].values()):
            require(image_id.startswith("sha256:"), "Unpinned image")
            got = self.run(["docker", "image", "inspect", "--format", "{{.Id}}", image_id]).strip()
            require(got == image_id, "Recorded rollback image is missing")

    def verify_running(self, meta):
        config = read_json(meta["config"])
        for name in APP_SERVICES:
            ids = self.compose(meta, "ps", "-q", name).split()
            require(len(ids) == 1, "Managed service is missing or duplicated")
            container = json.loads(self.run(["docker", "inspect", ids[0]]))[0]
            require(container["State"]["Running"] and container["Image"] == meta["images"][name],
                    "Running service image differs from recorded release")
            labels = container["Config"]["Labels"]
            require(labels.get("com.docker.compose.project") == "nap-store" and
                    labels.get("com.docker.compose.service") == name, "Container ownership mismatch")
            actual = dict(item.split("=", 1) for item in container["Config"]["Env"] if "=" in item)
            expected = config["services"][name].get("environment", {})
            require(all(actual.get(k) == str(v).replace("$$", "$") for k, v in expected.items()), "Running environment drift")

    def activate(self, meta):
        self.verify_images(meta)
        self.compose(meta, "up", "-d", "--no-deps", "--no-build", "--pull", "never", *APP_SERVICES)

    def ready(self, meta, seconds=120):
        deadline = time.monotonic() + seconds
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base = "http://127.0.0.1:" + str(meta["port"])
        while time.monotonic() < deadline:
            try:
                for suffix in ["/ru", "/en", "/api/v1/ready"]:
                    with opener.open(base + suffix, timeout=3) as response:
                        require(response.status == 200, "Readiness HTTP status")
                with opener.open(base + "/api/v1/version", timeout=3) as response:
                    data = json.load(response)
                # API may wrap its response in a data envelope.
                require(data.get("build", data.get("data", {}).get("build")) == meta["sha"],
                        "Readiness returned another release")
                return
            except Exception:
                time.sleep(1)
        raise Refusal("Bounded readiness failed (RU/EN/DB/build)")

class Controller:
    def __init__(self, root, engine=None):
        self.root = Path(root)
        self.engine = engine or Engine()
        self.state_path = self.root / "release-state.json"
        self.lock = self.root / ".release-lock"

    def validate_root(self):
        require(self.root.is_absolute() and self.root.exists(), "Provision an absolute Store root first")
        require(str(self.root.resolve()) == str(self.root), "Root must not traverse symlinks")
        for sub in ["releases", "shared", "control"]:
            p = self.root / sub
            require(p.is_dir() and not p.is_symlink(), "Provision releases/shared/control directories first")
        require(not self.state_path.is_symlink(), "State file must not be a symlink")

    def state(self):
        if not self.state_path.exists():
            require(not os.path.lexists(self.root / "current"), "Unmanaged current release; inspect manually")
            return {"current": None, "previous": None, "status": "idle"}
        state = read_json(self.state_path)
        require(state["status"] == "idle", "Unfinished operation; manual reconciliation required")
        cur = state["current"]
        if cur:
            self.verify_meta(cur)
            require((self.root / "current").is_symlink() and
                    (self.root / "current").resolve() == Path(cur["release"]), "Current pointer changed")
        return state

    def verify_meta(self, meta):
        require(SHA.fullmatch(meta["sha"]), "Invalid stored commit")
        release = self.root / "releases" / meta["sha"]
        require(meta["release"] == str(release) and not release.is_symlink(), "Release path escaped root")
        require(tree_hashes(release) == meta["files"], "Release files changed since preparation")
        control = self.root / "control" / meta["sha"]
        require(not control.is_symlink() and control.resolve() == control, "Control directory escaped root")
        for field in ["config", "env"]:
            p = Path(meta[field])
            require(p.parent == control and not p.is_symlink(), "Control path escaped root")
            require(digest(p) == meta[field + "_hash"], "Frozen configuration changed")

    def expected(self, state, expected):
        actual = state["current"]["sha"] if state["current"] else "none"
        require(actual == expected, "Current release differs from expected; reconcile first")

    def resources(self):
        require(shutil.disk_usage(self.root).free >= 8 * 1024**3, "Less than 8 GiB free disk")
        fields = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        require(int(fields["MemAvailable"].split()[0]) >= 4 * 1024**2,
                "Less than 4 GiB available RAM; obtain a separate resource plan")

    def prepare(self, args, state):
        source = self.root / "releases" / args.sha
        control = self.root / "control" / args.sha
        require(not source.exists() and not control.exists(), "Release already exists; never overwrite it")
        env = self.root / "shared" / ".env"
        require(not env.is_symlink() and digest(env) == args.env_sha256, "Shared env differs from approved hash")
        require(env.stat().st_mode & 0o077 == 0, "Shared env must be private (mode 600)")
        # Archive is checked again while holding the lock. No extractall or symlinks.
        members = archive_members(args.archive, args.sha, args.archive_sha256)
        source.mkdir()
        control.mkdir(mode=0o700)
        with tarfile.open(args.archive, "r:gz") as tar:
            for member in members:
                target = source / member.name
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(member) as src, target.open("xb") as dst:
                        shutil.copyfileobj(src, dst)
                    target.chmod(0o755 if member.mode & 0o111 else 0o644)
        require(digest(args.archive) == args.archive_sha256, "Archive changed during extraction")
        files = tree_hashes(source)
        if state["current"]:
            require(schema_hashes(files) == schema_hashes(state["current"]["files"]),
                    "Schema/topology change requires a separately reviewed migration; no activation")
            require(args.env_sha256 == state["current"]["env_hash"],
                    "Environment changes require a separate rollout plan")
        frozen_env = control / "release.env"
        shutil.copyfile(env, frozen_env)
        frozen_env.chmod(0o600)
        require(digest(frozen_env) == args.env_sha256, "Environment changed during snapshot")
        base = ["docker", "compose", "-p", "nap-store", "--env-file", str(frozen_env),
                "-f", str(source / "docker-compose.yml"),
                "-f", str(source / "ops/cohost/docker-compose.cohost.yml")]
        extra = {"NAP_BUILD": args.sha, "STORE_SHARED": str(self.root / "shared")}
        config = json.loads(self.engine.run(base + ["config", "--format", "json"], cwd=source, extra=extra))
        api_env = config["services"]["api"]["environment"]
        require(api_env.get("NAP_ENV") == "demo", "Only a closed demo rollout is supported by this candidate")
        require(api_env.get("NAP_COOKIE_SECURE") == "true", "Secure cookies required")
        require(api_env.get("NAP_PUBLIC_BASE_URL") == "https://store.napsystem.com", "Unexpected public URL")
        require(len(api_env.get("NAP_SECRET_KEY", "")) >= 32 and
                not any(x in api_env["NAP_SECRET_KEY"] for x in ["change-me", "dev-"]), "Set a real secret")
        require(len(config["services"]["postgres"]["environment"].get("POSTGRES_PASSWORD", "")) >= 24,
                "Set a real database password")
        require(api_env.get("NAP_STORAGE_BACKEND") == "local", "S3 rollout needs a separate data/backup plan")
        ports = config["services"]["web"].get("ports", [])
        require(len(ports) == 1 and ports[0].get("host_ip") == "127.0.0.1", "Web must bind only loopback")
        port = int(ports[0]["published"])
        require(1024 <= port <= 65535, "Invalid web port")
        if not state["current"]:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", port))  # fail if another site already owns the port
            for volume in config.get("volumes", {}).values():
                name = volume.get("name", "")
                require(name.startswith("nap-store_") and not volume.get("external"), "Unexpected volume ownership")
                names = self.engine.run(["docker", "volume", "ls", "-q", "--filter", "name=^" + name + "$"])
                require(not names.strip(), "Existing named volume; initial bootstrap forbidden")
        for name, service in config["services"].items():
            require(name == "web" or not service.get("ports"), "Unexpected published port")
        tags = {name: "nap-store-" + ("web" if name == "web" else "backend") + ":" + args.sha
                for name in APP_SERVICES + ["migrate"]}
        for name, tag in tags.items():
            config["services"][name]["image"] = tag
        cfg = control / "compose.json"
        atomic_json(cfg, escape_compose(config))
        meta = {"sha": args.sha, "release": str(source), "config": str(cfg), "env": str(frozen_env),
                "env_hash": digest(frozen_env), "files": files, "port": port,
                "archive_hash": args.archive_sha256}
        self.engine.compose(meta, "build", "api", "web", timeout=900)
        ids = {}
        for name, tag in tags.items():
            image_id = self.engine.run(["docker", "image", "inspect", "--format", "{{.Id}}", tag]).strip()
            require(re.fullmatch(r"sha256:[0-9a-f]{64}", image_id), "Invalid built image ID")
            ids[name] = image_id
            config["services"][name]["image"] = image_id
            config["services"][name].pop("build", None)
        # Pre-provisioned infrastructure images only; no implicit image pull at bootstrap.
        for name in ["postgres", "redis", "backup"]:
            image_id = self.engine.run(["docker", "image", "inspect", "--format", "{{.Id}}",
                                       config["services"][name]["image"]]).strip()
            require(re.fullmatch(r"sha256:[0-9a-f]{64}", image_id), "Provision infrastructure images first")
            config["services"][name]["image"] = image_id
            ids[name] = image_id
        atomic_json(cfg, escape_compose(config))
        meta.update(images=ids, config_hash=digest(cfg))
        self.verify_meta(meta)
        return meta

    def pointer(self, meta):
        link = self.root / ".current-next"
        require(not os.path.lexists(link), "Pending pointer exists; reconcile manually")
        link.symlink_to(meta["release"], target_is_directory=True)
        os.replace(link, self.root / "current")
        sync_directory(self.root)

    def transition(self, state, target, bootstrap=False):
        old = state["current"]
        atomic_json(self.state_path, {**state, "status": "activating", "target": target})
        try:
            if bootstrap:
                # No existing stack is allowed at first bootstrap. This is demo data only.
                self.engine.compose(target, "up", "-d", "--no-deps", "--pull", "never", "--wait", "--wait-timeout", "90", "postgres", "redis")
                self.engine.compose(target, "run", "--rm", "--no-deps", "migrate", timeout=600)
            self.engine.activate(target)
            self.engine.ready(target)
            self.engine.verify_running(target)
            if bootstrap:
                self.engine.compose(target, "up", "-d", "--no-deps", "--pull", "never", "backup")
            self.pointer(target)
            atomic_json(self.state_path, {"current": target, "previous": old, "status": "idle"})
        except BaseException:
            # An unknown/failed DB operation is never replayed or reversed automatically.
            if old:
                try:
                    self.verify_meta(old)
                    self.engine.activate(old)
                    self.engine.ready(old)
                    self.engine.verify_running(old)
                    self.pointer(old)
                    atomic_json(self.state_path, {**state, "status": "idle", "last_failure": target["sha"]})
                except BaseException:
                    atomic_json(self.state_path, {**state, "status": "recovery_required", "target": target})
                    raise Refusal("Activation AND recovery failed; lock retained, operator action required")
                raise Refusal("Activation failed; prior images/config restored and readiness verified")
            try:
                self.engine.compose(target, "stop", *APP_SERVICES, "backup", "postgres", "redis")
            finally:
                atomic_json(self.state_path, {**state, "status": "initial_recovery_required", "target": target})
            raise Refusal("Initial demo failed; services stopped, data preserved, manual inspection required")

    def execute(self, args):
        self.validate_root()
        state = self.state()
        self.expected(state, args.expected_current)
        require(DIGEST.fullmatch(args.env_sha256), "Approved environment hash required")
        require(digest(self.root / "shared" / ".env") == args.env_sha256, "Environment hash mismatch")
        if args.operation == "deploy":
            archive_members(args.archive, args.sha, args.archive_sha256)
            require(args.sha != args.expected_current, "Already current; no replay")
        else:
            require(state["previous"], "No previous release recorded")
            self.verify_meta(state["previous"])
        if not args.apply:
            print("PLAN ONLY: inputs verified. No Docker, builds, HTTP, extraction or activation performed.")
            return
        require(sys.platform == "linux", "Apply is Linux-only")
        require(os.geteuid() != 0, "Use the approved dedicated account, not root")
        require(args.closed_demo, "Explicit --closed-demo required; not a public/production installer")
        if not state["current"]:
            require(args.operation == "deploy" and args.bootstrap_demo, "Initial demo requires --bootstrap-demo")
        require(not self.lock.exists(), "Another operation or interrupted lock exists; do not remove blindly")
        self.lock.mkdir(mode=0o700)  # atomic cross-process lock; never expire automatically
        keep_lock = True
        transition_started = False
        try:
            atomic_json(self.lock / "owner.json", {"pid": os.getpid(), "operation": args.operation})
            state = self.state()
            self.expected(state, args.expected_current)
            self.resources()
            if not state["current"]:
                active = self.engine.run(["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=nap-store"])
                require(not active.strip(), "Existing unmanaged Store containers; bootstrap forbidden")
                volumes = self.engine.run(["docker", "volume", "ls", "-q", "--filter", "label=com.docker.compose.project=nap-store"])
                require(not volumes.strip(), "Existing Store data; bootstrap forbidden")
            else:
                self.verify_meta(state["current"])
                self.engine.verify_images(state["current"])
                self.engine.ready(state["current"], seconds=15)
                self.engine.verify_running(state["current"])
            target = self.prepare(args, state) if args.operation == "deploy" else state["previous"]
            if args.operation == "rollback":
                require(target["env_hash"] == args.env_sha256 and
                        schema_hashes(target["files"]) == schema_hashes(state["current"]["files"]),
                        "Rollback requires a separately reviewed schema/config recovery")
            # The build may take minutes: recheck state/env immediately before activation.
            self.expected(self.state(), args.expected_current)
            require(digest(self.root / "shared" / ".env") == args.env_sha256, "Shared environment changed during preparation")
            if state["current"]:
                self.engine.verify_running(state["current"])
            transition_started = True
            self.transition(state, target, bootstrap=not state["current"])
            keep_lock = False
            print("Release ready: " + target["sha"])
        finally:
            if transition_started and self.state_path.exists() and read_json(self.state_path).get("status") == "idle":
                keep_lock = False
            if not keep_lock:
                (self.lock / "owner.json").unlink(missing_ok=True)
                self.lock.rmdir()
            # Ambiguous interruption/build failure leaves lock/evidence for the operator.

def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("operation", choices=["deploy", "rollback"])
    p.add_argument("--root", default="/srv/nap-store")
    p.add_argument("--expected-current", required=True, help="Full current SHA, or none for initial demo")
    p.add_argument("--env-sha256", required=True, help="Reviewed shared/.env SHA-256; never pass contents")
    p.add_argument("--sha")
    p.add_argument("--archive")
    p.add_argument("--archive-sha256")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--closed-demo", action="store_true")
    p.add_argument("--bootstrap-demo", action="store_true")
    return p

if __name__ == "__main__":
    try:
        args = parser().parse_args()
        if args.operation == "deploy":
            require(args.sha and args.archive and args.archive_sha256, "Deploy requires archive, SHA and SHA-256")
        Controller(args.root).execute(args)
    except (Refusal, OSError, ValueError, subprocess.TimeoutExpired, tarfile.TarError) as error:
        # No command stdout/stderr or environment data in the error message.
        print("STOP: " + str(error) if isinstance(error, Refusal) else "STOP: local operation failed; inspect privately", file=sys.stderr)
        sys.exit(1)
