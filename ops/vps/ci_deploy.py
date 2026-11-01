#!/usr/bin/env python3
"""NAP-087: GitHub Actions side of the store delivery. Talks to ops/vps/napdeploy.py over SSH.
  ci_deploy.py deploy                 env SHA, BACKEND, WEB (image digests), PULL_TOKEN, PULL_USER
  ci_deploy.py status|rollback|backup|restart
  ci_deploy.py logs <service>
Connection: env SSH_KEY (secret), HOST, HOST_KEY ("ssh-ed25519 AAAA…"). The host key is pinned:
an unknown or changed server key stops the job. The SSH key on the server is forced to napdeploy.py.
"""
import base64
import json
import os
import pathlib
import subprocess
import sys
import tempfile

BUNDLE = ["ops/vps", "ops/backup-loop.sh", "ops/backup.sh", "ops/restore_verify.sh"]


def summary(text):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as f:
            f.write(text + "\n")


def main(argv):
    op = argv[:1] or ["status"]
    for k in ("SSH_KEY", "HOST", "HOST_KEY"):
        if not os.environ.get(k):
            sys.exit(f"{k} is not configured (see docs/vps-deploy.md)")
    stdin = b""
    if op == ["deploy"]:
        sha = os.environ["SHA"]
        bundle = subprocess.run(["git", "archive", "--format=tar.gz", sha, *BUNDLE], check=True,
                                capture_output=True).stdout
        payload = {"sha": sha, "backend": os.environ["BACKEND"], "web": os.environ["WEB"],
                   "token": os.environ.get("PULL_TOKEN", ""), "user": os.environ.get("PULL_USER", ""),
                   "bundle": base64.b64encode(bundle).decode()}
        stdin = json.dumps(payload).encode() + b"\n"
    with tempfile.TemporaryDirectory() as t:
        key = pathlib.Path(t, "key")
        key.write_text(os.environ["SSH_KEY"].strip() + "\n")
        key.chmod(0o600)
        hosts = pathlib.Path(t, "known_hosts")
        hosts.write_text(f"{os.environ['HOST']} {os.environ['HOST_KEY'].strip()}\n")
        cmd = ["ssh", "-i", str(key), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
               "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={hosts}",
               "-o", "ConnectTimeout=20", "-o", "ServerAliveInterval=30",
               f"napdeploy@{os.environ['HOST']}", " ".join(op + argv[1:2]) if op[0] == "logs" else op[0]]
        r = subprocess.run(cmd, input=stdin, capture_output=True, timeout=1800)
    out = r.stdout.decode(errors="replace")
    print(out)
    if r.stderr:
        print(r.stderr.decode(errors="replace")[-2000:], file=sys.stderr)
    if op[0] == "logs":
        summary(f"## logs {argv[1:2]}\n\n```\n{out[-60000:]}\n```")
    else:
        summary(f"## store {op[0]}\n\n```json\n{out[-60000:]}\n```")
    try:
        status = json.loads(out).get("status")
    except ValueError:
        status = None
    if op[0] == "deploy" and status not in ("deployed", "already_current"):
        return 1
    return r.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
