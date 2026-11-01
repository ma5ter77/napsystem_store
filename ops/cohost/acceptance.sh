#!/usr/bin/env bash
# Disposable-stand acceptance of ops/cohost/release_control.py (docs/release-safety.md, "Оставшиеся проверки", п. 2–3).
# Runs ONLY on a throwaway Linux machine with Docker (CI runner). Never on the platform server.
# Real Docker: bootstrap of a closed demo, upgrade, failed upgrade with automatic restore, rollback, and refusals:
# busy port, plan-only, parallel operator (lock), env drift, file drift, kill -9 during preparation.
# Synthetic demo data only. Secrets are generated here and never printed.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
STAND="${STAND:-$HOME/nap-stand}"
ROOT="$STAND/srv"
CTL="python3 ops/cohost/release_control.py"
PORT=3100
RESULTS=()
pass() { RESULTS+=("✅ $*"); printf '\n\033[32mPASS\033[0m %s\n' "$*"; }
fail() { RESULTS+=("❌ $*"); printf '\n\033[31mFAIL\033[0m %s\n' "$*"; summary; exit 1; }
summary() {
  { echo "## Release-safety acceptance (disposable Docker stand)"; printf '%s\n' "${RESULTS[@]}"; } | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
}
# expect_stop "<substring>" cmd...  — command must fail with STOP containing the substring
expect_stop() {
  local want="$1"; shift; local out
  if out=$("$@" 2>&1); then echo "$out"; return 1; fi
  echo "$out" | tail -3
  grep -q "STOP: .*$want" <<<"$out"
}
psql_q() { docker exec "$(docker ps -qf label=com.docker.compose.project=nap-store -f label=com.docker.compose.service=postgres)" \
  psql -U nap -d nap -tAc "$1"; }
api_exec() { docker exec "$(docker ps -qf label=com.docker.compose.project=nap-store -f label=com.docker.compose.service=api)" sh -c "$1"; }
http_ok() { curl -fsS --noproxy '*' -o /dev/null "http://127.0.0.1:$PORT$1"; }
build_of() { curl -fsS --noproxy '*' "http://127.0.0.1:$PORT/api/v1/version" | python3 -c 'import json,sys; print(json.load(sys.stdin)["build"])'; }
current() { python3 -c 'import json,sys; s=json.load(open(sys.argv[1])); print((s["current"] or {}).get("sha","none"), s["status"], (s.get("previous") or {}).get("sha","none"))' "$ROOT/release-state.json"; }

archive() {  # archive <sha> -> prints "path sha256"
  local out="$STAND/archives/store-$1.tar.gz"
  [ -f "$out" ] || git archive --format=tar.gz --output="$out" "$1"
  echo "$out $(sha256sum "$out" | cut -d' ' -f1)"
}
deploy() {  # deploy <root> <expected-current> <sha> [extra flags...]
  local root="$1" exp="$2" sha="$3"; shift 3
  read -r a h < <(archive "$sha")
  $CTL deploy --root "$root" --expected-current "$exp" --env-sha256 "$ENV_SHA" --sha "$sha" --archive "$a" --archive-sha256 "$h" "$@"
}
provision() {  # provision <root>
  mkdir -p "$1"/{releases,shared/backups,control}
  install -m 600 "$STAND/env" "$1/shared/.env"
}

# ---------------------------------------------------------------- stand
mkdir -p "$STAND/archives"
STAND="$(cd "$STAND" && pwd -P)"; ROOT="$STAND/srv"
[ "$(id -u)" != 0 ] || { echo "run as a non-root user in the docker group"; exit 2; }
umask 077
{
  echo "NAP_ENV=demo"
  echo "NAP_SECRET_KEY=$(openssl rand -hex 32)"
  echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)"
  echo "NAP_PUBLIC_BASE_URL=https://store.napsystem.com"
  echo "NAP_STORAGE_BACKEND=local"
  echo "STORE_WEB_PORT=$PORT"
} > "$STAND/env"
umask 022
ENV_SHA=$(sha256sum "$STAND/env" | cut -d' ' -f1)
provision "$ROOT"
for img in postgres:16-alpine redis:7-alpine postgres:16-bookworm; do docker pull -q "$img" >/dev/null; done

