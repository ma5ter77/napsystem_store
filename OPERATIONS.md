# Эксплуатация сервера магазина (runbook)

Сервер `nap-store-arm1` (2.31.171.3), сайт https://store.napsystem.com. Схема — [ARCHITECTURE.md](ARCHITECTURE.md).
Всё обычное делается из GitHub без SSH. SSH нужен только для настроек и аварий.

## Обычные действия — GitHub → Actions → store-ops → Run workflow

| op | Что делает | Когда |
|---|---|---|
| `status` | текущий и предыдущий выпуск, состояние сервисов, свободный диск и память | в любой момент |
| `logs` + сервис | последние 200 строк сервиса (секреты вырезаются) | разбор ошибки |
| `restart` | перезапуск сервисов текущего выпуска | после правки `shared/.env` |
| `rollback` | вернуть предыдущий выпуск (схема БД не меняется) | после неудачного выпуска |
| `backup` | цикл резервного копирования сейчас | перед ручными изменениями |

Выложить конкретный коммит: Actions → **deliver-vps** → Run workflow → `sha` (коммит должен иметь зелёный CI).

## Ручной доступ (SSH)

```bash
ssh root@2.31.171.3
# команда compose текущего выпуска (дальше — $DC)
CUR=$(python3 -c 'import json;print(json.load(open("/srv/nap-store/state/state.json"))["current"]["sha"])')
DC="docker compose -p nap-store --project-directory /srv/nap-store/releases/$CUR/ops/vps \
  --env-file /srv/nap-store/shared/.env --env-file /srv/nap-store/releases/$CUR/release.env \
  -f /srv/nap-store/releases/$CUR/ops/vps/docker-compose.prod.yml"
$DC ps                       # сервисы
$DC logs --tail 100 api      # логи
df -h / ; free -m            # диск и память
cat /srv/nap-store/backups/last-status.json   # результат последнего бэкапа
```

Настройки приложения: `sudo -u napdeploy nano /srv/nap-store/shared/.env`, затем store-ops → `restart`.

## Резервные копии

| Что | Когда | Сколько хранится | Где |
|---|---|---|---|
| Дамп БД (`nap-*.dump` + `.sha256`) | ежедневно в `BACKUP_AT` (23:30 UTC) | `BACKUP_KEEP_DAYS` = 14 дней | `/srv/nap-store/backups` |
| Проверка дампа | сразу после дампа: восстановление в отдельную БД, сверка числа строк, баланса журнала, триггеров неизменяемости | — | результат в `last-status.json` и метрике `nap_backup_age_seconds` |
| Архив файлов (`storage-*.tar`) | вместе с дампом | `BACKUP_STORAGE_KEEP_DAYS` = 2 дня (каждый архив — полная копия всех файлов) | там же |
| Дамп перед выкладкой (`pre-deploy-*.dump`) | перед каждой миграцией | последние 5 | там же |
| Снимок всего сервера (Hetzner Backups) | ежедневно, если включено в консоли | 7 | у Hetzner, вне сервера |

Если архив файлов оставил бы на диске меньше `BACKUP_MIN_FREE_PCT` (10%), он пропускается и цикл помечается
`failed`, так что `last-status.json` объясняет почему. Дамп БД при этом сделан.

## Восстановление

**1. Вернуть базу к дампу на сервере** (например, после ошибочной операции):
```bash
# сначала свежая копия текущего состояния: store-ops → backup
ls -lt /srv/nap-store/backups/*.dump | head          # выбрать файл
F=/srv/nap-store/backups/nap-YYYYMMDDTHHMMSSZ.dump
sha256sum -c "$F.sha256"
$DC stop api worker-media worker-ops web
$DC exec -T postgres psql -U nap -d postgres -c "DROP DATABASE nap WITH (FORCE)" -c "CREATE DATABASE nap OWNER nap"
$DC exec -T postgres pg_restore -U nap -d nap --no-owner < "$F"
$DC start api worker-media worker-ops web
curl -s https://store.napsystem.com/api/v1/ready
```
Этот шаг стирает данные после момента дампа, поэтому выполняется только с согласия владельца.

**2. Вернуть весь сервер:** Hetzner Console → сервер → Backups → нужный снимок → Restore. Сервер
перезагрузится в состоянии на момент снимка.

**3. Сервер потерян целиком.** Новый CAX21 → установщик (docs/vps-deploy.md) → новые значения
`STORE_DEPLOY_HOST` / `STORE_DEPLOY_HOST_KEY` / `STORE_DEPLOY_SSH_KEY` в GitHub → deliver-vps → восстановить
дамп (шаг 1) и архив файлов в том `storage` → DNS `store` на новый IP. **Сейчас это возможно только из
снимка Hetzner Backups**: внешнего хранилища для дампов ещё нет (см. ниже).

## Аварии: что проверить

| Симптом | Проверка | Действие |
|---|---|---|
| Сайт не открывается | `curl -sI https://store.napsystem.com`; store-ops → `status` | сервис упал → `restart`; сервер не отвечает → Hetzner Console (питание, консоль) |
| `/api/v1/ready` → 503 | в ответе видно, что не так: database / redis / storage | `logs postgres` / `redis`; при нехватке места — раздел «Диск» |
| Выкладка `failed_rolled_back` | отчёт deliver-vps: ошибка и шаг | сайт уже на предыдущем выпуске; исправить в PR |
| Сертификат не выдаётся | `logs caddy`; DNS `store` указывает на 2.31.171.3 | Caddy повторяет сам, порты 80/443 должны быть открыты |
| Диск заполняется | `status` → `disk_free_gib`; `du -sh /srv/nap-store/backups /var/lib/docker` | уменьшить `BACKUP_KEEP_DAYS`, увеличить диск (Rescale) или подключить хранилище |
| Бэкап `failed` | `cat /srv/nap-store/backups/last-status.json` | причина в поле `message` |

## Что ещё не сделано (NAP-097)

1. **Бэкапы вне сервера**: Hetzner Storage Box BX11 (1 ТБ, €3,20/мес) или S3. Нужен заказ владельца.
   После этого — восстановление с нуля на чистом сервере как проверка.
2. **Внешний мониторинг**: UptimeRobot (бесплатно) проверяет `/api/v1/ready` каждые 5 минут и оповещает
   по почте или в Telegram. Аккаунт заводит владелец.
3. **Cloudflare** перед сервером (DNS на Cloudflare, режим Full strict): нужен перенос DNS-зоны — решение владельца.
4. **Нагрузочный прогон** на этом сервере.
5. **Боевой режим**: см. README_DEPLOY.md, «Переход из демо».
