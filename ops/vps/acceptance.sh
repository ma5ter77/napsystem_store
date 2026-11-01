#!/usr/bin/env bash
# NAP-087: real Docker acceptance of the dedicated-server release runner on a throwaway machine
# (GitHub runner). A local registry stands in for GHCR, Caddy serves "localhost" with its internal CA.
# Scenarios: first release, idempotent repeat, upgrade with pre-deploy dump, broken release -> automatic
# return to the previous one, manual rollback, restart, backup, logs, status, refused tampered bundles.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
W="${RUNNER_TEMP:-$(mktemp -d)}/vps-acc"
ROOT="$W/srv"
REG=localhost:5000
rm -rf "$W"; mkdir -p "$ROOT"/{releases,state,backups,shared}
chmod 700 "$ROOT/shared"
cat > "$ROOT/shared/.env" <<EOF
NAP_ENV=demo
NAP_SEED_DEMO=1
STORE_DOMAIN=localhost
POSTGRES_PASSWORD=$(openssl rand -hex 24)
NAP_SECRET_KEY=$(openssl rand -hex 32)
EOF
chmod 600 "$ROOT/shared/.env"
export NAPDEPLOY_CONFIG="$W/config.json"
echo "{\"root\": \"$ROOT\", \"registry\": \"$REG\", \"image_prefix\": \"$REG/nap-store-\", \"keep\": 3, \"ready_seconds\": 300}" > "$NAPDEPLOY_CONFIG"
ND="python3 ops/vps/napdeploy.py"
pass=0
ok() { pass=$((pass+1)); echo "PASS $pass: $*"; }
fail() { echo "FAIL: $*"; exit 1; }
field() { python3 -c "import json,sys; print(json.load(sys.stdin).get('$1'))"; }

