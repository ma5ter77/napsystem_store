#!/usr/bin/env bash
# NAP Store — загрузка проверенного исходного архива на сервер (инструкция от 01.11.2026, пункты 1–4).
# Только загрузка: ничего не распаковывает, не устанавливает и не запускает; на сервере ничего не удаляет.
# Запуск на Mac:   bash ops/upload-to-store.sh   (или копия ~/Projects/_nap-store-backup-20261031/nap-store-upload.sh)
#   Другой согласованный коммит:  STORE_SHA=<полный SHA> bash ops/upload-to-store.sh   (или копия ~/Projects/_nap-store-backup-20261031/nap-store-upload.sh)
#   Параметры и порядок — docs/deploy-upload.md в репозитории магазина.
# Секреты не выводятся: приватный ключ только используется ssh, его содержимое не читается и не печатается.
set -euo pipefail

KEY="$HOME/.ssh/nap_store_ed25519"
HOST="napstore@217.114.3.160"
FP_EXPECTED="SHA256:kXp2pB86W3gjWj4+Nv0GSJD9Bh8+t0E5jDk8du4tuKw"
REPO="$HOME/Projects/nap-store-publish"          # исходный Git магазина (не nap-trainer)
TESTED_COMMIT="12ead32b4816ff989bd1ae08157df09ca779a8e0"   # 0.8.0 + безопасный выпуск (PR #20): CI зелёный, приёмка на Docker-стенде 12/12
SSH_OPTS=(-i "$KEY" -o IdentitiesOnly=yes -o HostKeyAlgorithms=ssh-ed25519 -o ForwardAgent=no -o ForwardX11=no
          -o ClearAllForwardings=yes -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no)

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
stop() { printf '\n\033[31mСТОП: %s\033[0m\n' "$*"; exit 1; }
ask()  { local a; read -r -p "$1 [введите yes]: " a; [ "$a" = "yes" ] || stop "не подтверждено"; }

# ---------------------------------------------------------------- 1. первый вход
say "1. Ключ и первый вход"
[ -f "$KEY" ] || stop "нет файла $KEY (приватный ключ должен быть на этом Mac)"
ls -l "$KEY"
chmod 600 "$KEY"
if ! ssh-keygen -F 217.114.3.160 >/dev/null 2>&1; then
  echo "Сервер ещё не в known_hosts. ssh покажет отпечаток СЕРВЕРА — сравните его посимвольно с:"
  echo "    $FP_EXPECTED"
  echo "Ответьте yes ТОЛЬКО при полном совпадении. При расхождении ответьте no — скрипт остановится."
  echo "Если ssh спросит passphrase for key — это ваша локальная фраза ключа. Пароль сервера не нужен."
fi
OUT=$(ssh "${SSH_OPTS[@]}" -o StrictHostKeyChecking=ask "$HOST" \
  'whoami; id; test -w /srv/nap-store/incoming && echo UPLOAD_DIRECTORY_OK') || stop "вход не удался (ключ/пользователь/отпечаток)"
echo "$OUT"
echo "$OUT" | head -1 | grep -qx napstore || stop "ожидался пользователь napstore"
echo "$OUT" | grep -qx UPLOAD_DIRECTORY_OK || stop "нет права записи в /srv/nap-store/incoming"
FP_KNOWN=$(ssh-keygen -F 217.114.3.160 -f "$HOME/.ssh/known_hosts" | grep -v '^#' | ssh-keygen -lf - 2>/dev/null | awk '/ED25519/{print $2}' | head -1)
[ "$FP_KNOWN" = "$FP_EXPECTED" ] || stop "отпечаток в known_hosts ($FP_KNOWN) не совпадает с ожидаемым — ничего не загружено"
echo "Отпечаток сервера совпадает: $FP_KNOWN"

