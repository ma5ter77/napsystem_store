#!/usr/bin/env bash
# NAP-088: development stand without Docker (Claude's workspace or any Linux box with PostgreSQL 16 and Redis).
#   bash ops/dev/stand.sh up      # services, migrations, demo data, build, start api/worker/web
#   bash ops/dev/stand.sh restart # restart app processes only (after code changes: add --build)
#   bash ops/dev/stand.sh status | down | logs [api|worker|web]
# Demo data and a local-only secret; never used for production. Logs: $NAP_STAND_DIR (default /tmp/nap-stand).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LOGS="${NAP_STAND_DIR:-/tmp/nap-stand}"; mkdir -p "$LOGS"
export NAP_ENV=demo
export NAP_DATABASE_URL="${NAP_DATABASE_URL:-postgresql+psycopg://nap:nap@localhost:5432/nap}"
export NAP_REDIS_URL="${NAP_REDIS_URL:-redis://localhost:6379/0}"
export NAP_STORAGE_ROOT="$ROOT/backend/var/storage" NAP_JOB_WORKDIR="$ROOT/backend/var/jobs"
export NAP_SECRET_KEY="${NAP_SECRET_KEY:-local-dev-secret-not-for-production}"
NAP_BUILD="$(git -C "$ROOT" rev-parse HEAD)"; export NAP_BUILD
export NAP_EMAIL_BACKEND=file NAP_EMAIL_FILE_DIR="$ROOT/backend/var/mail"

services() {
  pg_isready -h localhost -q || sudo -n service postgresql start >/dev/null 2>&1 || service postgresql start >/dev/null
  redis-cli ping >/dev/null 2>&1 || sudo -n service redis-server start >/dev/null 2>&1 || service redis-server start >/dev/null
  for _ in $(seq 1 20); do pg_isready -h localhost -q && return; sleep 1; done
  echo "PostgreSQL is not running"; exit 1
}
stop() { for p in "uvicorn app.main" "celery -A app.worker" "next-server" "next start"; do pkill -f "$p" 2>/dev/null || true; done; sleep 1; }
build() {
  (cd "$ROOT/backend" && uv sync -q)
  (cd "$ROOT/frontend" && { [ -d node_modules ] || npm ci --silent; } && npm run build >"$LOGS/build.log" 2>&1) || { tail -30 "$LOGS/build.log"; exit 1; }
}
start() {
  cd "$ROOT/backend"
  nohup uv run uvicorn app.main:app --port 8000 >"$LOGS/api.log" 2>&1 &
  nohup uv run celery -A app.worker worker -Q media,ops -B -c 2 >"$LOGS/worker.log" 2>&1 &
  cd "$ROOT/frontend"; nohup npx next start -p 3000 >"$LOGS/web.log" 2>&1 &
  for _ in $(seq 1 60); do
    curl -sf localhost:3000/ru >/dev/null && curl -sf localhost:8000/api/v1/ready >/dev/null && { status; return; }
    sleep 1
  done
  echo "not ready, see $LOGS"; exit 1
}
status() {
  curl -s localhost:8000/api/v1/version | python3 -c "import json,sys; d=json.load(sys.stdin); print('api', d['version'], d['build'][:12])" 2>/dev/null || echo "api down"
  echo "web /ru $(curl -s -o /dev/null -w '%{http_code}' localhost:3000/ru)"
}
case "${1:-up}" in
  up) services; (cd "$ROOT/backend" && uv sync -q && uv run alembic upgrade head && uv run python -m app.seed); build; stop; start ;;
  restart) services; [ "${2:-}" = --build ] && build; stop; start ;;
  status) status ;;
  down) stop; echo stopped ;;
  logs) tail -n 80 "$LOGS/${2:-api}.log" ;;
  *) sed -n 2,6p "$0"; exit 1 ;;
esac
