# NAP Store

Маркетплейс по модели App Store для нейроакустической платформы NAP SYSTEM: приложения с настоящим бинауральным конвертером, подписки и распределение дохода 30/70, магазин оборудования с комплектами, складом и международной доставкой, кабинеты разработчика/поставщика/модератора/администратора, SDK/API и симулятор кресла. Интерфейс на русском и английском.

> Demo-режим работает без внешних секретов: платежи, доставка, кресло и измерения — явно помеченные симуляторы. Технические проверки подтверждают параметры сигнала, а не состояние мозга.

## Быстрый старт (Docker)

```bash
cp .env.example .env
docker compose up --build
# магазин      http://localhost:3000   (редирект на /ru или /en по языку браузера)
# API/OpenAPI  http://localhost:8000/api/v1/docs
```

Сервис `migrate` применяет миграции и загружает demo: синтезирует аудио и прогоняет его через настоящий конвертер. Первый запуск занимает около минуты.

## Запуск без Docker

Нужны PostgreSQL 16, Redis 7, FFmpeg с soxr, Python 3.12+ и [uv](https://docs.astral.sh/uv/), Node.js 22.

```bash
# backend
cd backend
uv sync --frozen
export NAP_DATABASE_URL=postgresql+psycopg://nap:nap@localhost:5432/nap NAP_SECRET_KEY=local-secret
uv run alembic upgrade head
uv run python -m app.seed
uv run uvicorn app.main:app --port 8000
uv run celery -A app.worker.celery worker -B -Q media,ops --concurrency 2     # отдельный терминал
# (без Redis: NAP_RUN_JOBS_INLINE=true — конвертация в фоновом потоке API)

# frontend
cd frontend
npm ci && npm run build
BACKEND_URL=http://127.0.0.1:8000 npm start                                     # http://localhost:3000
```

## Demo-аккаунты (пароль `demo-password-1`)

| E-mail | Роли |
|---|---|
| `user@nap.demo` / `buyer.en@nap.demo` | покупатель (RU / EN); у первого есть заказ с двумя посылками |
| `dev1@nap.demo`, `dev2@nap.demo` | разработчики (изоляция друг от друга) |
| `moderator@nap.demo` | модератор |
| `admin@nap.demo` | администратор + модератор |
| `seller@nap.demo` | продавец-оператор (собственная продажа, demo) |
| `fulfillment@nap.demo` | оператор исполнения того же продавца |
| `seller2@nap.demo` | второй поставщик (проверка изоляции) |

Demo-каталог: опубликованные приложения разных категорий и моделей цены (разовая, подписка, бесплатное с аудио на английском), версии на модерации, отклонённая и черновик; оборудование, комплект из компонентов, готовый комплект, последний экземпляр (виброакустический мат), товар под заказ (Chair Pro), крупногабаритное кресло с индивидуальной доставкой, заказ с двумя посылками (одна доставлена, одна в пути).

## Demo-сценарии

1. **Автор → публикация.** `dev1@nap.demo` → «Кабинет разработчика» → «Новое приложение» → «Аудио и конвертер»: загрузите файл → «Конвертировать» → сравните исходник и результат, QA-отчёт → «Описание» (права) → «Программа» → «Цена» → «Отправить на модерацию». `moderator@nap.demo` → «Модерация» → «Одобрить». Автор → «Опубликовать».
2. **Покупка и сеанс.** Покупатель → приложение → «Купить» → тестовая оплата → «Библиотека» → «Слушать» → симулятор кресла или наушники + L/R-тест → анкета → сеанс → анкета → разница.
3. **Подписка.** «Курс фокуса» → подписка → в профиле «Симулировать продление», «Симулировать неудачное продление», «Отключить автопродление».
4. **Оборудование.** Карточка приложения → «Подобрать оборудование» → комплект → отметьте имеющееся → «Добавить недостающее» → корзина (разные продавцы → отдельные заказы) → адрес → расчёт доставки → оплата → `seller@nap.demo`: принять, упаковать, этикетка, передать перевозчику → покупатель видит tracking. Кресло: «Запросить расчёт доставки» → продавец отправляет предложение → покупатель принимает → оплата.
5. **Оценки и отзывы.** Покупатель с бесплатным приложением («Ночные истории» → «Получить бесплатно») ставит звёзды и пишет отзыв на странице приложения. `dev2@nap.demo` → «Кабинет разработчика» → «Отзывы» → ответ. Отзыв со ссылкой или контактами уходит модератору: `moderator@nap.demo` → «Модерация» → «Отзывы и жалобы».
6. **Возвраты/деньги.** Админ → «Финансы»: выплата разработчику, возврат цифрового заказа (доля пересчитывается). Продавец: возврат товара и денежный возврат строками.

## Тесты и проверки

```bash
cd backend && uv run pytest                      # 92 тестов: DSP, задания, права, деньги, webhooks, склад, доставка, сеансы, i18n, S3
cd frontend && npm run check-i18n && npm run check-patterns && npx tsc --noEmit
cd frontend && node e2e/flows.cjs                # браузерные сквозные сценарии RU и EN (нужен Playwright)
cd sdk && npm run build && NAP_API_KEY=napk_… npm run example
ops/backup.sh … && ops/restore_verify.sh …       # резервная копия и проверка восстановления
cd backend && uv run python scripts/benchmark.py 10 60
```

## Документация

- [Архитектура и решения](docs/architecture.md) · [Конвертер (DSP)](docs/dsp.md) · [Биллинг](docs/billing.md) · [Оборудование, склад, доставка](docs/hardware-commerce.md) · [DeviceAdapter и сеансы](docs/device-adapter.md) · [RU/EN](docs/i18n.md) · [Эксплуатация, бэкап, benchmark](docs/operations.md)
- [Отчёт по критериям приёмки](docs/acceptance-report.md) · [Production-зависимости](docs/production-dependencies.md)
- OpenAPI: `docs/api/openapi.json` (живой — `/api/v1/openapi.json`) · JSON Schema manifest: `docs/schemas/manifest.v1.json` · QA-отчёты demo: `docs/qa/`
- Инструкции: [RU](docs/guide.ru.md) · [EN](docs/guide.en.md) · SDK: `sdk/` (пример `sdk/examples/create-draft-and-convert.ts`)

## English summary

NAP Store is an App Store–style marketplace for the NAP SYSTEM neuroacoustic platform. Authors upload audio, a real two-carrier binaural converter (FFmpeg + NumPy, verified by automated QA) prepares it, and programs go through moderation and get published. Buyers purchase or subscribe (30/70 split, immutable double-entry ledger, idempotent webhooks), run sessions on a simulated chair or headphones, and buy equipment with kit building, stock reservation, international shipping and supplier fulfillment. Run with `docker compose up --build`; demo accounts and scenarios are listed above. Everything works offline from external providers via clearly labelled simulators; Stripe and EasyPost test adapters are implemented with contract tests, and their live sandbox runs are reported as *not run* without keys.
