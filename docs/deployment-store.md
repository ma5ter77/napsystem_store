# store.napsystem.com — репозиторий, адрес и будущий выпуск (NAP-085)

Основание — инструкция владельца «NAP SYSTEM — разделение платформы и Store» от 31.10.2026. Она заменяет прежний план «один репозиторий + адрес /store».

| | Платформа | Магазин (этот проект) |
|---|---|---|
| Репозиторий | BrothersB/nap-trainer (владелец BrothersB) | **BrothersB/nap-store** (приватный, владелец BrothersB, разработчик ma5ter77) |
| Адрес | napsystem.ru (документы); napsystem.com — подтвердить | **https://store.napsystem.com**, приложение в корне `/` |
| Стек | PHP 8.3 + MariaDB + nginx | FastAPI + PostgreSQL 16 + Redis + Celery + Next.js 15, Docker Compose |

> Целевой адрес — это не подтверждение DNS, TLS или работающего сайта. Production, DNS, nginx, службы, базы данных и SSH-доступы на этом этапе **не меняются**. Всё в `ops/cohost/` — неактивный пример. Первая установка на сервер, DNS, TLS и первый выпуск требуют отдельного разрешения владельца.

## 1. Перенос кода в BrothersB/nap-store

Исходный Git магазина уже есть — это вариант A инструкции: история переносится как есть, без переписывания и без squash.

- Источник: репозиторий магазина в рабочей среде Claude. Ветка `master`, 15 коммитов, теги `v0.1.0`…`v0.7.1`, автор всех коммитов — NAP Store. Копия на Mac (`~/Projects/nap-store`) содержит ту же историю до `v0.6.1`, а вложенная папка `nap-trainer/` в ней — это клон платформы. Он **не переносится**.
- Резерв: `git bundle` всей истории (`--all`, проверен `git bundle verify`) со списком refs и SHA-256 лежит вне рабочих деревьев на Mac, в `~/Projects/_nap-store-backup-20261031/`. Bundle не содержит `.env` и данных Docker — они остаются только локально.
- Проверка всей истории: секретов (ключи Stripe/EasyPost/AWS/GitHub, приватные ключи, токены), файлов `.env`, дампов, PDF, архивов и LFS-объектов нет. Крупнейший файл — `backend/uv.lock`, 251 КБ. В одном старом коммите лежит служебный файл планировщика `backend/celerybeat-schedule-wal` на 140 КБ, без секретов; в текущем дереве его нет.
- Публикуются явно перечисленные refs, после показа владельцу, в пустой репозиторий:
  ```
  git ls-remote https://github.com/BrothersB/nap-store.git          # должен быть пуст
  git remote add store-origin https://github.com/BrothersB/nap-store.git   # имя проверено, не занято
  git push --dry-run store-origin refs/heads/master:refs/heads/master
  git push store-origin refs/heads/master:refs/heads/master
  git push store-origin refs/heads/codex/store-napsystem-com:refs/heads/codex/store-napsystem-com
  for t in v0.1.0 v0.1.1 v0.2.0 v0.2.1 v0.2.2 v0.2.3 v0.3.0 v0.4.0 v0.5.0 v0.6.0 v0.6.1 v0.7.0 v0.7.1; do
    git push store-origin refs/tags/$t:refs/tags/$t; done
  ```
  Default branch — `master`, как в исходном репозитории. Переименование в `main` возможно только по согласованию.
- После push: сверить локальные и remote SHA (`git ls-remote store-origin`), сделать чистое контрольное клонирование в отдельный каталог, сравнить историю и хеши файлов, прогнать проверки.

## 2. Адаптация к store.napsystem.com (ветка `codex/store-napsystem-com`)

- Базовый адрес задаётся окружением, в коде не зашит: `NAP_PUBLIC_BASE_URL=https://store.napsystem.com` (ссылки в письмах, возвраты оплаты) и `PUBLIC_BASE_URL` (canonical, hreflang, sitemap). Приложение работает в корне `/`, вложенные маршруты и ассеты абсолютные от корня.
- Cookies host-only: `Domain` не задаётся, на `.napsystem.com` не расширяются. Cookies сессии и CSRF — `Secure`, `HttpOnly` у сессии, `SameSite=Lax`.
- CORS не используется: браузер обращается к API через тот же адрес (Next.js проксирует `/api/v1` на внутренний API). Wildcard-origin с cookies нет.
- Оплаты — только тестовый режим (`NAP_PAYMENT_PROVIDER=mock`, или Stripe с тестовыми ключами). Кабинеты провайдеров не меняются, приватных ключей в браузерном коде нет.
- Неактивный пример выпуска для сервера, где уже работает nginx платформы, — `ops/cohost/`:
  - `docker-compose.cohost.yml` — магазин слушает только `127.0.0.1:${STORE_WEB_PORT}`, по умолчанию 3100. API и базы остаются во внутренних сетях Docker, у магазина своя PostgreSQL;
  - `store.napsystem.com.nginx.conf` — отдельный файл сайта. Default server и сайты платформы не трогаются. Порт и раскладку nginx ещё нужно проверить на сервере, они не считаются известными;
  - `deploy-cohost.sh` / `rollback-cohost.sh` — выкладка ровно одного проверенного коммита в `releases/<sha>` от отдельного пользователя, не от общего root, с журналом выпусков и откатом;
  - `env.cohost.example` — имена переменных без значений.

## 3. Интеграция с платформой

Вынесена в отдельный план, `docs/integration-platform.md`. До его согласования магазин не подключается к базе платформы, не копирует её пользователей и не делает общий вход.

## 4. Будущий выпуск — только после разрешения владельца

Порядок, когда будет разрешение:
1. Проверка сервера, только чтение: ОС, диск, память, `nginx`, занятые порты, наличие Docker, раскладка сайтов nginx.
2. План: SHA, результаты проверок, порт, затрагиваемые файлы (только новые), откат.
3. После «да» владельца:
   - отдельный пользователь для выпусков;
   - Docker, если его нет;
   - `.env` на сервере заполняет владелец;
   - выкладка `git archive` проверенного SHA через `deploy-cohost.sh`;
   - отдельный сайт nginx с `nginx -t` до reload;
   - сертификат для store.napsystem.com;
   - DNS-запись `store` → IP сервера (владелец, в панели DNS).
4. Проверки:
   - HTTPS, `/ru` и `/en`;
   - прямое открытие и обновление вложенных страниц;
   - вход, загрузка и конвертация, тестовая оплата, письма;
   - сайты платформы работают как раньше.
5. Запись о выпуске (`docs/deployments/TEMPLATE.md`) — в репозиторий, без секретов.

Откат: `rollback-cohost.sh` для кода; для схемы БД — бэкап до выпуска (`docs/operations.md`); полностью — убрать ссылку сайта nginx, `nginx -t`, reload, `docker compose -p nap-store down` (тома с данными остаются).

Запрещено: копировать магазин в каталоги платформы, `git pull` в live-каталоге, перенос всего дерева через scp, подключение к БД платформы, общий root для повседневных выпусков, force push, `--mirror`/`--all` без проверки.
