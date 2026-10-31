# Публикация магазина на store.napsystem.com (NAP-085)

Решение владельца от 31.10.2026: магазин работает на поддомене **store.napsystem.com**. Основная платформа (napsystem.ru / napsystem.com, nginx + PHP + MariaDB в `/var/www/nap-trainer`) не меняется.

## Как это устроено

```
браузер ── HTTPS ──► nginx сервера (существующий)
                      └─ server_name store.napsystem.com ──► 127.0.0.1:3100 (контейнер web, Next.js)
                                                              └─► api (FastAPI) ─► PostgreSQL, Redis, воркеры
                                                                  (внутренние сети Docker, наружу не открыты)
```

- Магазин запускается в Docker Compose (проект `nap-store`) в `/srv/nap-store`. У него своя база PostgreSQL, к базе платформы он не подключается.
- Наружу ничего не открыто. Сайт слушает только `127.0.0.1:3100`, сертификат и HTTPS обслуживает nginx.
- Для nginx добавляется **один новый файл** сайта. Default server, корневой `location /` и сайты платформы не трогаются.
- Каждый выпуск — это конкретный commit SHA в своей папке `releases/<sha>`, `current` указывает на рабочий. Данные лежат в томах Docker и переживают выпуски и откаты.

Требования к серверу: Ubuntu 22.04/24.04, Docker Engine 24+ с плагином compose 2.24+, свободно ≥ 3 ГБ RAM (с конвертером) и ≥ 20 ГБ диска.

## Что делает владелец (один раз)

### 1. Доступ к репозиторию

Код магазина переносится в общий репозиторий **BrothersB/nap-trainer** (папка `apps/store`, ветка `codex/store-development`, Pull Request в основную ветку).

Владелец репозитория открывает GitHub → BrothersB/nap-trainer → Settings → Collaborators → Add people и добавляет учётную запись **ma5ter77** с ролью **Write**. Именно она подключена к сессии Claude. Если работа идёт от имени другой учётной записи, нужно назвать её. После этого Claude запросит доступ к репозиторию, и останется подтвердить запрос.

Если репозиторий принадлежит организации BrothersB, то вместо этого администратор организации может установить GitHub-приложение Claude для репозитория nap-trainer.

### 2. DNS

В панели, где управляется DNS домена napsystem.com, добавить запись:

| Тип | Имя | Значение | TTL |
|---|---|---|---|
| A | `store` | IP сервера (по сохранённым данным 217.114.3.160 — подтвердить) | 300 |

Если DNS идёт через CDN (Cloudflare и т. п.), запись создаётся там же. Пока выпускается сертификат, режим лучше поставить «DNS only».

Проверка: `nslookup store.napsystem.com` (или `Resolve-DnsName store.napsystem.com` в PowerShell) возвращает IP сервера.

### 3. SSH-доступ

На своём компьютере (пароль ключа вводится локально, существующие ключи не трогать):

```
ssh-keygen -t ed25519 -f ~/.ssh/nap_store_ed25519 -C "nap-store"
```

Содержимое **публичного** файла `nap_store_ed25519.pub` надо дописать в `~/.ssh/authorized_keys` пользователя на сервере, не заменяя файл. Это делается через консоль хостинга или уже доверенную сессию. Приватный ключ, пароли и `.env` в чат и Telegram не отправляются.

Отпечаток сервера сверяется в консоли хостинга: `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256`. Затем при первом входе:

```
ssh -i ~/.ssh/nap_store_ed25519 -o IdentitiesOnly=yes root@217.114.3.160
```

### 4. Секреты магазина

На сервере (после шага «Подготовка» ниже):

```
mkdir -p /srv/nap-store/shared && cd /srv/nap-store/shared
# шаблон: .env.example из выпуска + ops/cohost/env.cohost.example
nano .env      # NAP_SECRET_KEY и POSTGRES_PASSWORD — `openssl rand -hex 32`, SMTP — когда будет ящик
chmod 600 .env
```

