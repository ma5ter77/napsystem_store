#!/usr/bin/env bash
# NAP-006: daily backup with restore verification and retention. Runs as the `backup` service in docker compose.
#   PG_URL              database to back up                 (postgresql://nap:nap@postgres:5432/nap)
#   PG_ADMIN_URL        maintenance DB for the restore test  (…/postgres)
#   BACKUP_DIR          where dumps go                      (/backups)
#   BACKUP_AT           daily time, UTC, HH:MM              (02:30)
#   BACKUP_KEEP_DAYS    delete dumps older than this        (14)
#   BACKUP_STORAGE_DIR  if set, the file storage is archived too (tar)
#   BACKUP_STORAGE_KEEP_DAYS  retention of the file archives (2): each one is a full copy of every stored file, so
#                       14 of them would fill the disk long before the database dumps do
#   BACKUP_MIN_FREE_PCT keep at least this share of the backup disk free (10): an archive that would go below it is
#                       skipped and the cycle reports "failed", instead of filling the disk the database lives on
#   BACKUP_S3_DATA_DIR  if set and not empty, the bundled S3 server's data directory is archived too (NAP-004)
#   BACKUP_ONCE=1       run one cycle and exit (manual run, tests)
# Every cycle writes $BACKUP_DIR/last-status.json, read by the API for /metrics (nap_backup_age_seconds).
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PG_URL="${PG_URL:-postgresql://nap:nap@postgres:5432/nap}"
PG_ADMIN_URL="${PG_ADMIN_URL:-${PG_URL%/*}/postgres}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_AT="${BACKUP_AT:-02:30}"
KEEP="${BACKUP_KEEP_DAYS:-14}"
STORAGE_KEEP="${BACKUP_STORAGE_KEEP_DAYS:-2}"
MIN_FREE_PCT="${BACKUP_MIN_FREE_PCT:-10}"
room_for() {  # bytes needed -> 0 if the backup disk keeps MIN_FREE_PCT free after writing them
  local need=$1 total avail
  read -r total avail < <(df -P -B1 "$BACKUP_DIR" | awk 'NR==2{print $2, $4}')
  [ $((avail - need)) -ge $((total * MIN_FREE_PCT / 100)) ]
}
mkdir -p "$BACKUP_DIR"

status() {  # ok|failed, file, message
  printf '{"finished_at":"%s","status":"%s","file":"%s","size_bytes":%s,"message":"%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$(basename "${2:-}")" "$( [ -n "${2:-}" ] && [ -f "$2" ] && stat -c %s "$2" || echo 0)" \
    "$(echo "$3" | tr -d '"\\' | tr '\n' ' ' | cut -c1-300)" > "$BACKUP_DIR/last-status.json.tmp"
  mv "$BACKUP_DIR/last-status.json.tmp" "$BACKUP_DIR/last-status.json"
}

cycle() {
  local f out rc
  if ! f="$("$HERE/backup.sh" "$PG_URL" "$BACKUP_DIR" 2>&1 | tail -1)" || [ ! -f "$f" ]; then
    echo "backup failed: $f"; status failed "" "pg_dump failed: $f"; return 1
  fi
  if ! out="$("$HERE/restore_verify.sh" "$f" "$PG_URL" "$PG_ADMIN_URL" 2>&1)"; then
    echo "$out"; status failed "$f" "restore check failed: $(echo "$out" | tail -2)"; return 1
  fi
  echo "$out" | tail -1
  # retention first (it must run even when the file archive below is skipped); file archives make room first
  find "$BACKUP_DIR" -maxdepth 1 -type f -name 'nap-*.dump*' -mtime +"$KEEP" -print -delete
  find "$BACKUP_DIR" -maxdepth 1 -type f -name 'storage-*.tar*' -mtime +"$((STORAGE_KEEP - 1))" -print -delete
  if [ -n "${BACKUP_STORAGE_DIR:-}" ] && [ -d "$BACKUP_STORAGE_DIR" ]; then
    local t="$BACKUP_DIR/storage-$(date -u +%Y%m%dT%H%M%SZ).tar" need
    need=$(du -sb "$BACKUP_STORAGE_DIR" | cut -f1)
    if ! room_for "$need"; then
      status failed "$f" "database backed up; file archive skipped: $((need / 1048576)) MiB would leave less than ${MIN_FREE_PCT}% of the disk free"
      return 1
    fi
    tar -C "$BACKUP_STORAGE_DIR" -cf "$t" . && sha256sum "$t" > "$t.sha256" || { status failed "$f" "storage archive failed"; return 1; }
  fi
  if [ -n "${BACKUP_S3_DATA_DIR:-}" ] && [ -d "$BACKUP_S3_DATA_DIR" ] && [ -n "$(ls -A "$BACKUP_S3_DATA_DIR" 2>/dev/null)" ]; then
    local t3="$BACKUP_DIR/storage-s3-$(date -u +%Y%m%dT%H%M%SZ).tar"
    # tar exit 1 = "file changed as we read it" (the server is live): the archive is still usable
    tar -C "$BACKUP_S3_DATA_DIR" -cf "$t3" . ; rc=$?
    [ $rc -le 1 ] && sha256sum "$t3" > "$t3.sha256" || { status failed "$f" "s3 data archive failed"; return 1; }
  fi
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
