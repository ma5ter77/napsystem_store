#!/usr/bin/env bash
# NAP-085: return the store to the previous release (code only; data in Docker volumes stays).
# A database migration made by the new release is NOT reverted automatically: restore the backup taken before the
# deploy if the old code cannot work with the new schema (docs/deployment-store.md, "Откат").
set -euo pipefail
ROOT=/srv/nap-store
PREV=$(cat "$ROOT/previous" 2>/dev/null || true)
[ -n "$PREV" ] && [ -d "$PREV" ] || { echo "No previous release recorded"; exit 1; }
cd "$PREV"
STORE_SHARED="$ROOT/shared" docker compose -p nap-store -f docker-compose.yml -f ops/cohost/docker-compose.cohost.yml up -d --build
CUR=$(readlink "$ROOT/current"); ln -sfn "$PREV" "$ROOT/current"; echo "$CUR" > "$ROOT/previous"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) rollback to $PREV (was $CUR)" >> "$ROOT/deployments.log"
echo "Rolled back to $PREV"