## Порядок выпуска

1. **Проверка сервера, только чтение:** `whoami; hostname; cat /etc/os-release; df -h; free -m; systemctl is-active nginx; ss -ltn; docker --version; ls /etc/nginx/sites-enabled`. Убедиться, что сайта store.napsystem.com ещё нет и порт 3100 свободен (иначе выбрать другой `STORE_WEB_PORT` и поправить файл nginx).
2. **План → «да» владельца.** В плане: SHA, результаты тестов, порт, затрагиваемые файлы (только новые: `/srv/nap-store/**`, `/etc/nginx/sites-available/store.napsystem.com`, ссылка в `sites-enabled`, сертификат), порядок проверки и отката.
3. **Подготовка (после «да»):** установить Docker, если его нет: `curl -fsSL https://get.docker.com | sh`. Это ставит только Docker, PHP/MariaDB/nginx не меняются.
4. **Выкладка ровно согласованного коммита** (не `git pull` в рабочей папке):
   ```
   # на своём компьютере или в CI
   git archive --format=tar.gz -o store-<sha>.tar.gz <sha> apps/store
   scp -i ~/.ssh/nap_store_ed25519 store-<sha>.tar.gz root@217.114.3.160:/srv/nap-store/
   # на сервере
   bash /srv/nap-store/releases/<sha>/apps/store/ops/cohost/deploy-cohost.sh /srv/nap-store/store-<sha>.tar.gz <sha>
   ```
   При первом выпуске скрипт ещё не лежит на сервере: распакуйте архив во временную папку и запустите `ops/cohost/deploy-cohost.sh` оттуда.
5. **nginx:**
   ```
   cp /srv/nap-store/current/ops/cohost/store.napsystem.com.nginx.conf /etc/nginx/sites-available/store.napsystem.com
   ln -s /etc/nginx/sites-available/store.napsystem.com /etc/nginx/sites-enabled/
   nginx -t && systemctl reload nginx
   certbot --nginx -d store.napsystem.com
   ```
   `nginx -t` обязателен перед reload. При ошибке ничего не перезагружать, а удалить ссылку.
6. **Проверка:**
   - https://store.napsystem.com/ru и /en открываются;
   - прямое открытие и обновление вложенных страниц (`/ru/apps`, `/ru/devices/...`);
   - вход и регистрация;
   - загрузка и конвертация трека;
   - тестовая покупка;
   - письмо уведомления;
   - `/api/v1/ready` на `127.0.0.1:3100` отвечает `ready: true`;
   - сайты платформы на napsystem.ru и napsystem.com работают как раньше.
7. **Запись о выпуске:** скопировать `docs/deployments/TEMPLATE.md` в `docs/deployments/<дата>-<sha>.md`, заполнить без секретов, закоммитить в общий репозиторий.

## Откат

- **Код:** `bash /srv/nap-store/current/ops/cohost/rollback-cohost.sh` возвращает предыдущий выпуск. Данные сохраняются.
- **База,** если новый выпуск успел применить миграцию, с которой старый код не работает: восстановить последний бэкап из `/srv/nap-store/shared/backups/` по `docs/operations.md` (раздел «Резервное копирование и восстановление»).
- **Полностью убрать магазин с поддомена:**
  ```
  rm /etc/nginx/sites-enabled/store.napsystem.com && nginx -t && systemctl reload nginx
  cd /srv/nap-store/current && docker compose -p nap-store down   # тома с данными остаются
  ```

## Что не делать

- Не копировать магазин в `/var/www/nap-trainer/public/store`.
- Не подключать магазин к базе платформы.
- Не импортировать `database/schema.sql`.
- Не менять default server и корневой `location /` основного сайта.
- Не хранить токен GitHub в адресе remote.
- Не запускать `git pull` в рабочей папке выпуска.