git config user.name "acceptance"; git config user.email "acceptance@invalid"
A=$(git rev-parse HEAD)
git checkout -q -b acceptance-b
echo "<!-- acceptance B: docs-only change, same schema/topology -->" >> docs/release-safety.md
git commit -qam "acceptance: release B (docs only)"; B=$(git rev-parse HEAD)
git checkout -q -b acceptance-c
printf '\nraise SystemExit("acceptance: deliberately broken release C")\n' >> backend/app/main.py
git commit -qam "acceptance: release C (api cannot start)"; C=$(git rev-parse HEAD)
git checkout -q acceptance-b
git checkout -q -b acceptance-d
echo "<!-- acceptance D -->" >> docs/release-safety.md
git commit -qam "acceptance: release D (interrupted)"; D=$(git rev-parse HEAD)
git checkout -q "$A"
echo "A=$A B=$B C=$C D=$D"

# ---------------------------------------------------------------- refusals before bootstrap
python3 -m http.server "$PORT" --bind 127.0.0.1 >/dev/null 2>&1 & BUSY=$!; sleep 1
provision "$STAND/port-root"
if expect_stop "local operation failed" deploy "$STAND/port-root" none "$A" --apply --closed-demo --bootstrap-demo; then
  [ -d "$STAND/port-root/.release-lock" ] && pass "Занятый порт: первичная установка остановлена до сборки, lock сохранён для оператора" \
    || fail "Занятый порт: lock не сохранён"
else fail "Занятый порт: установка не остановилась"; fi
kill "$BUSY"; wait "$BUSY" 2>/dev/null || true
[ -z "$(docker ps -aq --filter label=com.docker.compose.project=nap-store)" ] || fail "Занятый порт: появились контейнеры"

out=$(deploy "$ROOT" none "$A")
grep -q "PLAN ONLY" <<<"$out" && [ -z "$(docker ps -aq --filter label=com.docker.compose.project=nap-store)" ] && [ ! -e "$ROOT/releases/$A" ] \
  && pass "Plan-only: входы проверены, ничего не распаковано и не запущено" || fail "Plan-only изменил систему"

# ---------------------------------------------------------------- bootstrap A
deploy "$ROOT" none "$A" --apply --closed-demo --bootstrap-demo || fail "Первичная установка A"
read -r cur st prev < <(current)
[ "$cur" = "$A" ] && [ "$st" = idle ] && [ "$(build_of)" = "$A" ] && http_ok /ru && http_ok /en && http_ok /api/v1/ready \
  && [ "$(readlink "$ROOT/current")" = "$ROOT/releases/$A" ] && [ ! -e "$ROOT/.release-lock" ] \
  && pass "Первичная установка закрытого demo A: /ru, /en, /ready, /version.build=A, current→A, lock снят" || fail "Bootstrap A: проверки"
[ -n "$(docker ps -q --filter label=com.docker.compose.service=backup)" ] && pass "Резервное копирование запущено после bootstrap" || fail "backup не запущен"
USERS=$(psql_q "select count(*) from users"); APPS=$(psql_q "select count(*) from apps")
psql_q "create table acceptance_marker(v text); insert into acceptance_marker values ('kept')" >/dev/null
api_exec 'echo media-kept > /app/var/storage/acceptance-media.txt'
echo "users=$USERS apps=$APPS"

# ---------------------------------------------------------------- upgrade A -> B
deploy "$ROOT" "$A" "$B" --apply --closed-demo || fail "Обновление A→B"
read -r cur st prev < <(current)
[ "$cur" = "$B" ] && [ "$prev" = "$A" ] && [ "$(build_of)" = "$B" ] && http_ok /ru || fail "Обновление A→B: состояние"
[ "$(psql_q "select count(*) from users")" = "$USERS" ] && [ "$(psql_q "select count(*) from apps")" = "$APPS" ] \
  && [ "$(psql_q "select v from acceptance_marker")" = kept ] && [ "$(api_exec 'cat /app/var/storage/acceptance-media.txt')" = media-kept ] \
  && pass "Обновление A→B: build=B, previous=A; SQL и медиа сохранены, повторного seed нет (users=$USERS, apps=$APPS)" \
  || fail "Обновление A→B: данные изменились"

