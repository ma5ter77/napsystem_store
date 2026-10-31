# Выпуск магазина — <ГГГГ-ММ-ДД>

Без секретов, паролей, содержимого .env и логов с персональными данными.

| Поле | Значение |
|---|---|
| Адрес | https://store.napsystem.com |
| Репозиторий / ветка | BrothersB/nap-trainer / codex/store-development (или основная после merge) |
| Commit SHA | |
| Версия магазина (release.json) | |
| Время выпуска (UTC) | |
| Кто выпустил | |
| Папка выпуска | /srv/nap-store/releases/<sha>/apps/store |
| Предыдущий выпуск | |
| Порт | 127.0.0.1:3100 |
| Изменённые файлы сервера | /etc/nginx/sites-available/store.napsystem.com (+ ссылка в sites-enabled), сертификат store.napsystem.com |
| Бэкап до выпуска | имя файла в /srv/nap-store/shared/backups |

## Проверки перед выпуском
- [ ] backend pytest
- [ ] frontend check-i18n / typecheck / build
- [ ] browser E2E RU/EN

## Проверки после выпуска
- [ ] https://store.napsystem.com/ru и /en
- [ ] прямое открытие и обновление вложенных страниц
- [ ] вход / регистрация
- [ ] загрузка и конвертация
- [ ] тестовая покупка
- [ ] письмо уведомления
- [ ] /api/v1/ready = true
- [ ] сайты платформы работают как раньше

## Откат
`bash /srv/nap-store/current/ops/cohost/rollback-cohost.sh`; при несовместимой миграции — восстановление бэкапа (docs/operations.md).
