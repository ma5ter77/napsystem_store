#!/usr/bin/env bash
# NAP-006: daily backup with restore verification and retention. Runs as the `backup` service in docker compose.
#   PG_URL              database to back up                 (postgresql://nap:nap@postgres:5432/nap)
#   PG_ADMIN_URL        maintenance DB for the restore test  (…/postgres)
#   BACKUP_DIR          where dumps go                      (/backups)
#   BACKUP_AT           daily time, UTC, HH:MM              (02:30)
#   BACKUP_KEEP_DAYS    delete dumps older than this        (14)
#   BACKUP_STORAGE_DIR  if set, the file storage is archived too (tar, same retention)
#   BACKUP_ONCE=1       run one cycle and exit (manual run, tests)
# Every cycle writes $BACKUP_DIR/last-status.json, read by the API for /metrics (nap_backup_age_seconds).
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PG_URL="${PG_URL:-postgresql://nap:nap@postgres:5432/nap}"
PG_ADMIN_URL="${PG_ADMIN_URL:-${PG_URL%/*}/postgres}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_AT="${BACKUP_AT:-02:30}"
KEEP="${BACKUP_KEEP_DAYS:-14}"
mkdir -p "$BACKUP_DIR"

status() {  # ok|failed, file, message
  printf '{"finished_at":"%s","status":"%s","file":"%s","size_bytes":%s,"message":"%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$(basename "${2:-}")" "$( [ -n "${2:-}" ] && [ -f "$2" ] && stat -c %s "$2" || echo 0)" \
    "$(echo "$3" | tr -d '"\\' | tr '\n' ' ' | cut -c1-300)" > "$BACKUP_DIR/last-status.json.tmp"
  mv "$BACKUP_DIR/last-status.json.tmp" "$BACKUP_DIR/last-status.json"
}

cycle() {
  local f out
  if ! f="$("$HERE/backup.sh" "$PG_URL" "$BACKUP_DIR" 2>&1 | tail -1)" || [ ! -f "$f" ]; then
    echo "backup failed: $f"; status failed "" "pg_dump failed: $f"; return 1
  fi
  if ! out="$("$HERE/restore_verify.sh" "$f" "$PG_URL" "$PG_ADMIN_URL" 2>&1)"; then
    echo "$out"; status failed "$f" "restore check failed: $(echo "$out" | tail -2)"; return 1
  fi
  echo "$out" | tail -1
  if [ -n "${BACKUP_STORAGE_DIR:-}" ] && [ -d "$BACKUP_STORAGE_DIR" ]; then
    local t="$BACKUP_DIR/storage-$(date -u +%Y%m%dT%H%M%SZ).tar"
    tar -C "$BACKUP_STORAGE_DIR" -cf "$t" . && sha256sum "$t" > "$t.sha256" || { status failed "$f" "storage archive failed"; return 1; }
  fi
  find "$BACKUP_DIR" -maxdepth 1 -type f \( -name 'nap-*.dump*' -o -name 'storage-*.tar*' \) -mtime +"$KEEP" -print -delete
  status ok "$f" "$(echo "$out" | tail -1)"
}

if [ "${BACKUP_ONCE:-0}" = "1" ]; then cycle; exit $?; fi
while true; do
  now=$(date -u +%s); next=$(date -u -d "today $BACKUP_AT" +%s)
  [ "$next" -le "$now" ] && next=$(date -u -d "tomorrow $BACKUP_AT" +%s)
  echo "next backup at $(date -u -d "@$next" +%Y-%m-%dT%H:%M:%SZ)"
  sleep $((next - now))
  cycle || echo "cycle failed, see last-status.json"
done