# ---------------------------------------------------------------- 2. архив
say "2. Исходный Git магазина и выбор коммита"
cd "$REPO"
git rev-parse --show-toplevel
git status --short --branch | sed -n 1,5p || true
git remote -v | sed -E 's#https://[^@/]+@#https://***@#'   # токен в URL, если он есть, не показывается
git fetch -q store-origin
git log -5 --oneline store-origin/master
SOURCE="master на GitHub"
if [ -n "${STORE_SHA:-}" ]; then
  SOURCE="коммит, указанный вручную"
  STORE_SHA=$(git rev-parse --verify "${STORE_SHA}^{commit}") || stop "коммит $STORE_SHA не найден (сначала git fetch store-origin)"
  git branch -r --contains "$STORE_SHA" | grep -q 'store-origin/' || stop "коммита нет ни в одной ветке GitHub — загружаются только опубликованные коммиты"
else
  STORE_SHA=$(git rev-parse store-origin/master)
fi
echo
echo "Кандидат: BrothersB/nap-store, $SOURCE = $STORE_SHA"
git show --no-patch --format=fuller "$STORE_SHA"
VERSION=$( { git show "$STORE_SHA:release.json" 2>/dev/null || true; } | awk -F'"' '/"version"/{print $4; exit}' || true)
# Docs and this upload script do not affect what runs on the server: differences there are listed, not blocking.
NONRUNTIME=(-- . ':(exclude)docs' ':(exclude)ops/upload-to-store.sh' ':(exclude)README.md')
if git cat-file -e "$TESTED_COMMIT" 2>/dev/null && git diff --quiet "$TESTED_COMMIT" "$STORE_SHA" "${NONRUNTIME[@]}"; then
  DOCS_ONLY=$(git diff --name-only "$TESTED_COMMIT" "$STORE_SHA" | tr '\n' ' ')
  MATCH="да — рабочий код совпадает с проверенным выпуском ($TESTED_COMMIT)${DOCS_ONLY:+; отличаются только документация/скрипт выгрузки: $DOCS_ONLY}"
  echo "✓ Версия $VERSION. Файлы совпадают с проверенным выпуском ($TESTED_COMMIT): тесты, E2E RU/EN и CI зелёные."
else
  MATCH="НЕТ — содержимое отличается от проверенного выпуска ($TESTED_COMMIT)"
  echo
  echo "⚠⚠ ВНИМАНИЕ: это НЕ проверенный выпуск. Версия в коммите: ${VERSION:-неизвестна}."
  git diff --stat "$TESTED_COMMIT" "$STORE_SHA" 2>/dev/null | tail -3 || true
  echo "   Если общий запрос ещё не принят в master, остановитесь (Ctrl+C) и примите его,"
  echo "   или запустите с проверенным коммитом:  STORE_SHA=$TESTED_COMMIT bash $0"
  read -r -p "   Чтобы всё равно загрузить ЭТОТ коммит, введите первые 7 символов его SHA (${STORE_SHA:0:7}): " a
  [ "$a" = "${STORE_SHA:0:7}" ] || stop "не подтверждено"
fi
FILES=$(git ls-tree -r --name-only "$STORE_SHA")
echo "Файлов в коммите: $(echo "$FILES" | wc -l | tr -d ' ')"
BAD=$(echo "$FILES" | grep -iE '(^|/)\.env$|(^|/)\.env\.(local|prod|production)$|\.pem$|\.key$|id_rsa|id_ed25519|\.p12$|\.pfx$|\.dump$|\.sql(\.gz)?$|\.sqlite3?$' || true)
[ -z "$BAD" ] || { echo "$BAD"; stop "в коммите есть файлы, похожие на секреты/дампы"; }
if git grep -qIE 'sk_live_[0-9a-zA-Z]{8,}|whsec_[0-9a-zA-Z]{16,}|BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY|AKIA[0-9A-Z]{16}|ghp_[0-9A-Za-z]{30,}' "$STORE_SHA" -- . ':!*package-lock.json' ':!*.lock'; then
  stop "найдены строки, похожие на ключи — проверьте вручную: git grep -nIE 'sk_live_|whsec_|PRIVATE KEY' $STORE_SHA"
