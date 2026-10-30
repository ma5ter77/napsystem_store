#!/usr/bin/env bash
# Logical PostgreSQL backup (custom format). Usage: ops/backup.sh [DATABASE_URL] [OUT_DIR]
set -euo pipefail
URL="${1:-${PG_URL:-postgresql://nap:nap@localhost:5432/nap}}"
OUT="${2:-./backups}"
mkdir -p "$OUT"
F="$OUT/nap-$(date -u +%Y%m%dT%H%M%SZ).dump"
pg_dump --format=custom --no-owner --dbname="$URL" --file="$F"
sha256sum "$F" > "$F.sha256"
echo "$F"
