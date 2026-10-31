#!/usr/bin/env bash
# NAP-085: deploy one exact commit of the store (repository BrothersB/nap-store) to /srv/nap-store
# next to the existing platform.  INACTIVE EXAMPLE — not applied anywhere yet; the first server install,
# DNS and TLS need a separate permission of the owner (handoff 31.10.2026, section 7).
#   bash deploy-cohost.sh <release.tar.gz|release-dir> <commit-sha>
# The archive is made from the agreed, checked commit:  git archive --format=tar.gz -o store-<sha>.tar.gz <sha>
# Run as a dedicated deploy user (e.g. "napstore", member of the docker group), not as the shared root.
# Layout:  /srv/nap-store/releases/<sha>/   code of that commit (read-only use)
#          /srv/nap-store/current -> releases/<sha>
#          /srv/nap-store/shared/.env       secrets, written by the owner (never in git or chat)
#          /srv/nap-store/shared/backups/   daily verified backups
# Data lives in Docker volumes of the compose project "nap-store" and survives releases and rollbacks.
# Does NOT touch nginx, the main site, its database or its files. Rollback: bash rollback-cohost.sh
set -euo pipefail
SRC="${1:?release archive or directory}"; SHA="${2:?commit sha}"
ROOT=/srv/nap-store; REL="$ROOT/releases/$SHA"; SHARED="$ROOT/shared"
PORT=$(grep -E '^STORE_WEB_PORT=' "$SHARED/.env" 2>/dev/null | cut -d= -f2 || true); PORT=${PORT:-3100}

command -v docker >/dev/null || { echo "Docker is not installed — install it first (see docs/deployment-store.md)"; exit 1; }
docker compose version >/dev/null || { echo "docker compose plugin missing"; exit 1; }
[ -f "$SHARED/.env" ] || { echo "Create $SHARED/.env first (template: .env.example + ops/cohost/env.cohost.example)"; exit 1; }
grep -q 'change-me' "$SHARED/.env" && { echo "$SHARED/.env still has change-me values"; exit 1; }
if ss -ltn "( sport = :$PORT )" | grep -q LISTEN && ! docker compose -p nap-store ps --format '{{.Name}}' 2>/dev/null | grep -q web; then
  echo "Port 127.0.0.1:$PORT is busy — set another STORE_WEB_PORT in $SHARED/.env and in the nginx site"; exit 1
fi
avail_mb=$(df -Pm "$ROOT" 2>/dev/null | awk 'NR==2{print $4}' || df -Pm / | awk 'NR==2{print $4}')
[ "${avail_mb:-0}" -gt 8000 ] || echo "WARNING: less than 8 GB free disk"

mkdir -p "$ROOT/releases" "$SHARED/backups"
if [ ! -d "$REL" ]; then
  mkdir -p "$REL"
  if [ -d "$SRC" ]; then cp -a "$SRC/." "$REL/"; else tar -xzf "$SRC" -C "$REL"; fi
fi
APP="$REL"
[ -f "$APP/docker-compose.yml" ] || { echo "docker-compose.yml not found in $REL"; exit 1; }
echo "$SHA" > "$APP/.release-sha"
ln -sfn "$SHARED/.env" "$APP/.env"
PREV=$(readlink "$ROOT/current" 2>/dev/null || true)
cd "$APP"
export STORE_SHARED="$SHARED"
docker compose -p nap-store -f docker-compose.yml -f ops/cohost/docker-compose.cohost.yml config -q
NAP_BUILD="$SHA" docker compose -p nap-store -f docker-compose.yml -f ops/cohost/docker-compose.cohost.yml up -d --build
for i in $(seq 1 60); do curl -fsS "http://127.0.0.1:$PORT/ru" >/dev/null 2>&1 && break; sleep 5; done
curl -fsS "http://127.0.0.1:$PORT/api/v1/ready" && echo
ln -sfn "$APP" "$ROOT/current"
[ -n "$PREV" ] && echo "$PREV" > "$ROOT/previous"
cat >> "$ROOT/deployments.log" <<LOG
$(date -u +%Y-%m-%dT%H:%M:%SZ) deployed $SHA from $SRC to $APP (previous: ${PREV:-none}) port $PORT
LOG
echo "OK: $SHA is live on 127.0.0.1:$PORT — check https://store.napsystem.com"