fi
echo "$FILES" | grep -qE '^\.gitmodules$' && stop "есть submodules — нужен отдельный план комплектования"
git show "$STORE_SHA:.gitattributes" 2>/dev/null | grep -qi 'filter=lfs' && stop "есть Git LFS — нужен отдельный план"
echo "✓ Секретов, .env, ключей, дампов, LFS и submodules нет (.env.example — только шаблон)."
ask "Загрузить архив именно этого коммита $STORE_SHA (согласован с владельцем)?"

STORE_EXPORT=$(mktemp -d "${TMPDIR:-/tmp}/nap-store-upload.XXXXXX")
git archive --format=tar.gz --output="$STORE_EXPORT/store-$STORE_SHA.tar.gz" "$STORE_SHA"
(cd "$STORE_EXPORT" && shasum -a 256 "store-$STORE_SHA.tar.gz" > "store-$STORE_SHA.tar.gz.sha256")
LOCAL_SUM=$(awk '{print $1}' "$STORE_EXPORT/store-$STORE_SHA.tar.gz.sha256")
cat "$STORE_EXPORT/store-$STORE_SHA.tar.gz.sha256"
ls -l "$STORE_EXPORT"

# ---------------------------------------------------------------- 3. загрузка
say "3. Новая уникальная папка на сервере и загрузка двух файлов"
STORE_REMOTE=$(ssh "${SSH_OPTS[@]}" -o StrictHostKeyChecking=yes "$HOST" 'mktemp -d /srv/nap-store/incoming/upload.XXXXXXXX' | tr -d '\r')
case "$STORE_REMOTE" in /srv/nap-store/incoming/upload.*) ;; *) stop "неожиданный ответ сервера: $STORE_REMOTE";; esac
echo "Папка на сервере: $STORE_REMOTE"
scp -i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o HostKeyAlgorithms=ssh-ed25519 \
  "$STORE_EXPORT/store-$STORE_SHA.tar.gz" "$STORE_EXPORT/store-$STORE_SHA.tar.gz.sha256" "$HOST:$STORE_REMOTE/" \
  || stop "передача не удалась — запустите скрипт снова (будет создана новая папка; старые файлы не трогаются)"

# ---------------------------------------------------------------- 4. проверка
say "4. Проверка на сервере"
REMOTE_CHECK=$(ssh "${SSH_OPTS[@]}" -o StrictHostKeyChecking=yes "$HOST" \
  "cd '$STORE_REMOTE' && ls -l && sha256sum -c 'store-$STORE_SHA.tar.gz.sha256' && sha256sum 'store-$STORE_SHA.tar.gz'")
echo "$REMOTE_CHECK"
REMOTE_SUM=$(echo "$REMOTE_CHECK" | tail -1 | awk '{print $1}')
echo "$REMOTE_CHECK" | grep -q ": OK$" || stop "sha256sum -c на сервере не OK"
[ "$REMOTE_SUM" = "$LOCAL_SUM" ] || stop "SHA-256 на сервере ($REMOTE_SUM) отличается от Mac ($LOCAL_SUM)"

say "ГОТОВО — отчёт владельцу (скопируйте и отправьте)"
cat <<REPORT
NAP Store — исходный архив загружен (не распакован, не установлен, не запущен).
Репозиторий: https://github.com/BrothersB/nap-store
Commit:      $STORE_SHA ($SOURCE), версия ${VERSION:-?}
Проверенный выпуск: $MATCH
Папка:       $STORE_REMOTE
Файлы:       store-$STORE_SHA.tar.gz, store-$STORE_SHA.tar.gz.sha256
SHA-256:     $LOCAL_SUM (Mac) = $REMOTE_SUM (сервер), sha256sum -c: OK
CI:          https://github.com/BrothersB/nap-store/commit/$STORE_SHA (статус проверок коммита) и https://github.com/BrothersB/nap-store/actions
Ограничения: демо-данные и демо-аккаунты (seed) не для production; оплата и доставка в mock-режиме до ключей
             Stripe/ЮKassa/EasyPost; налоги не рассчитываются (цены без НДС); юридические документы версии 1.0
             со статусом «на проверке юристом», реквизиты-заглушки; перед выпуском — повторная сверка hash.
REPORT
echo "Локальная копия архива: $STORE_EXPORT (можно удалить после выпуска)."