echo "== registry and images"
docker run -d --rm -p 5000:5000 --name nap-acc-registry registry:2 >/dev/null
digest() { docker inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$1" | grep "^$REG/" | head -1 | cut -d@ -f2; }
docker build -q -t $REG/nap-store-backend:good backend >/dev/null
docker build -q --build-arg BACKEND_URL=http://api:8000 -t $REG/nap-store-web:good frontend >/dev/null
cp -r backend "$W/broken"; echo 'raise SystemExit("deliberately broken release")' >> "$W/broken/app/main.py"
docker build -q -t $REG/nap-store-backend:broken "$W/broken" >/dev/null
for t in backend:good web:good backend:broken; do docker push -q $REG/nap-store-$t >/dev/null; done
GOOD=$(digest $REG/nap-store-backend:good); WEB=$(digest $REG/nap-store-web:good); BROKEN=$(digest $REG/nap-store-backend:broken)
[[ $GOOD == sha256:* && $WEB == sha256:* && $BROKEN == sha256:* ]] || fail "no digests"

A=$(git rev-parse HEAD); B=$(printf 'b%.0s' {1..40}); C=$(printf 'c%.0s' {1..40}); T=$(printf 'd%.0s' {1..40})
git archive --format=tar.gz HEAD ops/vps ops/backup-loop.sh ops/backup.sh ops/restore_verify.sh > "$W/bundle.tgz"
payload() {  # sha backend-digest [bundle]
  python3 - "$1" "$2" "$WEB" "${3:-$W/bundle.tgz}" <<'PY'
import base64, json, sys
sha, be, web, b = sys.argv[1:]
print(json.dumps({"sha": sha, "backend": be, "web": web, "bundle": base64.b64encode(open(b, "rb").read()).decode()}))
PY
}
deploy() { payload "$@" | $ND deploy; }
build_seen() { curl -sk --max-time 5 https://localhost/api/v1/version | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('data',d)['build'])"; }

echo "== 1. first release"
out=$(deploy "$A" "$GOOD") || { echo "$out"; docker ps -a; fail "first deploy"; }
[ "$(echo "$out" | field status)" = deployed ] || fail "first: $out"
for _ in $(seq 1 30); do [ "$(build_seen 2>/dev/null)" = "$A" ] && break; sleep 2; done
[ "$(build_seen)" = "$A" ] || fail "https via caddy does not serve A"
curl -skf --max-time 10 https://localhost/ru >/dev/null || fail "/ru via caddy"
ok "first release served over HTTPS by Caddy"

echo "== 2. repeat is a no-op"
[ "$(deploy "$A" "$GOOD" | field status)" = already_current ] || fail "repeat"
ok "repeat deploy is idempotent"

echo "== 3. upgrade"
out=$(deploy "$B" "$GOOD") || { echo "$out"; fail "upgrade"; }
[ "$(echo "$out" | field previous)" = "$A" ] || fail "previous: $out"
compgen -G "$ROOT/backups/pre-deploy-*.dump" >/dev/null || fail "no pre-deploy dump"
[ "$(build_seen)" = "$B" ] || fail "B not served"
ok "upgrade with pre-deploy database dump"

echo "== 4. broken release returns automatically"
set +e; out=$(deploy "$C" "$BROKEN"); rc=$?; set -e
[ $rc -ne 0 ] && [ "$(echo "$out" | field status)" = failed_rolled_back ] || fail "broken: rc=$rc $out"
[ "$(build_seen)" = "$B" ] || fail "B not restored"
[ "$($ND status | field current)" = "$B" ] || fail "state after failure"
ok "broken release refused, previous release restored and serving"

echo "== 5. manual rollback"
[ "$($ND rollback | field current)" = "$A" ] || fail "rollback"
[ "$(build_seen)" = "$A" ] || fail "A not served after rollback"
ok "rollback to previous release"

echo "== 6. restart, backup, logs, status"
[ "$($ND restart | field status)" = ok ] || fail "restart"
[ "$($ND backup | field status)" = ok ] || fail "backup"
$ND logs api | grep -q . || fail "logs"
set +e; $ND logs evil >/dev/null; rc=$?; set -e; [ $rc -eq 2 ] || fail "logs of unknown service allowed"
$ND status | python3 -c "import json,sys; s=json.load(sys.stdin)['services']; assert 'running' in s['api'] and 'running' in s['caddy'], s"
ok "restart, backup with restore check, logs, status"

echo "== 7. tampered bundles are refused"
mkdir -p "$W/t"; tar -xzf "$W/bundle.tgz" -C "$W/t"
python3 - "$W/t/ops/vps/docker-compose.prod.yml" <<'PY'
import sys; p=sys.argv[1]; s=open(p).read()
s=s.replace("  api:\n    image: ${IMAGE_BACKEND}\n", "  api:\n    image: ${IMAGE_BACKEND}\n    privileged: true\n", 1)
open(p,"w").write(s)
PY
grep -q "privileged: true" "$W/t/ops/vps/docker-compose.prod.yml" || fail "tamper prep"
tar -czf "$W/evil.tgz" -C "$W/t" ops
set +e; out=$(deploy "$T" "$GOOD" "$W/evil.tgz"); rc=$?; set -e
[ $rc -eq 2 ] && echo "$out" | grep -q privileges || fail "privileged compose accepted: $out"
git archive --format=tar.gz HEAD ops/vps ops/backup-loop.sh ops/backup.sh ops/restore_verify.sh README.md > "$W/extra.tgz"
set +e; out=$(deploy "$T" "$GOOD" "$W/extra.tgz"); rc=$?; set -e
[ $rc -eq 2 ] && echo "$out" | grep -q "unexpected file" || fail "extra file accepted: $out"
[ "$(build_seen)" = "$A" ] || fail "refusal changed the live release"
ok "privileged compose and foreign files refused, live release untouched"

echo "== 8. gate"
set +e; SSH_ORIGINAL_COMMAND="docker ps" $ND gate >/dev/null; rc=$?; set -e
[ $rc -eq 2 ] || fail "gate allowed docker ps"
[ "$(SSH_ORIGINAL_COMMAND=status $ND gate | field current)" = "$A" ] || fail "gate status"
ok "forced-command gate"

echo "ALL $pass SCENARIOS PASSED"
