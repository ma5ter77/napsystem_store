#!/usr/bin/env bash
# Restores a dump into a scratch database and compares row counts + ledger balance with the source.
# Usage: ops/restore_verify.sh DUMP SOURCE_URL ADMIN_URL
set -euo pipefail
DUMP="$1"; SRC="$2"; ADMIN="$3"; DB="nap_restore_check"
sha256sum -c "$DUMP.sha256"
psql "$ADMIN" -qc "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB"
TGT="${ADMIN%/*}/$DB"
pg_restore --no-owner --dbname="$TGT" "$DUMP"
Q="SELECT string_agg(t || '=' || n, ',' ORDER BY t) FROM (
  SELECT 'users' t, count(*) n FROM users UNION ALL SELECT 'apps', count(*) FROM apps
  UNION ALL SELECT 'app_versions', count(*) FROM app_versions UNION ALL SELECT 'conversion_jobs', count(*) FROM conversion_jobs
  UNION ALL SELECT 'assets', count(*) FROM assets UNION ALL SELECT 'digital_orders', count(*) FROM digital_orders
  UNION ALL SELECT 'hardware_orders', count(*) FROM hardware_orders UNION ALL SELECT 'parcels', count(*) FROM parcels
  UNION ALL SELECT 'ledger_entries', count(*) FROM ledger_entries UNION ALL SELECT 'ledger_sum', coalesce(sum(amount_minor),0) FROM ledger_entries
  UNION ALL SELECT 'audit_log', count(*) FROM audit_log UNION ALL SELECT 'inventory_on_hand', sum(on_hand) FROM inventory) x"
A=$(psql "$SRC" -tAc "$Q"); B=$(psql "$TGT" -tAc "$Q")
echo "source:   $A"; echo "restored: $B"
REV=$(psql "$TGT" -tAc "select version_num from alembic_version")
# immutability triggers survive the restore
psql "$TGT" -tAc "UPDATE ledger_entries SET amount_minor = amount_minor WHERE false" >/dev/null
if psql "$TGT" -tAc "DELETE FROM audit_log WHERE id IN (SELECT id FROM audit_log LIMIT 1)" 2>/dev/null; then TRIG=missing; else TRIG=present; fi
psql "$ADMIN" -qc "DROP DATABASE $DB"
[ "$A" = "$B" ] && [ "$TRIG" = present ] && echo "RESTORE OK (migration $REV, append-only triggers $TRIG)" || { echo "RESTORE MISMATCH"; exit 1; }