# ---------------------------------------------------------------- failed upgrade B -> C, automatic restore
if expect_stop "prior images/config restored" deploy "$ROOT" "$B" "$C" --apply --closed-demo; then
  read -r cur st prev < <(current)
  [ "$cur" = "$B" ] && [ "$st" = idle ] && [ "$(build_of)" = "$B" ] && http_ok /ru && http_ok /api/v1/ready && [ ! -e "$ROOT/.release-lock" ] \
    && grep -q "\"last_failure\": \"$C\"" "$ROOT/release-state.json" \
    && [ "$(psql_q "select v from acceptance_marker")" = kept ] \
    && pass "Сломанный выпуск C: отказ /ready → восстановлены образы B, сайт работает, last_failure=C, данные на месте" \
    || fail "Сломанный выпуск C: восстановление неполное"
else fail "Сломанный выпуск C не был остановлен"; fi

# ---------------------------------------------------------------- refusals with a running store
mkdir "$ROOT/.release-lock"
expect_stop "Another operation" deploy "$ROOT" "$B" "$D" --apply --closed-demo && pass "Второй оператор / существующий lock: операция отклонена" || fail "lock не соблюдён"
rmdir "$ROOT/.release-lock"
cp -p "$ROOT/shared/.env" "$STAND/env.bak"; echo "# drift" >> "$ROOT/shared/.env"
expect_stop "Environment hash mismatch" deploy "$ROOT" "$B" "$D" --apply --closed-demo && pass "Дрейф окружения: отклонено" || fail "дрейф env не пойман"
cp -p "$STAND/env.bak" "$ROOT/shared/.env"
echo drift > "$ROOT/releases/$B/DRIFT.txt"
expect_stop "Release files changed" deploy "$ROOT" "$B" "$D" --apply --closed-demo && pass "Дрейф файлов текущего выпуска: отклонено" || fail "дрейф файлов не пойман"
rm "$ROOT/releases/$B/DRIFT.txt"
expect_stop "Current release differs" deploy "$ROOT" "$A" "$D" --apply --closed-demo && pass "Неверный expected-current: отклонено" || fail "expected-current не проверен"

# ---------------------------------------------------------------- kill -9 during preparation
( deploy "$ROOT" "$B" "$D" --apply --closed-demo >/dev/null 2>&1 ) & DPID=$!
for _ in $(seq 1 600); do [ -d "$ROOT/releases/$D" ] && break; sleep 0.2; done   # preparation has begun
pkill -9 -f "release_control.py deploy" || true; wait "$DPID" 2>/dev/null || true
read -r cur st prev < <(current)
[ -d "$ROOT/.release-lock" ] && [ "$cur" = "$B" ] && [ "$st" = idle ] && [ "$(build_of)" = "$B" ] && http_ok /ru \
  && expect_stop "Another operation or interrupted lock" deploy "$ROOT" "$B" "$D" --apply --closed-demo \
  && pass "kill -9 во время сборки: lock сохранён, текущий B работает, повтор вслепую отклонён" || fail "kill -9: неверное поведение"
rm -rf "$ROOT/.release-lock" "$ROOT/releases/$D" "$ROOT/control/$D"   # operator reconciliation on the stand

# ---------------------------------------------------------------- rollback B -> A
rb() { $CTL rollback --root "$ROOT" --expected-current "$1" --env-sha256 "$ENV_SHA" "${@:2}"; }
grep -q "PLAN ONLY" <<<"$(rb "$B")" || fail "Откат: plan-only"
rb "$B" --apply --closed-demo || fail "Откат B→A"
read -r cur st prev < <(current)
[ "$cur" = "$A" ] && [ "$prev" = "$B" ] && [ "$(build_of)" = "$A" ] && http_ok /en && http_ok /api/v1/ready \
  && [ "$(psql_q "select v from acceptance_marker")" = kept ] && [ "$(psql_q "select count(*) from users")" = "$USERS" ] \
  && pass "Откат B→A без пересборки и миграций: build=A, данные сохранены" || fail "Откат B→A: проверки"

summary
